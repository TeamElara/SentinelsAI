# 52 — Subdomain Security agent

> **Status:** done. Sentinels now runs 8 agents. A live scan of `example.com`
> now scores **52/F**, down from 54 — not a bug: the new agent found
> `www.example.com` also missing HSTS/CSP, which V3's alias table correctly
> recognizes as the *same* underlying problem as the apex's own missing-HSTS
> finding, just decayed to 50% weight as the second sighting. `pytest
> backend/tests -q` → 47 passed (12 new).

## What we built

An 8th agent, `subdomain`, that answers a question none of the other seven
ask: **what else does this domain expose besides the site you typed in?**
It finds other live hosts under the same domain — `api.`, `staging.`,
`blog.`, whatever's actually there — three different ways (the main site's
own TLS certificate lists every hostname it covers; a public log called
Certificate Transparency records every certificate ever issued for the
domain; and a short list of common names like `dev`/`admin`/`test` gets
checked against real DNS). A name only makes it into the report if DNS
actually resolves it — "commonly used, therefore probably exists" is never
good enough on its own.

For the ones it finds, it checks the same kind of things the other agents
check for the main site — HSTS, CSP, a valid TLS certificate — but per
*subdomain*, plus one thing nothing else in Sentinels does: whether a
subdomain's DNS record points at something that no longer exists (a
"dangling" record), which is how real subdomain takeovers happen.

## The one new idea: testing code that talks to DNS and the network

Every other agent's tests use `mock_site` — a fake HTTP server built from a
`{path: response}` dictionary. That works because `httpx` (the HTTP
library) lets you swap out its actual network layer for a fake one
(`MockTransport`) and nothing else about the code has to change.

DNS doesn't have an equivalent. `dns.resolver.resolve("dev.example.com",
"A")` either asks a real DNS server or it doesn't — there's no
`MockTransport` for it. So this agent's DNS-and-certificate logic lives in
small, separate functions instead of being written inline:

```python
def _resolve(hostname: str) -> tuple[str, str] | None:
    """Blocking: CNAME first, then A, then AAAA."""
    resolver = _make_resolver()
    ...
```

And the agent calls `_resolve` by its plain name, not as `self._resolve` or
anything tied to the class. That single choice is what makes it swappable:
in a test, `monkeypatch.setattr(subdomain, "_resolve", fake_resolve)`
replaces the module's `_resolve` with a fake one for the duration of one
test, and every place the agent calls `_resolve(...)` — however deep in the
call stack — gets the fake instead, with zero changes to the agent code
itself.

Standalone example — nothing here is about DNS, just the mechanism:

```python
# weather.py
def get_temperature(city: str) -> float:
    # in real life: calls a weather API over the network
    raise NotImplementedError("no network in tests!")

def describe_weather(city: str) -> str:
    temp = get_temperature(city)          # note: calls the plain function name
    return "hot" if temp > 30 else "mild"
```

```python
# test_weather.py
import weather

def test_describe_weather_hot(monkeypatch):
    monkeypatch.setattr(weather, "get_temperature", lambda city: 35.0)
    assert weather.describe_weather("Delhi") == "hot"
```

`describe_weather` never imports or calls anything test-specific — it just
calls `get_temperature` by name, the same way it always did.
`monkeypatch.setattr` reaches into the *module* and swaps that one name for
the test's duration, then puts the real one back automatically when the
test ends (that's what makes `monkeypatch` safer than editing the module
by hand — nothing leaks into the next test). This is exactly
`test_subdomain.py`'s pattern for `_resolve`, `_target_resolves`,
`_query_ct_logs`, and `fetch_certificate` — four different pieces of
real-network code, none of which ever runs during `pytest`.

## The actual code

**Reusing one handshake for two jobs.** `tls.py` already opens a real TLS
connection to read the apex's certificate. This agent needed that same
capability for two things — reading the apex cert's list of covered
hostnames (SANs), and separately checking whether *each discovered
subdomain's own* certificate is valid — so `tls.py`'s handshake function
was renamed from `_fetch_certificate` to `fetch_certificate` (dropping the
underscore that means "private to this file") and imported directly:

```python
from agents.tls import fetch_certificate
...
cert, _ = await asyncio.to_thread(fetch_certificate, apex, 443, CT_TIMEOUT)
```

One implementation, two callers, TLS agent's own behavior completely
unchanged.

**Being honest about "why is this here".** The riskiest thing this agent
could get wrong is crying "takeover!" over something ordinary — a CNAME
pointing at `github.io` is how millions of legitimate sites work. So the
decision is a strict three-way split, matching PLAN-v4's table exactly:

```python
if not target_resolves:
    # the CNAME points at a name that no longer exists at all
    return Finding(id="subdomain-dangling-dns", severity=Severity.MEDIUM,
                    confidence=0.6, title="Potential dangling DNS record...")

provider = match_provider(target)
if provider is None or response is None:
    return None   # points somewhere ordinary, or we never fetched it — say nothing

if provider["fingerprint"].lower() not in response.text.lower():
    return None   # points at a known provider, but it's serving a real site — say nothing

return Finding(id="subdomain-takeover-potential", severity=Severity.HIGH,
                confidence=0.9, title="Potential subdomain takeover — verify manually")
```

Two different findings, two different confidence numbers, and the word
"potential" in both titles — because confirming either one for real means
attempting to register the resource yourself, which is exploitation, not
scanning.

**Budget, made concrete.** Three sources feed one shared discovery loop,
capped at 25 total hosts; only the top 10 (closest to the apex, CNAMEs
first, then alphabetical — so the same domain always yields the same 10)
get the expensive follow-up: one HTTPS attempt, one HTTP fallback if that
failed, one TLS handshake. DNS lookups and HTTP requests are tracked
against two separate `Budget` objects (`agents/probe.py`, from V2) so a
domain with hundreds of real subdomains still finishes in the same ≤15s
every other agent's budget aims for.

## Try it

- `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_subdomain.py -v`
  — 12 tests: the three-outcome takeover table, plain-HTTP detection, an
  invalid per-subdomain certificate, and a check that crt.sh failing
  outright still leaves the other two discovery sources working.
- Open `test_cname_to_dead_target_is_dangling_medium` and change
  `target_resolves={"ghost-bucket.s3.amazonaws.com": False}` to `True` —
  rerun it and watch the dangling finding disappear, since a live target
  is no longer suspicious at all.
- Run a live scan of a real domain you know has subdomains and look at
  `report.subdomains` — that's the structured inventory, independent of
  the findings list, that V8 will eventually render as a table.

## Words worth knowing

- **Monkeypatching** — swapping out one function or attribute for a fake
  one, only for the duration of a test, so code that talks to the real
  world (DNS, the network, the clock) can still be tested without it.
- **CNAME record** — a DNS record that says "this name is really just an
  alias for that other name" — following the chain to the *other* name is
  what "resolving" a CNAME means.
- **Dangling DNS** — a DNS record (usually a CNAME) still pointing at a
  resource that's been deleted or deprovisioned. The record itself isn't
  dangerous; the danger is that someone else can sometimes claim the
  now-empty resource and have this domain's DNS record point straight at
  their content.
- **Certificate Transparency (CT) log** — a public, append-only record
  every certificate authority must publish to when it issues a
  certificate. Searchable by anyone (`crt.sh` is one such search engine),
  and reading it is a passive lookup — no traffic to the actual target.

---

**Next:** V7 — checklist rules + AI report integration.
