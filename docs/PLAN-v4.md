# Sentinels v4 — Attack Surface: API, Subdomains, Misconfiguration

> **Status:** written 2026-08-09, on branch `v4-attack-surface-agents`.
>
> Same discipline as [`PLAN-v2.md`](PLAN-v2.md) and [`PLAN-v3.md`](PLAN-v3.md):
> small, independently verifiable milestones; **do not start N+1 until N passes
> its verification**; one short learning note per milestone
> (`docs/learning/`), per [`CLAUDE.md`](../CLAUDE.md).
>
> V1 done, 2026-08-09. Slugs confirmed as planned; crt.sh confirmed in. Note:
> [`learning/47-affected-url-and-confidence.md`](learning/47-affected-url-and-confidence.md).
>
> V2 done, 2026-08-09. `ResponseCache`/`RobotsGate`/`Budget`/`safe_get`+co.
> live in `backend/agents/probe.py`; `ScanContext` gained `cache`/`shared`,
> both defaulted — a live scan of `example.com` still scores exactly 54/F.
> Note: [`learning/48-shared-probe-layer.md`](learning/48-shared-probe-layer.md).
>
> V3 done, 2026-08-09. `calculate_score` gained dedup by `(base_id, host)`,
> an `ALIASES` table, repeat decay, and a 20-point cap per new agent; test
> infra (`pytest`, `conftest.py`'s `mock_site`) bootstrapped, 11 tests green.
> A live scan of `example.com` still scores exactly 54/F. Note:
> [`learning/49-scoring-dedup.md`](learning/49-scoring-dedup.md).
>
> V4 done, 2026-08-09. `backend/agents/api_security.py` (new) registered as
> the 6th agent (`ApiSecurityAgent`, slug `api-security`) — HEAD-first,
> budget/robots-gated discovery over the 11 planned paths; checks A/A2/B/C/
> C2/D/E all implemented (docs, GraphQL, response leak, CORS,
> content-type/cacheable, auth posture, risky methods), all soft-404-safe.
> 19 tests green (`test_api_security.py`, 8 new). A live scan of
> `example.com` still scores exactly 54/F (api-security added only a clean
> PASS, 307ms). Note:
> [`learning/50-api-security-agent.md`](learning/50-api-security-agent.md).
>
> V5 done, 2026-08-09. `backend/agents/misconfig.py` (new) registered as the
> 7th agent (`MisconfigAgent`, slug `misconfig`) — checks A-G all
> implemented (directory listing, backup/config files, debug output, server
> version, risky methods, default/setup pages, unsafe caching); budget
> exactly 18 requests/12s. `.env`/`.git` exclusion enforced by a dedicated
> test. 35 tests green (`test_misconfig.py`, 16 new). A live scan of
> `example.com` still scores exactly 54/F (misconfig added only an Info
> PASS, 376ms). Note:
> [`learning/51-misconfig-agent.md`](learning/51-misconfig-agent.md).
>
> V6 done, 2026-08-09. `backend/agents/subdomain.py` (new) registered as the
> 8th agent (`SubdomainAgent`, slug `subdomain`) — discovery merges
> certificate SANs (`tls.fetch_certificate`, now public), crt.sh CT logs,
> and a 12-name common list, every candidate DNS-verified before inclusion;
> `takeover_signatures.py` (new, data only) backs the honest three-outcome
> takeover/dangling-DNS decision table. Structured inventory persisted via
> migration v9 (`subdomains` table, `storage/subdomains.py`) and carried on
> `ScanReport.subdomains`; `ScanContext.shared["subdomains"]` is how the
> agent hands its inventory to `orchestrator._finalize`. Budget ≤40 DNS
> lookups + ≤25 HTTP requests/15s, capped at 25 discovered hosts / 10
> followed-up. 12 new tests (`test_subdomain.py`, 47 total, all green) —
> DNS/CT/TLS are monkeypatched, never real network calls. A live scan of
> `example.com` now scores 52/F (was 54) — `www.example.com` also missing
> HSTS/CSP aliases onto the apex's existing base_id and decays to 50%
> weight, exactly V3's dedup design; an expected score change per Decision
> 4, not a regression. Note:
> [`learning/52-subdomain-agent.md`](learning/52-subdomain-agent.md).
>
> V7 done, 2026-08-09. Three new checklist rules (`no_directory_listing`,
> `no_debug_output` [blocking], `no_dangling_dns`) in
> `backend/checklist/rules.py`, reusing the existing `_from_finding` helper
> unchanged. `backend/ai/prompts.py`: `PROMPT_VERSION` → `"v3"`;
> `build_analyst_messages` now splits findings into confirmed vs.
> needs-verification by `confidence` (threshold 0.9, matching
> `FindingRow.tsx`'s chip) and carries `affected_url` per line;
> `ANALYST_SYSTEM` gained explicit anti-hallucination/anti-restatement
> instructions; `build_chat_messages` gained `affected_url`/`confidence` per
> finding and a new subdomain-inventory section. 9 new tests
> (`test_checklist_v4.py`, 56 total, all green). Verified: a live 8-agent
> scan of `example.com` with `GROQ_API_KEY` unset still returns a complete
> report (`summary == ""`) at the same 52/F score as V6. Note:
> [`learning/53-checklist-and-ai-integration.md`](learning/53-checklist-and-ai-integration.md).
>
> V8 done, 2026-08-09. `frontend/lib/api.ts` gained `SubdomainEntry` +
> `ScanReport.subdomains`; `ScanProgress.tsx`'s grid went `sm:grid-cols-5` →
> `sm:grid-cols-4` for 8 panels; new `SubdomainTable.tsx` renders the
> inventory, sortable by issue count; the subdomain agent's detail page
> fetches the full report only for that one slug. `AgentReel.tsx` and
> `FindingRow.tsx` needed zero changes — both were already generic. Verified
> live in the browser: 8 panels filled, final score 52/F (matches V6,
> confirming zero backend logic touched), subdomain table rendered correctly.
> Artwork for the 3 new agent plates still outstanding (not self-sourced).
> Note: [`learning/54-frontend-attack-surface-ui.md`](learning/54-frontend-attack-surface-ui.md).
>
> V9 done, 2026-08-09. Three new test files — `test_probe.py` (16 tests:
> `ResponseCache` concurrent dedup, `RobotsGate` allow/disallow, `Budget`
> request+deadline cutoff, `safe_get/head/options` swallowing
> Connect/Timeout/SSL errors), `test_findings_schema.py` (11 tests: a real
> SQLite round trip via a new `temp_db` fixture — `affected_url`/
> `confidence` survive save→load including the `confidence=0.0` falsy trap,
> `SubdomainEntry.tls_valid`'s True/False/None round-trips through SQLite's
> 0/1/NULL encoding, `get_scan`'s counts still exclude PASS findings),
> `test_orchestrator.py` (7 tests: `normalize_url` rejects bad input, an
> unreachable host raises the friendly `ValueError`, and — the matrix's
> named acceptance case — one agent raising leaves the other seven results
> intact and the report still completes, for both `run_scan` and
> `run_scan_stream`). Plus targeted failure-case tests added to the three
> V4-V6 agent files (malformed JSON, 403/404/429-everywhere, redirect
> chains, a mid-scan DNS exception) — 45 new tests total, 101 passing (was
> 56). No third-party site touched anywhere in the suite. Note:
> [`learning/55-testing-the-untested-paths.md`](learning/55-testing-the-untested-paths.md).
>
> V10 done, 2026-08-09. `README.md`'s agent table grew to 8 rows plus a new
> "What Sentinels does *not* do" section; `CLAUDE.md` gained the plan's two
> non-negotiables (bounded probing, stated confidence) and its agent count;
> `docs/ROADMAP.md` got a "Beyond this roadmap" pointer to PLAN-v2/v3/v4 (it
> had never referenced any of them); `docs/ACTIVITY_LOG.md` got a closing
> summary entry. Full end-to-end pass against a real running app (not just
> the test suite): `example.com` live-scanned in the browser at 52/F, 8
> panels; the same URL scanned twice via the API both times 52/F
> (determinism); `wordpress.org` — a genuinely busier site — scored 61/D in
> 22.5s with 11 discovered subdomains and zero agent errors; a throwaway
> backend with `GROQ_API_KEY` forced empty still returned a complete 52/F
> report with `summary: ""`; a scan stored on 2026-08-03 (pre-v4) reloaded
> correctly at its original 54/F with exactly 5 agents and `subdomains: []`;
> all three export formats (PDF/Markdown/JSON) for the `wordpress.org` scan
> carry the three new agents' findings and the subdomain inventory,
> confirmed by parsing them directly. Live verification caught one real gap
> no test could have — the scan dialog's copy still said "Five agents" —
> fixed in `ScanDialog.tsx` and reverified live. Note:
> [`learning/56-shipping-v4.md`](learning/56-shipping-v4.md).
>
> **PLAN-v4 is complete — V1 through V10, all verified.**

---

## Context

Sentinels' URL scan today looks at exactly one host, over HTTP and DNS, through
five agents (`headers`, `recon`, `tls`, `exposure`, `dns`). This plan adds three
more, all still **strictly passive**:

| New agent | Question it answers |
|---|---|
| **API Security** | Is there a publicly reachable API, and what does it give away? |
| **Subdomain Security** | What else does this domain expose besides `www`, and is any of it dangling? |
| **Misconfiguration** | Is the server itself set up carelessly — listings, backups, debug pages, risky methods? |

Nothing is rewritten. Every new agent is a `BaseAgent` subclass returning
`Finding` objects into the existing pipeline:

```
URL → 8 agents (concurrent) → Findings → scoring.py → checklist → Groq → report
```

The only shared machinery that genuinely changes is **scoring**, and only
because eight agents can now see the *same* underlying problem (§V3).

### Non-negotiables carried forward

From `CLAUDE.md`, unchanged and binding on every line of this plan:

- **Passive only.** GET / HEAD / OPTIONS to public paths, public DNS lookups,
  TLS handshakes. No fuzzing, no wordlists, no auth bypass, no POST/PUT/DELETE,
  no deliberate error triggering, no rate that could hurt a target.
- **Agents never crash the scan.** `BaseAgent.run()` already guarantees this;
  new agents additionally catch per-probe failures so one dead path doesn't
  cost the other checks in the same agent.
- **Scoring stays deterministic.** No model in the loop, ever.
- **AI only enriches.** No `GROQ_API_KEY` → the report is still complete.
- **Every change gets its learning note**, short and plain-language.

### Two new non-negotiables (add to `CLAUDE.md` in V10)

1. **Bounded probing.** Every agent declares a hard cap on how many requests it
   may issue against a target, and a wall-clock deadline. No check may loop over
   an unbounded list. The caps are constants at the top of each agent file so
   they can be read in five seconds.
2. **Confidence is stated, never implied.** Any finding that can't be proven
   from what was observed says so — in its title ("Potential…"), in its wording
   ("manual verification recommended"), and in the new `confidence` field. We
   never upgrade a guess into a claim.

---

## What already exists (verified by reading the code, 2026-08-09)

This is the inventory the plan builds on. Nothing below needs replacing.

| Piece | Where | Reused how |
|---|---|---|
| Finding schema | [`backend/models.py:72`](../backend/models.py) — `id, title, category, severity, status, owasp, evidence, description, remediation, agent, evidence_items, file_path, line` | Extended additively in V1 (`affected_url`, `confidence`) |
| Severity enum | `models.Severity` — Critical / High / Medium / Low / Info | Reused as-is. **No second severity system.** |
| Penalties | `models.SEVERITY_PENALTY` — 25 / 15 / 8 / 3 / 0 | Reused as-is |
| Agent contract | [`backend/agents/base.py`](../backend/agents/base.py) — `ScanContext`, `BaseAgent.scan()`, crash-proof `run()`, `self.evidence(...)` | New agents subclass it unchanged |
| Registry | [`backend/agents/registry.py`](../backend/agents/registry.py) — one list, `AGENTS` | +3 lines, nothing else |
| Orchestrator | [`backend/orchestrator.py`](../backend/orchestrator.py) — `asyncio.gather` / `as_completed`, one shared `httpx.AsyncClient` | Picks the new agents up automatically |
| Scoring | [`backend/scoring.py`](../backend/scoring.py) — pure functions | Gains dedup + caps (V3) |
| Checklist | [`backend/checklist/rules.py`](../backend/checklist/rules.py) — declarative rules keyed on `finding.id` | +3 rules (V7) |
| AI layer | [`backend/ai/prompts.py`](../backend/ai/prompts.py), `analyst.py`, `fixes.py`, `chat.py` | Prompts tightened, `PROMPT_VERSION` bumped (V7) |
| Storage | [`backend/db.py`](../backend/db.py) numbered migrations, [`backend/storage/`](../backend/storage/) | +2 migrations (V1, V6) |
| Exporters | [`backend/report/`](../backend/report/) — all iterate findings generically | No change needed |
| Frontend agent UI | `ScanProgress.tsx`, `AgentReel.tsx`, `/scan/[scanId]/agents/[agentName]/page.tsx` (fully generic, driven by `GET /agents`) | Small additive changes (V8) |

**The agent detail page already works for any newly registered agent with zero
changes** — that was M8's explicit verification bar. The frontend work in V8 is
therefore polish (artwork, grid, subdomain table), not plumbing.

---

## Overlap map — what each new agent must NOT re-check

The single biggest risk in this plan is three new agents re-reporting what the
existing five already found. Every new check below was chosen against this table
and the plan says explicitly, per agent, what it defers.

| Already owned by | Check | New agent's rule |
|---|---|---|
| `headers` | CSP, HSTS, X-Content-Type-Options, X-Frame-Options **on the apex host** | API/Misconfig never re-report these for the apex. Subdomain reports them **per subdomain only**, and at reduced weight (§V3). |
| `headers` | — | CORS (`Access-Control-Allow-Origin` / `-Credentials`) is **not** checked anywhere today → owned by **API Security**. |
| `recon` | `<meta name="generator">`, `robots.txt` sensitive paths | Misconfig never re-reports these. Its server-disclosure check fires only on `Server` / `X-Powered-By` / `X-AspNet-Version` **carrying a version number** — a different signal, different header, different fix. |
| `exposure` | `/.env`, `/.git/HEAD` | Misconfig's backup-file list **excludes both**, enforced by a test. |
| `tls` | Apex certificate validity, expiry, protocol version | Subdomain reuses `tls._fetch_certificate` for *other hosts only*; never re-checks the apex. |
| `dns` | SPF, DMARC on the apex | Subdomain does A/AAAA/CNAME lookups only. No SPF/DMARC anywhere. |

---

## Decisions to confirm (defaults chosen, say so if you disagree)

1. **Agent slugs:** `api-security`, `subdomain`, `misconfig` — hyphenated, matching
   the `repo-*` convention, and they appear in URLs (`/scan/<id>/agents/subdomain`).
   *(Your brief wrote `api_security`; the value shipped in `Finding.agent` will
   match whichever slug we register, so the two must not drift.)*
2. **Certificate Transparency lookup (crt.sh):** ON by default as a subdomain
   discovery source — it is entirely passive (a public log query, no traffic to
   the target) and finds real subdomains a 12-word list never will. Hard 5 s
   timeout, any failure = silently skipped. Say the word and it comes out.
3. **Artwork:** `AgentReel` needs three new plate images
   (`frontend/public/agents/{api-security,subdomain,misconfig}.webp`, ~1400×900,
   matching the existing set's look). **I won't source these — tell me what you
   want and I'll ask for them.** Until they exist the reel degrades to a plain
   coloured plate, which already works.
4. **Scores will change** for every site once these land — eight agents see more
   than five. That's correct behaviour, not a regression. Old stored scans keep
   their old score; only new scans reflect the new agents.

---

## PHASE V-A — Foundation *(no new checks yet, no visible UI change)*

### V1 — Finding schema: `affected_url` + `confidence`

> **Status:** done, 2026-08-09. Migration v8 applied in place to the existing
> database; all three pre-v4 stored scans re-read at identical scores with both
> new fields NULL; a live `POST /scan` of `example.com` scored 54/F exactly as
> the stored pre-change scan of the same site did. Note:
> [`learning/47-affected-url-and-confidence.md`](learning/47-affected-url-and-confidence.md).

**Files:** `~backend/models.py`, `~backend/db.py`, `~backend/storage/findings.py`,
`~backend/storage/scans.py`, `~frontend/lib/api.ts`, `~frontend/components/FindingRow.tsx`

The current `Finding` covers seven of your nine requested fields already
(`recommendation` is called `remediation` — the project's name wins). Two are
genuinely missing, and both matter for the new agents:

```python
affected_url: Optional[str] = None   # the exact URL/host this finding is about
confidence: Optional[float] = None   # 0.0-1.0; None = "not applicable / certain"
```

- `affected_url` is what makes a subdomain finding legible ("HSTS missing" — on
  *what*?) and is the dedup key in V3.
- `confidence` exists so "Potential subdomain takeover" can be honest. Only the
  new agents set it; the existing five leave it `None`, which the UI renders as
  nothing at all — no visual change to any existing finding.

**Migration v8** (additive, backfills as NULL, matching `_V6_SCHEMA`'s pattern):

```sql
ALTER TABLE findings ADD COLUMN affected_url TEXT;
ALTER TABLE findings ADD COLUMN confidence REAL;
```

`storage/findings.py` writes them; `storage/scans.py:get_scan` reads them back.
`frontend/lib/api.ts` mirrors both as `string | null` / `number | null`.
`FindingRow` shows `affected_url` as a mono line under the title when present,
and a `confidence 60%` chip when `confidence !== null && confidence < 0.9`.

**Also fixed here** (one-line, pre-existing bug this plan would otherwise make
much worse): `storage/scans.py:get_scan` rebuilds `counts` by counting *every*
finding including passes, while a live scan counts only non-passes
(`scoring.count_by_severity`). Today that's a small discrepancy after a page
refresh; with three agents emitting dozens of INFO/PASS inventory findings it
becomes glaring. `get_scan` will call `count_by_severity` like everyone else.

**Verify:** existing scan re-read from the DB is byte-identical apart from the
corrected `counts`; a hand-built `Finding` with both new fields round-trips
through save → load; frontend renders an unchanged finding exactly as before.

---

### V2 — Shared probe layer: one fetch cache, robots.txt, budgets

> **Status:** done, 2026-08-09. Verified against `httpx.MockTransport`
> (script, not yet a committed test — pytest infra is V3's job): two
> concurrent `cache.get()` calls for the same URL produced exactly one
> transport hit; `Disallow: /admin` made `allowed("/admin")` false while
> `allowed("/pricing")` stayed true; `Budget(3, ...)` allowed exactly 3
> requests then set `partial`. A live `run_scan("example.com")` after the
> change still scored 54/F, matching V1's baseline exactly. Note:
> [`learning/48-shared-probe-layer.md`](learning/48-shared-probe-layer.md).

**Files:** `+backend/agents/probe.py`, `~backend/agents/base.py`

Three new agents probing a site independently would mean re-fetching the
homepage four times over and re-fetching `robots.txt` twice. This is the
"reuse existing requests, no duplicate HTTP" requirement, solved once.

`ScanContext` gains two fields, both defaulted so **every existing agent and
every existing call site is untouched**:

```python
@dataclass
class ScanContext:
    url: str
    client: httpx.AsyncClient
    cache: "ResponseCache" = field(default_factory=ResponseCache)   # new
    shared: dict = field(default_factory=dict)                      # new
```

`probe.py` contains:

- **`ResponseCache.get(client, url, *, method, follow_redirects, timeout)`** —
  memoizes by `(method, url, follow_redirects)`. Two agents asking for the same
  URL at the same moment share **one** in-flight request: the cache stores an
  `asyncio.Task`, created under an `asyncio.Lock`, and the second caller awaits
  the same task rather than starting a second one. *(This is the milestone's
  async teaching moment — the learning note explains tasks vs. coroutines and
  why awaiting the same task twice is fine.)*
- **`RobotsGate`** — fetches `/robots.txt` once (through the cache, so `recon`'s
  existing fetch is reused), parses it with stdlib `urllib.robotparser`
  (`RobotFileParser.parse(lines)` — no network of its own), and answers
  `allowed(path) -> bool` for `User-agent: *`. A path we're told not to fetch is
  **skipped**, and the skip is recorded as evidence, not silently dropped.
- **`Budget(max_requests, deadline_seconds)`** — a counter plus an
  `asyncio.Semaphore(4)`. Exhausting either stops further probes and flips a
  `partial` flag the agent reports in its evidence ("stopped after N requests /
  Xs — results may be incomplete"). Not an error: a partial answer is honest,
  a crash is not.
- **`safe_get/safe_head/safe_options`** — wrap one request, catch
  `httpx.HTTPError` / `asyncio.TimeoutError` / `ssl.SSLError` and return `None`
  instead of raising, so one dead path never ends an agent's whole run.

Per-request timeout for probes: **5 s** (the shared client's default is 10 s;
probes are cheaper and more numerous, so they get less patience).

**Verify:** a unit test with `httpx.MockTransport` proves two concurrent
`cache.get()` calls for the same URL produce exactly one transport hit; a
`robots.txt` with `Disallow: /admin` makes `allowed("/admin")` false; a budget of
3 stops the 4th probe and sets `partial`.

---

### V3 — Scoring: deduplication, decay, and per-agent caps

**Files:** `~backend/scoring.py`, `+backend/tests/test_scoring.py`

This is the milestone that must not be got wrong. Eight agents can see one
underlying problem; the score must count it once.

`calculate_score` keeps its shape — start at 100, subtract penalties, clamp at 0 —
and gains three deterministic rules, applied in this order:

**1. Identity.** Each non-passing finding gets an *issue key*:

```
issue_key = (base_id, host)
   base_id = finding.id up to the first ":"      # "subdomain-missing-hsts:api.example.com" → "subdomain-missing-hsts"
   host    = hostname of affected_url, or the scanned host if affected_url is None
```

Duplicate issue keys are collapsed, keeping the **highest** severity. Two agents
reporting the same thing about the same host therefore cost points once.

**2. Cross-agent aliases.** A small explicit table maps different agents' names
for the same underlying issue onto one canonical key:

```python
ALIASES = {
    "subdomain-missing-hsts": "missing-hsts",
    "subdomain-missing-csp":  "missing-csp",
    "subdomain-tls-invalid":  "tls-cert-invalid",
    "api-missing-hsts":       "missing-hsts",
}
```

Because the *host* is part of the key, this suppresses the apex duplicate your
brief calls out (`headers` and `subdomain` both seeing missing HSTS on
`example.com` → one deduction) while correctly **keeping** the genuinely
separate problem (`api.example.com` also missing HSTS → still counted, at
decayed weight per rule 3).

**3. Repeat decay + per-agent cap.** The same base issue found on many hosts is
one operational mistake, not fifteen:

| Occurrence of the same `base_id` | Weight |
|---|---|
| 1st | 100 % |
| 2nd–3rd | 50 % (rounded down) |
| 4th and beyond | 0 % |

and each of the three new agents may deduct at most **20 points total**
(`AGENT_PENALTY_CAP`). Deduction order is sorted by `(severity desc, host asc,
base_id asc)` before decay is applied, so the result never depends on which
agent finished first. Existing agents are **not** capped — nothing about their
contribution changes.

**Backward compatibility is the acceptance test.** The five existing agents each
emit one finding per `id`, all on the apex host, all with `affected_url = None`
→ every issue key unique → every penalty full → **the score for any given site
is identical to today's**, given the same five agents' findings. `test_scoring.py`
pins this with a fixture of real 5-agent findings.

Also in this milestone, since it's the first file with tests:

**Test infrastructure bootstrap** — `+backend/requirements-dev.txt`
(`pytest`, `pytest-asyncio`, `anyio`), `+backend/pytest.ini`,
`+backend/tests/__init__.py`, `+backend/tests/conftest.py` with a
`mock_site(routes: dict[str, tuple[int, dict, str]])` fixture built on
`httpx.MockTransport`. **No test in this plan touches a real third-party site.**

**Verify:** `pytest backend/tests -q` green; the regression fixture scores
identically to a pre-change run of the same findings; a synthetic 30-subdomain
report can never drop the score by more than 20 from the subdomain agent.

---

## PHASE V-B — The three agents

Each agent lands **registered and live** in its own milestone, so it's
verifiable end-to-end (`POST /scan` against a real site, plus a mocked unit
test) before the next one starts.

### V4 — API Security agent

> **Status:** done, 2026-08-09. All 7 checks (A, A2, B, C, C2, D, E)
> implemented in `backend/agents/api_security.py`, registered as the 6th
> agent. 8 new tests in `test_api_security.py` (19 total, all green). Note:
> [`learning/50-api-security-agent.md`](learning/50-api-security-agent.md).

**Files:** `+backend/agents/api_security.py`, `~backend/agents/registry.py`,
`+backend/tests/test_api_security.py`

```
slug: api-security   display: "API Security"   category: "API"
```

**Discovery — 11 paths, hard-capped, robots-gated, HEAD-first:**

```
/api  /api/v1  /api/v2  /api/docs  /swagger  /swagger-ui
/swagger.json  /openapi.json  /openapi.yaml  /graphql  /graphiql
```

HEAD first; a GET follows **only** for paths that answer 200 and are candidates
for content inspection (the doc/spec ones). Non-200, redirect-away, and
HTML-soft-404 responses are discarded using the same
"does the body actually look like the thing" discipline `exposure.py` already
uses — an OpenAPI hit must parse as JSON/YAML with an `openapi`/`swagger` key,
not merely return 200.

**Checks**

| # | Check | Finding | Severity |
|---|---|---|---|
| A | Public API docs (Swagger UI / OpenAPI JSON / YAML) | `api-docs-public` | **Info** (PASS-adjacent) → **Low** if the spec's `servers`/`paths` reveal internal hosts or admin-ish endpoints |
| A2 | GraphQL endpoint reachable over GET | `api-graphql-exposed` | **Info**; **Medium** only if the GET response itself shows introspection is enabled (e.g. a GraphiQL IDE served to anonymous users). **We never send an introspection POST.** |
| B | Sensitive data in a public API/doc response — internal hostnames (`*.internal`, `10.x`, `192.168.x`), stack traces, DB error strings, framework debug markers | `api-response-leak` | **Medium**, **High** if credentials/tokens are pattern-matched (and the value is **never echoed** — count + type only, `exposure.py:74`'s rule) |
| C | CORS: `Access-Control-Allow-Origin: *` — **and** `Allow-Credentials: true`, which is the actually-dangerous combination | `api-cors-permissive` | **Medium** alone, **High** with credentials |
| C2 | API responses served without `Content-Type: application/json`, or cached (`Cache-Control` missing/`public`) on a JSON endpoint | `api-content-type`, `api-cacheable-response` | **Low** |
| D | Auth posture: does a discovered endpoint return data (200 + JSON body) to an anonymous request? | `api-unauthenticated-endpoint` | **Medium**, `confidence 0.5` — wording says "appears to be publicly readable; verify whether this data is meant to be public". Never an assertion. |
| E | `OPTIONS` on the discovered API base → `Allow` header lists PUT/DELETE/PATCH/TRACE | `api-risky-methods` | **Low**, **Medium** if PUT *and* DELETE. **The methods are never invoked.** |

Budget: **≤ 16 requests, 12 s**. Clean site → one PASS finding
(`api-surface-clean`, "no publicly reachable API endpoints found"), never an
empty result, never an error.

**Verify:** mocked site serving a valid `/openapi.json` → exactly one docs
finding with the right URL in `affected_url`; a site 200-ing every path with
HTML → **zero** findings (soft-404 discipline); a site with
`Access-Control-Allow-Origin: *` + credentials → High; unreachable site → clean
`AgentResult` with `error` set, scan still completes. Live run against a real
site with a public API (e.g. a public docs site you pick) under 12 s.

---

### V5 — Misconfiguration agent

> **Status:** done, 2026-08-09. All 7 checks (A-G) implemented in
> `backend/agents/misconfig.py`, registered as the 7th agent. 16 new tests
> in `test_misconfig.py` (35 total, all green). Note:
> [`learning/51-misconfig-agent.md`](learning/51-misconfig-agent.md).

**Files:** `+backend/agents/misconfig.py`, `~backend/agents/registry.py`,
`+backend/tests/test_misconfig.py`

```
slug: misconfig   display: "Misconfiguration"   category: "Misconfiguration"
```

Every check below was chosen against the overlap map. Nothing here re-reports
`.env`, `.git`, the generator tag, robots.txt, or any of the four security
headers.

| # | Check | How | Finding / severity |
|---|---|---|---|
| A | **Directory listing** on `/uploads/ /files/ /assets/ /backup/ /images/ /static/` (6 paths) | 200 + body matching autoindex markers (`<title>Index of /`, nginx's `<h1>Index of`, Apache's `<table id="indexlist">`) — a marker match, not just a 200 | `dir-listing` — **Low** for ordinary static assets, **Medium** when the listing contains backup/archive/DB extensions, **High** if it contains a `.sql`/`.env`-shaped name |
| B | **Backup / config files** — `/backup.zip /backup.sql /database.sql /site.tar.gz /web.config.bak /config.php.bak /.DS_Store` (7, **excluding** `.env` and `.git`, test-enforced) | 200 + non-HTML content-type + a size/shape sanity check | `backup-file-exposed` — **High**, **Critical** for a database dump (`.sql`, `dump`) that really parses as SQL |
| C | **Debug / error exposure** | Pattern scan over **responses we already have** (homepage, robots, every probe response — via V2's cache). Markers: `Traceback (most recent call last)`, Werkzeug/Whoops/Symfony debug pages, ASP.NET yellow-screen, `Warning: mysqli`, `ORA-\d+`, `SQLSTATE[`, `X-Debug-Token`. **We never deliberately trigger an error.** | `debug-output-exposed` — **High** (stack trace / debugger) or **Medium** (verbose error text) |
| D | **Server version disclosure** | `Server` / `X-Powered-By` / `X-AspNet-Version` containing a version number (`\d+\.\d+`) | `server-version-disclosed` — **Low**. Bare `Server: nginx` with no version = PASS, explicitly, so this can't become a Recon duplicate |
| E | **HTTP methods** | `OPTIONS /` → `Allow` header | `risky-http-methods` — **Low** (TRACE), **Medium** (PUT/DELETE enabled). Never invoked. |
| F | **Default / setup pages** | Homepage + `/install.php /setup.php /phpinfo.php` (3 paths) against markers: "Welcome to nginx!", "Apache2 Ubuntu Default Page", "IIS Windows Server", "It works!", Laravel/Django welcome, phpinfo's signature table | `default-page-served` — **Low**; `setup-page-exposed` — **High** (an installer reachable by anyone is a takeover path) |
| G | **Unsafe caching** | A response that both sets a `Set-Cookie` (session-shaped) **and** carries `Cache-Control: public` / a positive `max-age` with no `no-store`/`private` | `sensitive-response-cacheable` — **Medium**. No evidence → no finding. |

Budget: **≤ 18 requests, 12 s**. Clean site → PASS findings, never empty.

**Verify:** mocked autoindex page → `dir-listing` with the right severity by
content; a `.sql` dump → Critical; a site returning a styled 404 for every probe
→ zero findings; `Server: nginx` → PASS while `Server: nginx/1.18.0` → Low;
`.env`/`.git` never appear in this agent's output (asserted directly against
`exposure.py`'s paths).

---

### V6 — Subdomain Security agent

**Files:** `+backend/agents/subdomain.py`, `+backend/agents/takeover_signatures.py`,
`~backend/agents/registry.py`, `~backend/models.py`, `~backend/db.py`,
`~backend/storage/subdomains.py` *(new)*, `~backend/orchestrator.py`,
`+backend/tests/test_subdomain.py`

```
slug: subdomain   display: "Subdomain Security"   category: "Subdomain"
```

**Discovery — three passive sources, merged and deduped:**

1. **Certificate SANs** of the apex certificate — reuses `tls._fetch_certificate`
   (promoted to a shared helper, TLS agent behaviour unchanged). Free: a
   handshake we already do, listing hostnames the owner themselves published.
2. **Certificate Transparency** (`crt.sh?q=%25.<domain>&output=json`), 5 s
   timeout, any failure silently skipped. Passive — a public log, no traffic to
   the target. *(Decision 2 above; easy to remove.)*
3. **A 12-name list**, DNS-resolved only: `www api dev staging test admin app
   dashboard mail blog docs cdn`. **A name is only reported if DNS actually
   resolves it** — never "common, therefore probably exists".

Merged, deduped, capped at **25 discovered hosts** for the inventory and
**10 hosts** for HTTP/TLS follow-up (sorted deterministically: apex-adjacent and
resolving-with-CNAME first, then alphabetical — so the same domain always yields
the same 10).

**Per followed-up host, at most:** 1 DNS resolve (already done) + 1 HTTPS GET
(redirects followed, 5 s) + 1 HTTP GET only if HTTPS failed + 1 TLS handshake.

**Structured inventory.** A new model, mirroring `RepoFileEntry`'s precedent:

```python
class SubdomainEntry(BaseModel):
    host: str
    record_type: str            # "A" | "AAAA" | "CNAME"
    record_value: str
    source: str                 # "certificate" | "ct-log" | "common-name"
    http_status: Optional[int] = None
    scheme: Optional[str] = None            # "https" | "http" | None
    tls_valid: Optional[bool] = None
    server: Optional[str] = None
    redirects_to: Optional[str] = None
    issue_count: int = 0
```

Carried on `ScanReport.subdomains: list[SubdomainEntry] = []` (additive, so the
live SSE `done` event and `GET /scans/{id}` both already carry it), persisted by
**migration v9** into a `subdomains` table keyed on `scan_id`, written by
`storage/subdomains.py` inside `save_scan`'s existing transaction. The agent
publishes the list via `context.shared["subdomains"]` (V2's `shared` dict);
`orchestrator._finalize` reads it — `.get("subdomains", [])`, so a failed
subdomain agent simply means an empty list, never a broken report.

**Per-subdomain security findings** (each with `affected_url` set, each
subject to V3's decay and the 20-point cap):

- `subdomain-missing-hsts` / `subdomain-missing-csp` — **Low** each (they're
  aliased onto the apex issue when the host *is* the apex, §V3)
- `subdomain-tls-invalid` — **High** (expired / untrusted / hostname mismatch)
- `subdomain-plain-http` — **Medium** (resolves and serves over HTTP with no
  HTTPS available)
- `subdomain-server-disclosed` — **Low**
- `subdomain-sensitive-name-live` — **Medium** — a live, publicly reachable
  `staging.` / `dev.` / `test.` / `admin.` host returning 200 without any auth
  challenge. Non-production environments are the classic soft target.

**Dangling DNS / takeover — the honest version.** `takeover_signatures.py` is
data only (provider suffix → fingerprint string → provider name), covering
GitHub Pages, S3, CloudFront, Heroku, Azure, Netlify, Cloudflare Pages,
Shopify, Ghost, WordPress.com, Surge, Bitbucket, Readthedocs, Fastly. Three
outcomes, and only one of them uses the word "takeover":

| Evidence | Finding | Severity | Confidence |
|---|---|---|---|
| CNAME → known provider **and** the response body matches that provider's specific unclaimed fingerprint | `subdomain-takeover-potential` — "Potential subdomain takeover — verify manually" | **High** | 0.9 |
| CNAME exists but its **target does not resolve** (NXDOMAIN) | `subdomain-dangling-dns` — "Potential dangling DNS record — manual verification recommended" | **Medium** | 0.6 |
| CNAME → known provider that serves normally | *inventory only, no finding* | — | — |

**A CNAME pointing at a known provider is never, on its own, a takeover claim.**
That rule gets its own test.

Budget: **≤ 40 DNS lookups + ≤ 25 HTTP requests, 15 s** (the slowest of the
three; still concurrent with everything else, so the scan stays under 60 s).

**Verify:** mocked resolver + mocked HTTP — a CNAME to `foo.github.io` serving
the real "There isn't a GitHub Pages site here" body → High/0.9; the same CNAME
serving a normal page → **no finding**; an NXDOMAIN target → Medium/0.6 with
"manual verification" wording; a domain with zero discoverable subdomains → one
PASS finding and an empty inventory, not an error; `crt.sh` timing out → the
other two sources still work. Live run against a real domain with known
subdomains, under 15 s.

---

## PHASE V-C — Integration

### V7 — Checklist rules + AI report integration

**Files:** `~backend/checklist/rules.py`, `~backend/ai/prompts.py`,
`+backend/tests/test_checklist_v4.py`

**Checklist** — three new `auto` rules, same declarative shape as the existing
ten (each just names a finding id):

| Key | Title | Agent | Blocking |
|---|---|---|---|
| `no_directory_listing` | Directory listing disabled | `misconfig` | no |
| `no_debug_output` | No debug output or stack traces exposed | `misconfig` | **yes** |
| `no_dangling_dns` | No dangling DNS records | `subdomain` | no |

`_from_finding`'s `absent_state="pass"` default means a scan whose agent didn't
run still reads sensibly rather than showing a false failure.

**AI.** The pipeline is exactly what your brief describes and already exists:

```
agents → normalized findings → deterministic score (scoring.py) → Groq → summary
```

`ai/analyst.py` is not restructured. What changes is the *prompt contract*, in
`ANALYST_SYSTEM` and `build_analyst_messages`:

- Findings are passed grouped **confirmed vs. needs-verification** (split on
  `confidence`), and each line carries `affected_url` when set — so the model can
  say *which host*, and can't blur a 0.6-confidence dangling record into a fact.
- Explicit instructions added, in the model's own system message: **do not
  invent findings; do not restate or alter the score or grade (they are
  computed, not yours); do not change any severity; prioritise by severity then
  confidence; explain business impact in plain language; give concrete
  remediation steps; state clearly which items are confirmed and which need
  manual verification.**
- Chat digest (`build_chat_messages`) gains the subdomain inventory and
  `affected_url`s so "which subdomains did you find?" is answerable.
- `PROMPT_VERSION` → `"v3"`, which invalidates cached fix suggestions with no
  migration (existing mechanism).

**Verify:** with `GROQ_API_KEY` unset, a full 8-agent scan still returns a
complete report with `summary == ""` (graceful degradation intact); with a key,
the summary names a real finding, states no number that contradicts
`report.score`, and hedges appropriately on a low-confidence finding.

---

### V8 — Frontend

**Files:** `~frontend/lib/api.ts`, `~frontend/components/ScanProgress.tsx`,
`~frontend/components/AgentReel.tsx`, `~frontend/components/FindingRow.tsx`,
`+frontend/components/SubdomainTable.tsx`,
`~frontend/app/scan/[scanId]/agents/[agentName]/page.tsx`

Design language unchanged — glass panels, mono labels, the existing type scale.
No redesign.

1. **`lib/api.ts`** — mirror the backend additions: `Finding.affected_url`,
   `Finding.confidence`, `SubdomainEntry`, `ScanReport.subdomains`.
2. **`ScanProgress.tsx`** — `FALLBACK_NAMES.url` gains the three slugs; the grid
   goes `sm:grid-cols-5` → `sm:grid-cols-4` so eight panels form two clean rows
   of four instead of 5 + 3. *(The live list still comes from `GET /agents`; the
   fallback only matters when the backend is briefly unreachable.)*
3. **`AgentReel.tsx`** — three `PANELS` entries. Needs artwork (Decision 3);
   until then the existing fallback-colour path renders them correctly.
4. **`FindingRow.tsx`** — `affected_url` as a mono line; a `NEEDS VERIFICATION`
   chip when `confidence !== null && confidence < 0.9`. Both conditional, so no
   existing finding renders differently.
5. **`SubdomainTable.tsx`** — the attack-surface table: host, source, record,
   HTTP status, HTTPS/TLS, server, redirect target, issue count. Sortable by
   issue count. Rendered on the `subdomain` agent's detail page, which fetches
   `fetchScan(scanId)` alongside its agent result **only for that slug** — the
   page stays generic for every other agent.

Per-agent detail pages for all three agents need **no other work** — status,
finding count, severity, description, evidence, and recommendation are already
rendered generically (that was M8's verification bar, and it holds).

**Verify:** in the browser — run a live scan, watch eight panels fill; open each
new agent's page and confirm findings, evidence, and recommendations render;
open the subdomain page and confirm the table; confirm an existing scan from
before this branch still renders identically.

---

### V9 — Tests

**Files:** `+backend/tests/test_probe.py`, `+backend/tests/test_findings_schema.py`,
plus the per-agent files from V4–V6

The full matrix your brief asks for, all against mocked fixtures — **no
third-party site is ever scanned by the test suite**:

*Detection logic:* API discovery · Swagger/OpenAPI detection (including
rejecting a 200-HTML soft-404) · API response leak patterns · CORS combinations ·
subdomain discovery from each of the three sources · dangling-vs-takeover
decision table · directory-listing marker matching · backup-file detection ·
debug/error markers · default-page markers · risky-method parsing.

*Cross-cutting:* severity assignment per evidence · duplicate-finding prevention
(same issue from two agents = one deduction) · alias suppression · repeat decay ·
per-agent cap · **the 5-agent scoring regression** · `.env`/`.git` never
duplicated by misconfig.

*Failure cases:* invalid URL · unreachable host · connection refused · DNS
failure · TLS error · timeout mid-agent (partial result, no crash) · rate-limit
429 · malformed JSON/YAML · HTTP-only site · HTTPS-only site · redirect chains ·
403 · 404 · a site with no API · a domain with no subdomains · `crt.sh`
unavailable · one agent raising → other seven still report and the scan still
finishes.

**Verify:** `pytest backend/tests -q` green from a clean checkout, offline
(nothing in the suite needs the network).

---

### V10 — Documentation, learning notes, end-to-end pass

**Files:** `~README.md`, `~CLAUDE.md`, `~docs/ROADMAP.md`, `~docs/ACTIVITY_LOG.md`,
`~docs/PLAN-v4.md` (status lines), `+docs/learning/47…56-*.md`

- **README** — the agent table goes from five rows to eight; a new
  "What Sentinels does *not* do" section stating plainly, per agent, what is out
  of scope (no fuzzing, no auth bypass, no introspection POST, no method
  invocation, no takeover exploitation).
- **CLAUDE.md** — the two new non-negotiables from this document's header.
- **ROADMAP.md / ACTIVITY_LOG.md** — pointer to this plan and its outcome.
- **Learning notes**, one per milestone, short and plain-language per your
  standing preference — V2's is the async one (tasks vs. coroutines, sharing an
  in-flight request), V3's is the scoring-dedup one (why the same problem seen
  twice must cost once), V6's is the honest-evidence one (why a CNAME alone
  isn't a takeover).

**End-to-end verification (the real bar):**

1. A clean site and a deliberately messy one, scanned live, both under 60 s.
2. Eight agents in the live progress UI; one killed agent (temporarily raise in
   `scan()`) → seven results, one ⚠️, **report still generated**.
3. Same site scanned twice → **identical score** (determinism).
4. `GROQ_API_KEY` removed → complete report, empty summary.
5. A pre-v4 stored scan still loads and renders correctly.
6. PDF / Markdown / JSON export all include the new findings.

---

## Files at a glance

**New (16):**
`backend/agents/{api_security,misconfig,subdomain,probe,takeover_signatures}.py` ·
`backend/storage/subdomains.py` · `backend/requirements-dev.txt` ·
`backend/pytest.ini` · `backend/tests/{__init__,conftest,test_scoring,test_probe,test_api_security,test_misconfig,test_subdomain,test_checklist_v4,test_findings_schema}.py` ·
`frontend/components/SubdomainTable.tsx` · `docs/learning/47…56-*.md`

**Modified (14):**
`backend/models.py` · `backend/db.py` · `backend/scoring.py` ·
`backend/agents/{base,registry}.py` · `backend/agents/tls.py` *(export the cert
helper; no behaviour change)* · `backend/orchestrator.py` ·
`backend/storage/{findings,scans}.py` · `backend/checklist/rules.py` ·
`backend/ai/prompts.py` · `frontend/lib/api.ts` ·
`frontend/components/{ScanProgress,AgentReel,FindingRow}.tsx` ·
`frontend/app/scan/[scanId]/agents/[agentName]/page.tsx` ·
`README.md` · `CLAUDE.md` · `docs/ROADMAP.md`

**Untouched:** every report exporter, `ai/{client,analyst,fixes,chat}.py`, the
whole repo-scan side, every existing agent's logic, every existing frontend page.

---

## Known limitations (to state in the report UI and the README)

- **Subdomain discovery is never exhaustive.** Certificate SANs + CT logs + 12
  common names find a lot, but a subdomain that has never had a certificate and
  isn't commonly named will not appear. The inventory is a floor, not a ceiling.
- **Takeover findings always need manual confirmation.** Confirming one means
  attempting to claim the resource, which is active exploitation and out of
  scope by design.
- **"Appears unauthenticated" is an observation, not a verdict.** We see that a
  public GET returned data; whether that data is *meant* to be public is a
  judgement only the owner can make.
- **CDN and WAF fronting can mask the origin.** Headers, server strings, and TLS
  seen may belong to the CDN edge, not the application.
- **crt.sh is a third-party dependency.** When it's slow or down, discovery
  quietly falls back to the other two sources.
