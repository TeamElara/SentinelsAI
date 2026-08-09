# 50 — API Security agent

> **Status:** done. Sentinels now runs 6 agents. A live scan of `example.com`
> still scores exactly 54/F — this agent found no public API, so it added
> nothing but one clean PASS. `pytest backend/tests -q` → 19 passed.

## What we built

A 6th agent, `api-security`, that pokes at 11 common API-shaped paths
(`/api`, `/swagger.json`, `/graphql`, ...) and, only for the ones that
actually answer, checks five things: is there public API documentation, is
CORS wide open, does a response leak internal details, does an endpoint hand
back data with no login at all, and does the server advertise risky HTTP
methods like PUT/DELETE. It never sends a write request and never invokes
those risky methods — it only *reads* what the server says it would allow.

## The one new idea: spend a budget, then stop honestly

V2 built a `Budget` class (a request counter + a timer) but nothing used it
yet. This agent is its first real customer, and it changes how you have to
think about "did the check work?" — the answer is no longer just yes/no,
it's yes / no / *ran out of time before finishing*.

Standalone example — the same idea with a much smaller thing than HTTP:

```python
class Budget:
    def __init__(self, max_tries):
        self.max_tries = max_tries
        self.used = 0
        self.partial = False

    def allow(self):
        if self.used >= self.max_tries:
            self.partial = True
            return False
        self.used += 1
        return True

budget = Budget(3)
results = []
for name in ["apple", "banana", "cherry", "date", "elderberry"]:
    if not budget.allow():
        break
    results.append(name)

print(results)          # ['apple', 'banana', 'cherry']
print(budget.partial)   # True — we know we stopped early, not that the list ended
```

Without `budget.partial`, the caller can't tell "there were only 3 fruits"
from "there were 50 fruits and we gave up after 3." The flag is what makes
the second case honest instead of silently looking like the first.

Sentinels' agent does exactly this: it calls `budget.allow()` before every
HEAD, every follow-up GET, the CORS probe, and the OPTIONS probe. If a
pathological site 200s on every single path (11 HEAD + up to 11 GET = 22
requests, over the 16-request cap), the loop stops partway and the agent
adds one extra finding — `api-scan-partial` — saying so, instead of quietly
reporting "nothing found" as if that were the real answer.

## The actual code

Discovery, HEAD before GET, budget-gated and robots-gated:

```python
for path in DISCOVERY_PATHS:
    if not robots.allowed(path):
        continue
    if not budget.allow():
        break
    url = urljoin(context.url, path)
    head_response = await safe_head(context, url)
    if head_response is None or head_response.status_code != 200:
        continue
    get_response = await safe_get(context, url) if budget.allow() else None
    discovered.append({"path": path, "url": url, "get": get_response})
```

HEAD first because it's cheaper (no body downloaded) and most dead paths
answer non-200 to it — a GET is only worth the extra request once HEAD says
"something's actually here." Then, like `exposure.py`'s `.env` check, a 200
still isn't trusted on its own: `/openapi.json` only becomes a finding if
`json.loads()` succeeds *and* the parsed object has an `openapi` or
`swagger` key — a soft-404 page that happens to return 200 for every path
won't accidentally look like a real spec.

The CORS check is the one place this agent goes around the shared response
cache on purpose:

```python
response = await context.client.get(
    target, headers={"Origin": _CORS_PROBE_ORIGIN}, follow_redirects=True, timeout=5.0
)
```

The cache (from V2) keys on `(method, url, follow_redirects)` — it doesn't
know about headers. Reusing it here could hand back an earlier response that
never carried our `Origin` header at all, silently breaking the one check
that depends on it.

## Try it

- `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_api_security.py -v`
  — each test builds a tiny fake site and checks one behavior: a real
  `openapi.json` → one finding; a site that 200s everything with plain HTML
  → no security findings (soft-404 discipline holds); permissive CORS +
  credentials → High severity; a credential-shaped string never appears in
  the finding's evidence text, only its count.
- Open `test_soft_404_html_everywhere_yields_zero_findings` and change
  `MAX_REQUESTS` in `api_security.py` from 16 to 30 — rerun the test and
  watch `api-scan-partial` disappear from the results, since the budget no
  longer runs out.
- Scan a real site with a public Swagger UI (pick one you know of) and open
  its `/scan/<id>/agents/api-security` page once the frontend catches up
  (V8) — for now, check the raw JSON response instead.

## Words worth knowing

- **Budget** — a hard cap on requests and time, with a `partial` flag so
  "stopped early" is never confused with "found nothing."
- **HEAD-first discovery** — check cheaply (HEAD, no body) before spending a
  full request (GET) on inspecting content.
- **Soft-404 discipline** — never trust a 200 status code alone; require the
  *content* to actually match what a real finding claims it is.

---

**Next:** V5 — the Misconfiguration agent.
