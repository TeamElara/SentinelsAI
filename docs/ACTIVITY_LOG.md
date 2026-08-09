# Activity log

A running diary of every prompt-driven change to this repo, for the times you're
away from the keyboard and can't read along live. **This is not the same as
`docs/learning/`** — the learning notes teach the concepts for good, this log is a
timestamped record of *what happened, why, and where*, entry per prompt/work-session.
Read this when you're back; the learning note for the achievement is still the deeper
reference if you want the full teaching version later.

Newest entries at the bottom. Each entry:

- **Prompt/trigger** — what was asked
- **What I did** — the concrete change
- **Why** — the reasoning/decision behind it
- **Where** — exact files touched
- **Verification** — how I checked it actually works, not just that it looks right
- **Other notes** — anything else worth knowing (open questions, things I deliberately
  skipped, things to watch for)

---

## 2026-07-29 — A2: Server breathes

**Prompt/trigger:** "start with a2" (continue the roadmap to achievement A2).

**What I did:** Wrote `backend/main.py` — the first real application code in the repo.
It creates a FastAPI `app` object and registers two routes:
- `GET /health` → returns a small JSON status blob (status, service name, version, UTC
  timestamp). Does zero I/O on purpose.
- `GET /` → a one-line signpost pointing at `/docs` and `/health`, so hitting the bare
  host doesn't look like a broken 404.

**Why:** A2 on the roadmap is "open `localhost:8000/health` in a browser and see JSON."
The deeper point of this achievement isn't the two routes themselves — it's establishing
the FastAPI/uvicorn split before any real scanning logic exists: FastAPI is just a
routing table (URL → function), uvicorn is the actual server holding the socket open.
Getting this boundary straight now matters later, because A11 (parallel agents) depends
on this being an *async*-capable server, not a synchronous one like Flask.

`/health` deliberately does no work (no network calls, no disk reads) so that later,
when a monitor or teammate checks it, "the process is up" and "the process is fast" stay
two separate questions. A health check that pings the internet can fail because the
internet is having a bad day, not because your server crashed — that ambiguity is a
trap I wrote around, not stumbled into.

**Where:**
- [backend/main.py](../../backend/main.py) — new file, ~45 lines
- [docs/ROADMAP.md](../ROADMAP.md) — marked A2 `[x]`, updated "Current position" to
  point at A3 next
- [docs/learning/02-fastapi-and-the-server.md](learning/02-fastapi-and-the-server.md) —
  new learning note (ASGI, decorators, the app object, uvicorn, import strings, why
  `def` not `async def` yet)

**Verification:** Actually started the server (`uvicorn main:app --port 8000`) and hit
it with real HTTP requests, not just read the code:
- `GET /health` → `200`, `content-type: application/json`, correct body
- `GET /` → `200`
- `GET /nope` (route that doesn't exist) → `404 Not Found`
- `POST /health` (route exists, wrong method) → `405 Method Not Allowed`
- Confirmed `/openapi.json` and `/docs` auto-generate correctly from the two routes
- Also independently verified the two "try this" experiments in the learning note
  before writing them down as fact: a bare 6-line ASGI app (no FastAPI at all) really
  does serve under uvicorn, and `from main import health; health()` really does work
  with zero server running (proves the decorator didn't mutate the function, just
  registered it in a table).
- Killed the background server process afterward so nothing was left running.

**Other notes:**
- Used `def health()`, not `async def health()` — deliberately. There's nothing to
  `await` yet (no network/disk calls), so making it `async` now would be introducing
  the keyword with nothing to teach it against. That lands properly in A3/A4 when a
  real `httpx` call needs awaiting.
- No response model (`Pydantic BaseModel`) on `/health` — judged as ceremony for a
  fixed 4-key blob. A5's `/scan` endpoint is where a real response model earns its
  keep, since that's the shape the frontend will actually depend on.
- Nothing here touches scanning/security logic yet — still pure scaffolding.

---

## 2026-07-29 — A3: The contract

**Prompt/trigger:** You said you're busy and asked me to keep a detailed log per
prompt in a file instead of explaining live, then said: build one achievement at a
time, stop after each, talk to you, and you'll say when to continue. This is that
first stopping point.

**What I did:** Wrote `backend/agents/base.py`, defining the two shared shapes every
future scanner agent (Headers, Recon, TLS, Exposure, DNS) will plug into:

- `ScanContext` — a small `@dataclass` holding the normalized target `url` and one
  shared `httpx.AsyncClient`, built once per scan and passed to all five agents.
- `BaseAgent` — an abstract base class (`ABC`) with one abstract method, `scan()`,
  that every real agent must implement, and one concrete method, `run()`, that every
  agent inherits unchanged. `run()` times the call and wraps it in a broad
  `try/except Exception`, so whatever goes wrong inside any agent's `scan()`, it comes
  back as a normal `AgentResult` with `.error` set — never a crash.

**Why:** Five agents are coming in Act 2. Without a shared contract enforced by
Python itself (not just a comment saying "please implement scan()"), it's easy for
one agent to end up shaped slightly differently and break the orchestrator's loop
three achievements later. `ABC` + `@abstractmethod` makes "forgot to implement scan()"
a `TypeError` at the moment you try to create the broken agent — the cheapest
possible point to catch it — rather than a mysterious failure when the orchestrator
calls it.

The `run()`/`scan()` split (template method pattern) exists specifically so the
crash-proofing rule in CLAUDE.md ("Agents must never crash the scan. Every agent
catches its own exceptions...") is guaranteed by *one* method, written once, rather
than five agents each needing to remember their own try/except correctly. `ScanContext`
sharing one `httpx.AsyncClient` across all five agents avoids each agent separately
paying the cost of opening a new TCP+TLS connection to the same target.

**Where:**
- [backend/agents/base.py](../../backend/agents/base.py) — filled in (was an empty
  scaffold file from the initial layout commit)
- [docs/ROADMAP.md](../ROADMAP.md) — marked A3 `[x]`, "Current position" now points at
  A4
- [docs/learning/03-the-agent-contract.md](learning/03-the-agent-contract.md) — new
  learning note. This one is the first real async/await explanation in the project
  (per your calibration in CLAUDE.md — async is new to you and gets explained from
  first principles every time it shows up), built from a from-scratch "boiling two
  eggs concurrently" example before touching any project code, plus a from-scratch ABC
  example (`Shape`/`Square`/`Broken`) and a from-scratch dependency-passing example
  (`Chef`/`Kitchen`) — all deliberately non-security, per CLAUDE.md's rule that new
  concepts get an ordinary standalone example before the real code.

**Verification:** Wrote and ran a real script (not just read the code) that:
1. Confirms `BaseAgent()` raises `TypeError: Can't instantiate abstract class BaseAgent
   without an implementation for abstract method 'scan'` — proves the ABC constraint is
   actually enforced, not just declared.
2. Defines a `GoodAgent` subclass, runs it through `run()` against a real (if unused)
   `httpx.AsyncClient`, and confirms it returns a populated `AgentResult` with
   `error=None`, one finding, and `duration_ms >= 10` (it slept 0.01s inside `scan()`
   to prove timing is real, not a stub value).
3. Defines a `BrokenAgent` subclass whose `scan()` raises `ValueError("simulated
   failure")`, runs it through the same `run()`, and confirms the result comes back
   clean — `findings=[]`, `error='ValueError: simulated failure'` — with no traceback
   and no crash of the test process itself.

Full output captured; all three checks passed (`ALL CHECKS PASSED`).

**Other notes:**
- Deliberately used plain `@dataclass` for `ScanContext`, not Pydantic's `BaseModel`,
  even though every other shared shape in the project (`Finding`, `AgentResult`,
  `ScanReport`) is Pydantic. Reasoning logged in the learning note: `ScanContext` never
  crosses the API boundary (it's an internal handoff inside one process), and it holds
  a live `httpx.AsyncClient`, which isn't the kind of thing you'd want a validation
  library trying to serialize.
- Deliberately caught `except Exception`, not a narrower exception type, and explained
  why in the note: this is the one place in the codebase where the entire point is
  "catch anything, known or not." It does not catch `BaseException`, so
  `KeyboardInterrupt`/`SystemExit` still work normally.
- `time.perf_counter()` used instead of `time.time()` for the duration measurement —
  monotonic, immune to system clock adjustments mid-scan.
- No real scanning logic yet — `base.py` is pure scaffolding, same as A2. A4 is where
  an agent does an actual live HTTP request.
- Per your instruction, stopping here. Say "continue" (or similar) when you want A4.

---

## 2026-07-29 — A4: First real agent

**Prompt/trigger:** "lets go with a4" — then, before I started, you asked whether the
frontend was already designed (answered: no, that's Act 4/A13+) and whether you could
change the frontend tech stack (yes, your call). I walked through 5 frontend options
(Next.js/Vite+React/Vue/Svelte/htmx); you said stick with the current plan (Next.js App
Router + Tailwind) and added a standing instruction: stop before writing any frontend
code and let you describe your design first, once we reach Act 4. I saved that as a
memory (`sentinels-frontend-design-first`) so it persists across sessions, then
continued with A4 as originally requested.

**What I did:** Wrote `backend/agents/headers.py` — `HeadersAgent`, the first subclass
of `BaseAgent` that does real work. It makes one live HTTP GET request to the scan
target (with `follow_redirects=True`) and checks four response headers:
`Content-Security-Policy`, `Strict-Transport-Security`, `X-Content-Type-Options`,
`X-Frame-Options`. Each becomes one `Finding` — `FAIL` (with the header's real severity)
if missing, `PASS` (`Info` severity) if present, with the actual header value stored as
`evidence` either way.

**Why:** The roadmap's stated deliverable for A4 was narrower ("detect a missing CSP"),
but the file is `agents/headers.py` — the entire "Headers" category for the whole
product, not a single-purpose CSP checker — and there's no later achievement dedicated
to expanding header checks. So I built out the canonical small set of security headers
now rather than just CSP, since it's the same pattern repeated (no new abstraction, no
scope creep) and makes the Act 1 checkpoint ("a working, if narrow, product") represent
a page's actual header posture instead of one arbitrary header.

Returning a `Finding` for PASSing checks too (not just failures) was a deliberate call:
A6's scoring needs to know what was *checked*, not just what failed, and a report that's
only a list of problems is both less useful and less credible than one that also shows
what the site got right.

**Where:**
- [backend/agents/headers.py](../../backend/agents/headers.py) — filled in (was an
  empty scaffold file)
- [docs/ROADMAP.md](../ROADMAP.md) — marked A4 `[x]`, "Current position" now points at
  A5
- [docs/learning/04-headers-and-first-live-request.md](learning/04-headers-and-first-live-request.md)
  — new learning note: HTTP response headers from scratch (raw `curl -I` output),
  `httpx.AsyncClient`/`async with`/`await client.get()`, `httpx.Headers`
  case-insensitivity (proven with a standalone dict-vs-Headers comparison),
  `follow_redirects`, why every check returns a Finding on both PASS and FAIL, and
  keyword-only arguments (`*` in `_check`'s signature) with a non-security standalone
  example (`make_box`).
- Memory (not part of this repo — lives in the Claude memory store):
  `sentinels-frontend-design-first` — new feedback memory recording your instruction
  to pause before any frontend work in this project until you've described your
  design.

**Verification:** Ran the agent against two real, live sites (not mocked, not
simulated) and printed every finding:
- `https://example.com` → all 4 checks `FAIL` (it genuinely sets none of these
  headers) — evidence read `"No 'content-security-policy' header in the response."`
  etc.
- `https://github.com` → all 4 checks `PASS`, with real captured values, e.g.
  `strict-transport-security: max-age=31536000; includeSubdomains; preload` and
  `x-frame-options: deny`.

Then verified the two claims the learning note makes as fact, before writing them down:
- Pointed the agent at an unresolvable domain
  (`https://this-domain-does-not-exist-zzzz.invalid`) and confirmed A3's crash-proofing
  fires for the first time against a *real* failure: `result.error` came back
  `"ConnectError: [Errno 11001] getaddrinfo failed"`, `result.findings == []`, no
  traceback, process kept running.
- Called `HeadersAgent._check(...)` positionally (skipping the keyword-only arguments)
  and confirmed it raises `TypeError: _check() takes 2 positional arguments but 5 were
  given` immediately at the call site. (Caught and fixed a small mistake here: my first
  draft of the note said "6 were given" before I'd actually run it — corrected to the
  real number, 5, after testing.)

**Other notes:**
- Deliberately did **not** add per-check try/except inside `HeadersAgent` — the one
  network call happens once at the top of `scan()`, and if it fails, A3's `run()`
  wrapper already handles it. No new error-handling code was needed; that was the
  entire point of building `BaseAgent` the way we did.
- `_check` is `@staticmethod` since it never touches `self` — callable directly off the
  class, which is exactly how it was exercised in the keyword-only-argument test above.
- No orchestrator or `/scan` endpoint yet — `HeadersAgent` currently has no caller
  except test scripts. A5 is where a real POST request triggers this.
- Stopping here per your "one task, then talk" instruction. Say "continue" for A5.

---

## 2026-07-29 — A5: Orchestrator v1

**Prompt/trigger:** "ok then go with a5 now" (you'd asked in between whether it was
time for frontend yet — answered: not until A13, 8 backend achievements away).

**What I did:** Built the first true end-to-end request path:
- `ScanRequest` model added to `backend/models.py` — the `{"url": "..."}` JSON body
  shape for `POST /scan`.
- `backend/orchestrator.py` (new file, was an empty scaffold) — `normalize_url()`
  turns whatever a user types (`example.com`, `EXAMPLE.com/`, `https://example.com`)
  into one canonical form, and `run_scan()` normalizes the URL, runs every agent in a
  module-level `AGENTS` list (currently just `HeadersAgent`) against one shared
  `httpx.AsyncClient`, flattens their findings, and builds a `ScanReport`.
- `POST /scan` added to `backend/main.py` — the first `async def` endpoint in the
  project (required, since it awaits `run_scan`, unlike `/health`'s plain `def`).
  Catches `ValueError` from `normalize_url` and turns it into a proper `400` via
  `HTTPException`, using `raise ... from exc` so the original error stays visible in
  the traceback.

**Why:** This is the achievement that actually wires the pieces from A2-A4 into one
real request/response cycle — Checkpoint 1's demo command
(`curl -X POST localhost:8000/scan -d '{"url":"..."}'`) now genuinely works, modulo
score/grade being placeholders. `AGENTS` stores agent *classes*, not instances, so a
fresh instance is built per scan — no state can ever leak between one user's scan and
another's, even though `HeadersAgent` doesn't hold any state today. `score=0`,
`grade="N/A"`, `counts={}` are deliberate placeholders, not shortcuts: A6 is its own
achievement specifically about scoring, with its own concepts and its own note, and
keeping the scoring math out of the orchestrator means A6's scorer can be built and
tested as a pure function completely independent of the HTTP/agent layer.

**Where:**
- [backend/models.py](../../backend/models.py) — added `ScanRequest`
- [backend/orchestrator.py](../../backend/orchestrator.py) — new file: `normalize_url`,
  `run_scan`, `AGENTS`
- [backend/main.py](../../backend/main.py) — added `POST /scan`
- [docs/ROADMAP.md](../ROADMAP.md) — marked A5 `[x]`, "Current position" points at A6
- [docs/learning/05-orchestrator-and-the-scan-endpoint.md](learning/05-orchestrator-and-the-scan-endpoint.md)
  — new learning note: URL normalization (`urlsplit`/`urlunsplit`), the full request
  lifecycle and why 422 vs 400 mean different things, `HTTPException` +
  `raise ... from exc` exception chaining, the first required `async def` endpoint,
  Pydantic `response_model` validation, and a flattening nested list comprehension.

**A real bug, found by testing, not by re-reading the code:** My first version of the
scheme-detection regex was `^https?://` — meant to answer "does this URL already have a
scheme?" so I'd know whether to prepend `https://`. Testing
`{"url": "ftp://example.com"}` exposed the problem: `"ftp://example.com"` doesn't match
`^https?://` (it's neither `http` nor `https`), so the code wrongly concluded "no
scheme present" and prepended `https://` anyway — producing the nonsense URL
`"https://ftp://example.com"`. That string has a non-empty host, so the "no host" check
didn't catch it either; it only surfaced three steps later as a confusing
`ConnectError: getaddrinfo failed` from `httpx`, once it tried to actually connect to
the garbage host. The actual bug (wrong scheme, should be a clean 400) was invisible at
the point where it finally became visible (a DNS failure).

**Fix:** widened the regex to `^[a-zA-Z][a-zA-Z0-9+.-]*://` — matches *any* `word://`
prefix (`ftp`, `javascript`, anything), not just `http`/`https`. Now an unrecognized
scheme is correctly detected as "a scheme is already present," skips the
`https://`-prepending step, and falls through to the explicit
`if parsed.scheme not in ("http", "https"): raise ValueError(...)` check — producing a
clean `400 {"detail": "Unsupported URL scheme: 'ftp'"}` instead of a mangled URL and an
indirect network error.

**Verification:** Ran the full server and hit `/scan` with five real HTTP POSTs,
covering each stage of the request lifecycle described in the note:
1. Golden path (`"EXAMPLE.com"`, no scheme, mixed case) → `200`, normalized to
   `https://example.com/`, 4 real findings, no agent errors.
2. `"ftp://example.com"` → **originally** `200` with a mangled URL and a buried
   `ConnectError` (the bug above); **after the fix**, `400 {"detail": "Unsupported URL
   scheme: 'ftp'"}`.
3. `"   "` (whitespace only) → `400 {"detail": "URL is empty"}`.
4. `{}` (no `url` key at all) → `422`, Pydantic's automatic body validation, confirming
   our endpoint code never even runs for a malformed request shape.
5. An unresolvable domain → `200`, with the failure captured gracefully inside
   `agents[0].error` (`ConnectError: ...`), `findings: []` — the A3 crash-proofing
   contract holding up through two more layers of the stack now.
6. Also confirmed `/openapi.json` picked up `/scan` correctly.
7. Additionally, to verify the note's `response_model` claim rather than assert it:
   temporarily made `run_scan()` return a plain `{"url": url}` dict instead of a real
   `ScanReport`, restarted the server, and confirmed FastAPI rejected it with a real
   `500` and a `fastapi.exceptions.ResponseValidationError` listing exactly the four
   missing fields (`scanned_at`, `duration_ms`, `score`, `grade`) — then restored the
   file from a backup taken before the edit and re-confirmed `/scan` works normally
   again (`200`, 4 findings, `grade: N/A`) before moving on.
8. Killed every background uvicorn process spawned during testing; none left running.

**Other notes:**
- `timeout=10.0` on the shared `httpx.AsyncClient` in `run_scan` is deliberate — without
  it, a hung target site could block a scan indefinitely.
- No frontend touched — per your standing instruction (saved as the
  `sentinels-frontend-design-first` memory), that waits until A13 and until you've
  described your design.
- Stopping here per your "one task, then talk" instruction. Say "continue" for A6.

---

## 2026-07-29 — A6: Scoring

**Prompt/trigger:** "continue with a6 and dont forget you have to stop before
frontend" — explicit reminder of the standing frontend-design-first instruction, which
doesn't apply yet (A7-A12 are still backend; frontend starts at A13).

**What I did:** Wrote `backend/scoring.py` — three pure functions:
- `calculate_score(findings)` — starts at 100, subtracts `SEVERITY_PENALTY[severity]`
  (from `models.py`, A1) for every finding whose status isn't `PASS`, clamped to a
  floor of 0.
- `grade_for_score(score)` — maps the 0-100 score to a letter grade (A ≥90, B ≥80,
  C ≥70, D ≥60, else F) via a fixed, explicit threshold table.
- `count_by_severity(findings)` — a dict with all five severity levels always present
  as keys (zero-filled up front), incremented for every non-passing finding.

Then wired all three into `orchestrator.py`'s `run_scan()`, replacing the `score=0`,
`grade="N/A"`, `counts={}` placeholders from A5 with the real computed values.

**Why:** CLAUDE.md's rule ("scoring stays deterministic — no model in the loop") is
enforced structurally here, not just by care: `scoring.py` imports nothing but
`models.py` — no `httpx`, no `datetime`, no `anthropic` — so there is nothing in the
file *capable* of producing a different answer for the same input twice. Splitting
this out from A5's orchestrator (rather than computing score inline there) means these
functions can be tested completely offline, with fabricated findings, and the same
functions run unmodified inside a real request.

WARN and FAIL findings are treated identically for scoring purposes (both deduct their
severity's full penalty) — deliberately, so `severity` stays the single number that
answers "how much does this cost," rather than needing severity and status reasoned
about together.

**Where:**
- [backend/scoring.py](../../backend/scoring.py) — new file (was an empty scaffold):
  `calculate_score`, `grade_for_score`, `count_by_severity`
- [backend/orchestrator.py](../../backend/orchestrator.py) — `run_scan()` now calls
  the three scoring functions instead of using placeholders
- [docs/ROADMAP.md](../ROADMAP.md) — marked A6 `[x]`, noted **Checkpoint 1 reached**,
  "Current position" now points at A7 with the frontend-design reminder repeated
- [docs/learning/06-scoring.md](learning/06-scoring.md) — new learning note: pure
  functions (contrasted against `add_with_bonus`, an impure function using
  `random.randint`), clamping (`max(0, ...)`, illustrated with a battery-percentage
  example), dict comprehensions and aggregation (a vote-tallying example), and
  generator expressions inside `sum(...)`.

**Verification:** Two layers, deliberately — pure-function tests first (fast, no
network, exhaustive), then a live end-to-end sanity check (proves the wiring, not just
the math):

*Pure-function tests, run directly against fabricated `Finding` objects:*
- All-pass findings → score 100, grade A, all-zero counts dict.
- The exact shape of `example.com`'s real findings (2 High FAIL + 2 Medium FAIL) →
  score 54, grade F, counts `{Critical:0, High:2, Medium:2, Low:0, Info:0}` — computed
  *before* touching the network, then cross-checked against the live result below.
- Five fabricated Critical/FAIL findings (125 raw penalty) → clamped to exactly 0, not
  a negative number.
- One Low/WARN finding → score 97 — confirms WARN costs the same as FAIL at the same
  severity.
- Called `calculate_score` on the same input 50 times in a loop and collected results
  in a `set()` — exactly one distinct value came back, confirming determinism directly
  rather than assuming it.
- All ten grade-boundary values (100, 90, 89, 80, 79, 70, 69, 60, 59, 0) checked
  individually against their expected letter grade — including the two knife-edge
  cases (90 is exactly an A, not a B; 89 is exactly a B, not an A) where an off-by-one
  would be silent and easy to miss.

*Live, end-to-end, through the actual running server:*
- `POST /scan` for `example.com` → `54, F`, matching the pure-function prediction
  exactly.
- `POST /scan` for `github.com` → `100, A`.
- Scanned `example.com` twice in a row over a real network round-trip → identical
  score both times (`54` both runs) — determinism holding up outside the unit-test
  sandbox too, not just in isolation.
- Killed the test server afterward.

*Also verified before writing it into the note as fact:* deliberately reversed the
`_GRADE_THRESHOLDS` list to `[(60,"D"), (70,"C"), (80,"B"), (90,"A")]` and confirmed
`grade_for_score(95)` really does return `"D"` — proving the "check highest threshold
first" ordering is load-bearing, not cosmetic, before describing it that way in the
note.

**Other notes:**
- **Checkpoint 1 (from the roadmap) is now reached**: `curl -X POST localhost:8000/scan
  -d '{"url":"https://example.com"}'` returns a complete, real, graded report. That's
  the end of Act 1.
- No frontend work done or started — correctly out of scope until A13, per your
  standing instruction.
- Stopping here per your "one task, then talk" instruction. Say "continue" for A7 (Act
  2 begins: the Recon agent, and HTML parsing via BeautifulSoup).

---

## 2026-07-29 — A7: Recon agent

**Prompt/trigger:** "do a7"

**What I did:** Wrote `backend/agents/recon.py` — `ReconAgent`, the second real agent
(Act 2's first achievement), and registered it in `orchestrator.py`'s `AGENTS` list
alongside `HeadersAgent`. It runs two passive checks:
- **Generator meta tag** — parses the homepage HTML with BeautifulSoup, looking for
  `<meta name="generator" content="...">`. If present, `WARN`/`Low` ("version
  disclosure helps an attacker target known CVEs for that exact version"); if absent,
  `PASS`/`Info`.
- **robots.txt** — fetches `/robots.txt` (via `urljoin`, so it always hits the domain
  root regardless of which path was scanned) and checks `Disallow` entries against a
  short sensitive-keyword list (`admin`, `login`, `wp-admin`, `config`, `.env`, etc.).
  Sensitive matches → `WARN`/`Low`; a clean or missing robots.txt → `PASS`/`Info`.

**Why:** This is the first HTML parsing in the project (BeautifulSoup, `html.parser`)
and the first time a second agent joins the pipeline — a genuine test that A5's
`AGENTS` list design (store classes, loop and instantiate, no other code changes
needed) actually holds up in practice, not just in theory. Both checks are single GETs
to paths meant to be publicly fetched (the homepage a browser loads anyway;
`robots.txt`, whose entire purpose is being read by any crawler), staying inside
CLAUDE.md's passive-only scope.

**Where:**
- [backend/agents/recon.py](../../backend/agents/recon.py) — new file (was empty
  scaffold): `ReconAgent`, `_check_generator`, `_check_robots_txt`
- [backend/orchestrator.py](../../backend/orchestrator.py) — `AGENTS` now
  `[HeadersAgent, ReconAgent]`
- [docs/ROADMAP.md](../ROADMAP.md) — marked A7 `[x]`, "Current position" points at A8
- [docs/learning/07-recon-agent-and-html-parsing.md](learning/07-recon-agent-and-html-parsing.md)
  — new learning note: BeautifulSoup (`.find` vs `.find_all`, `html.parser` vs `lxml`
  and why we're stuck with the former per `requirements.txt`), what `robots.txt`
  actually is and why a security auditor cares about a file that's *supposed* to be
  public, `urljoin` vs. string concatenation for building the robots.txt URL, and the
  `dict.fromkeys` deduplication idiom.

**A real bug, found by testing against a genuinely complex real site:** first version
of the robots.txt parser collected every `Disallow:` line with no deduplication.
Testing against `github.com` produced `/account-login` listed **four times** in one
finding's evidence. I fetched GitHub's actual `robots.txt` to check whether this was a
parsing bug or real content — it's real: GitHub's file has separate `User-agent:`
blocks for `bingbot`, `adidxbot`, `BingPreview`, and `*`, and several of them
independently disallow the same path. My parser was reading the file correctly; the
gap was that nothing merged the same path appearing under different user-agent
sections, which real `robots.txt` files do constantly and legitimately. Fixed with
`disallowed = list(dict.fromkeys(disallowed))` — dedupes while preserving first-seen
order (unlike `set()`, which dedupes but not deterministically-orderedly), which also
matters for the project's "same site → same output" determinism goal at the evidence-
text level, not just the score.

**Verification:**
- Ran `ReconAgent` directly (no server) against `example.com` (neither check trips —
  `PASS`/`PASS`), `github.com` (robots.txt trips, no generator tag), and
  `wordpress.org` (both trip — real generator tag `WordPress 7.1-beta3-62896` found).
- Found the duplication bug on the `github.com` run; fetched the real robots.txt
  directly with `curl`-equivalent httpx to confirm it was genuine repeated content, not
  a parser mistake, before deciding how to fix it; re-ran after the fix and confirmed
  `/account-login` now appears exactly once.
- Ran the full pipeline live through `POST /scan` for `wordpress.org`: both agents
  ran (`headers`: 4 findings, `recon`: 2 findings, both `error: None`), 6 total
  findings, scored `71/C` — manually cross-checked the arithmetic
  (100 − 15 High − 8 Medium − 3 Low − 3 Low = 71) against A6's scoring rules and
  confirmed it matches exactly.
- Re-confirmed A3's crash-proofing holds for the new agent too: pointed `ReconAgent`
  at an unresolvable domain directly, got a clean `AgentResult` with
  `error: 'ConnectError: ...'` and `findings: []`, no crash.
- Killed the background server process after testing.

**Other notes:**
- No frontend work — still correctly out of scope until A13.
- Stopping here per your "one task, then talk" instruction. Say "continue" for A8 (TLS
  agent — certificate expiry via raw sockets and stdlib `ssl`).

---

## 2026-07-29 — A8: TLS agent

**Prompt/trigger:** "continue with a8"

**What I did:** Wrote `backend/agents/tls.py` — `TLSAgent`, the third real agent,
registered in `orchestrator.py`'s `AGENTS` list. Unlike the previous agents it doesn't
use `httpx`/HTTP at all: it opens a raw TCP socket and performs a real TLS handshake
via the stdlib `ssl` module. Three checks:
- If the scanned URL isn't `https://` at all → immediate `Critical`/`FAIL`
  ("Site is not served over HTTPS"), no connection attempted.
- Otherwise, a verified handshake (`ssl.create_default_context()`, matching browser
  behavior — trusted-CA chain + hostname check). If that raises `ssl.SSLError` →
  one `Critical`/`FAIL` finding ("TLS certificate could not be verified") using the
  real OpenSSL reason text as evidence.
- If the handshake succeeds: certificate expiry (`WARN` if ≤30 days left, else
  `PASS`) and negotiated protocol version (`FAIL`/`High` if TLS 1.0/1.1/SSLv2/v3,
  else `PASS`).

Because `socket`/`ssl` are blocking APIs with no async equivalent, the actual
handshake (`_fetch_certificate`, a plain synchronous function) runs via
`asyncio.to_thread(...)`, keeping the event loop free for the other agents.

**Why:** `stdlib ssl` + sockets is the roadmap's explicit ask, and it's also the first
agent that genuinely can't reuse `httpx`. Getting the async story right here matters
architecturally, not just stylistically — a blocking call made directly inside
`async def scan()` would freeze the *entire* event loop for the whole handshake,
silently breaking A11's "five agents concurrently" goal for every agent, not just this
one. Catching only `ssl.SSLError` (not a broad `except Exception`) was a deliberate,
narrower choice than A3's `run()` wrapper: an SSL verification failure is a genuine,
reportable fact about this site's TLS setup, worth turning into a Finding; a DNS
failure or refused connection is a different kind of problem already handled one
layer up by `run()`, and conflating the two would mean this agent's own error handling
starts doing A3's job for it.

**Where:**
- [backend/agents/tls.py](../../backend/agents/tls.py) — new file (was empty
  scaffold): `_fetch_certificate` (blocking helper), `TLSAgent`
- [backend/orchestrator.py](../../backend/orchestrator.py) — `AGENTS` now
  `[HeadersAgent, ReconAgent, TLSAgent]`
- [docs/ROADMAP.md](../ROADMAP.md) — marked A8 `[x]`, "Current position" points at A9
- [docs/learning/08-tls-agent-and-sockets.md](learning/08-tls-agent-and-sockets.md) —
  new learning note: sockets from scratch (a raw hand-written HTTP request over a
  plain socket, no `httpx`), the TLS handshake and `ssl.create_default_context()`,
  `asyncio.to_thread` (with a standalone `time.sleep`-vs-`asyncio.sleep` proof before
  the real-code proof), why `ssl.SSLError` is caught narrowly here instead of broadly,
  `strptime` date parsing, and a note on the difference between "scoring is
  deterministic" (A6, still true) and "the world changes over time" (also true, and
  not a contradiction — illustrated with a real, live coincidence, see below).

**Verification, entirely against real infrastructure, no mocking:**
- `example.com`, `github.com` → both `PASS`/`PASS`, real expiry dates
  (`Aug 29 21:41:26 2026 GMT`, 31 days out; `Sep 30 23:59:59 2026 GMT`, 63 days out)
  and `TLSv1.3` negotiated.
- `expired.badssl.com`, `self-signed.badssl.com`, `wrong.host.badssl.com` — a public
  test service purpose-built for exactly this — each correctly raised
  `ssl.SSLCertVerificationError` with the real, distinct OpenSSL reason text
  ("certificate has expired", "self-signed certificate", "Hostname mismatch, ..."),
  each turned into the correct `Critical`/`FAIL` finding.
- `http://example.com` (plain HTTP target) → immediate `Critical`/`FAIL`, no network
  connection attempted for the TLS check.
- Confirmed the exception-type split is real, not assumed: pointed the raw
  `socket.create_connection` at an unresolvable domain and got `socket.gaierror`, a
  completely different exception type from `ssl.SSLError` — confirms the narrow
  `except ssl.SSLError` correctly leaves DNS/connection failures to propagate to A3's
  `run()` wrapper instead of misclassifying them as a TLS problem.
- **Directly proved the non-blocking claim, not just assumed it**: ran `TLSAgent().run()`
  (a real ~430ms handshake against `github.com`) concurrently with a small counting
  task via `asyncio.gather` — the counter ticked 17 times during that same window,
  confirming the event loop stayed responsive throughout the real, blocking socket
  work.
- Ran the full 3-agent pipeline live through `POST /scan` for `github.com`: all three
  agents (`headers`, `recon`, `tls`) returned `error: None`, 8 total findings, scored
  `97/A` (one Low/WARN from `recon`'s robots.txt check, matching A6's rules exactly).
- Also verified both new standalone examples in the learning note (a raw hand-written
  HTTP GET over a bare socket; the `blocking_nap`/`ticker` proof) actually run and
  produce the described output before writing them into the note as fact.
- Killed the background server process after testing.

**A real, notable observation (not a bug):** `example.com`'s live certificate expires
in exactly 31 days as of this writing — one day outside the 30-day warning threshold,
so it currently reports `PASS`. This will very likely flip to `WARN` within the next
few weeks with zero code changes. Flagged explicitly in the note as an illustration
that "scoring is deterministic" (A6) and "findings reflect a changing real world" are
not in tension — the rule connecting facts to a score never changes; the facts
themselves are allowed to.

**Other notes:**
- No frontend work — still correctly out of scope until A13.
- Stopping here per your "one task, then talk" instruction. Say "continue" for A9
  (Exposure agent — detecting a publicly exposed `/.env`, safely).

---

## 2026-07-29 — A9: Exposure agent

**Prompt/trigger:** "continue with a9"

**What I did:** Wrote `backend/agents/exposure.py` — `ExposureAgent`, the fourth real
agent, registered in `orchestrator.py`'s `AGENTS` list. Two checks, each a single GET
to a fixed, well-known path:
- `/.env` — flagged `Critical`/`FAIL` only if the response is `200`, its
  `Content-Type` isn't HTML, **and** its body actually contains `KEY=VALUE`-shaped
  lines (a regex with `re.MULTILINE`). All three conditions, not just status code.
- `/.git/HEAD` — flagged `High`/`FAIL` only if `200` **and** the body matches a real
  git HEAD file's exact shape (`starts with "ref:"`).

Both checks use `follow_redirects=False` (a deliberate reversal of every prior agent's
choice) and `urljoin(context.url, "/...")` to always hit the domain root.

**Why:** This achievement is specifically about where a *passive* scanner's ethical
line sits, per CLAUDE.md's explicit scope rule (GET to public paths; no fuzzing, no
wordlists, no brute force). Two fixed, well-known paths — not a guessed list — is what
keeps this on the safe side of that line. The content-shape verification (not just
status code) exists because plenty of real sites return `200` for every path
("soft-404" behavior, common with SPAs and custom error pages) — a naive
status-code-only checker would flag nearly all of them as leaking secrets. And the
evidence field deliberately never echoes a genuinely-found file's actual content — if
this agent ever does find a real exposed `.env`, showing the literal secret text in
our own report would just create a second copy of the leak, in a place (a saved,
shareable report) more likely to spread further than the original.

**Where:**
- [backend/agents/exposure.py](../../backend/agents/exposure.py) — new file (was
  empty scaffold): `_check_env_file`, `_check_git_head`
- [backend/orchestrator.py](../../backend/orchestrator.py) — `AGENTS` now
  `[HeadersAgent, ReconAgent, TLSAgent, ExposureAgent]`
- [docs/ROADMAP.md](../ROADMAP.md) — marked A9 `[x]`, "Current position" points at A10
- [docs/learning/09-exposure-agent-and-ethical-limits.md](learning/09-exposure-agent-and-ethical-limits.md)
  — new learning note: ethical probing limits (a table contrasting what this agent
  does vs. what would cross into fuzzing), HTTP status code *categories* as a signal,
  the soft-404 false-positive problem demonstrated with real local fixtures, and the
  "don't leak the secret you just found" evidence-handling principle.

**Verification, using controlled local fixtures rather than probing real third-party
sites for actual secrets (a deliberate ethical choice — searching for a real exposed
`.env` on a random production site would itself cross the line this achievement is
about):**
- Built a local fixture server (`http.server` on `127.0.0.1`) serving a fabricated,
  clearly-dummy `.env` (`DB_PASSWORD=fake-dummy-value-not-real`, etc.) and
  `.git/HEAD` (`ref: refs/heads/main`). `ExposureAgent` correctly flagged both:
  `Critical`/`FAIL` for `.env`, `High`/`FAIL` for `.git/HEAD`.
- Built a second local fixture — a custom handler returning `200` + HTML for
  *every* path, simulating soft-404 behavior — and confirmed `ExposureAgent`
  correctly reported `PASS` for both checks despite the `200` status, proving the
  content-shape verification does real work.
- Ran against two real, clean production sites (`example.com`, `github.com`) — both
  correctly `PASS`/`PASS` (real `404`s).
- Ran the full 4-agent pipeline live through `POST /scan` for `github.com`: all four
  agents (`headers`, `recon`, `tls`, `exposure`) returned `error: None`, scored
  `97/A`.
- Chased down what looked like a real encoding bug: the evidence text's em-dash
  characters displayed as `�` in this terminal. Checked the source file's raw bytes
  (correct UTF-8), then checked the actual JSON payload's raw bytes directly (also
  correct UTF-8, real em-dash present, no replacement character) — confirmed this was
  purely a terminal display limitation in this specific bash environment, not a real
  data bug, before concluding anything was wrong.
- While writing the learning note's "try this" section, caught my own inaccurate claim
  before publishing it: my first draft said removing the content-type check from
  `_check_env_file` would reproduce a false positive against the HTML soft-404
  fixture — tested it directly and it doesn't (that fixture's HTML body has no `=`
  character, so the regex condition alone still correctly excludes it). Rewrote the
  experiment to use a `text/plain` soft-404 body instead, verified *that* correctly
  demonstrates the regex condition catching what the content-type check alone would
  miss, and rewrote the note's explanation to reflect that the two conditions guard
  against two different false-positive shapes, not the same one twice.
- Killed both local fixture servers and the test uvicorn instance after testing.

**Other notes:**
- No frontend work — still correctly out of scope until A13.
- Stopping here per your "one task, then talk" instruction. Say "continue" for A10
  (DNS agent — SPF/DMARC, whether a domain can be spoofed).

---

## 2026-07-29 — A10: DNS agent

**Prompt/trigger:** "go for a10 and again reminding one more time that dont start
front end" — reconfirmed the standing frontend-design-first instruction (still
correctly not applicable; A11-A12 remain backend).

**What I did:** Wrote `backend/agents/dns_email.py` — `DNSAgent`, the fifth and final
Act 2 agent, registered in `orchestrator.py`'s `AGENTS` list. Two checks, both DNS TXT
lookups via `dnspython`, run off the event loop via `asyncio.to_thread` (same pattern
as A8's blocking `ssl`/`socket` calls):
- **SPF** — reads the domain's own TXT records, finds the one starting with
  `v=spf1`, and classifies its trailing `all` qualifier: `-all`/`~all` → `PASS`,
  `?all` → `WARN`, `+all` (or bare `all`) → `FAIL` ("allows any server"), no `all` at
  all → `WARN`. A `redirect=` mechanism (delegating the whole policy to another
  domain's SPF record) is checked for and treated as `PASS` on its own, before the
  `all`-search even runs.
- **DMARC** — reads `_dmarc.<domain>`'s TXT records, finds the one starting with
  `v=DMARC1`, and reads its `p=` policy tag: `quarantine`/`reject` → `PASS`, `none` (or
  missing) → `WARN`. Absent entirely (either DNS exception) → `FAIL` for both checks.

**Why:** This is the last of the five Act 2 agents, and — per the roadmap — the one
answering "can this domain's email be spoofed." SPF and DMARC are the two canonical,
industry-standard DNS records that answer exactly that question, and reading them
requires nothing beyond ordinary public DNS lookups, the same reads any real mail
server performs before accepting a message. `dnspython`'s resolver is blocking
(no async API), so this reuses A8's `asyncio.to_thread` pattern rather than
introducing a new one.

**Where:**
- [backend/agents/dns_email.py](../../backend/agents/dns_email.py) — new file (was
  empty scaffold): `_query_txt`, `_find_record`, `DNSAgent`
- [backend/orchestrator.py](../../backend/orchestrator.py) — `AGENTS` now all five:
  `[HeadersAgent, ReconAgent, TLSAgent, ExposureAgent, DNSAgent]`
- [docs/ROADMAP.md](../ROADMAP.md) — marked A10 `[x]`, noted Checkpoint 2 is next,
  "Current position" points at A11
- [docs/learning/10-dns-agent-and-email-spoofing.md](learning/10-dns-agent-and-email-spoofing.md)
  — new learning note: TXT records as a general-purpose publishing mechanism (not
  just SPF/DMARC), SPF qualifiers grounded in RFC 7208, DMARC policy tags, and both
  real-world surprises below, worked through as genuine "found by testing" moments
  rather than presented as pre-known facts.

**Two genuine real-world discoveries, found by testing against real major domains
before finalizing the design — not caught by re-reading the code:**
1. `gmail.com`'s SPF record is `"v=spf1 redirect=_spf.google.com"` — no `all`
   mechanism at all. My first design would have classified any SPF record lacking an
   `all` token as `WARN` ("no enforcement mechanism"), which would have been *wrong*
   here: `redirect=` is a real, valid SPF mechanism meaning "evaluate this other
   domain's SPF record instead," and Google uses it on its own flagship domain. Fixed
   by checking for `redirect=` explicitly and treating it as its own `PASS` case,
   before the `all`-token search runs at all.
2. Both `gmail.com` and `python.org` — real, large, competently-run domains — have
   DMARC `p=none` on their apex domain, meaning spoofed mail failing SPF is still
   delivered normally. Not a bug in the check; a real, accurate, slightly surprising
   fact about the current state of email authentication in the wild, kept in the note
   as a genuine finding rather than smoothed over.

**Verification:**
- Directly probed real DNS data for 9+ domains *before* finalizing the classification
  logic (`example.com`, `github.com`, `gmail.com`, `python.org`, `example.org`,
  `example.net`, `wordpress.org`, `badssl.com`), specifically to observe real record
  formats (spacing variations around `p=`, multiple unrelated TXT records per domain,
  the `redirect=` mechanism) rather than designing from the spec alone.
- Specifically hunted for a real domain lacking DMARC to determine whether the
  failure mode is `NXDOMAIN` or `NoAnswer` (needed to know which exceptions to catch)
  — found `_dmarc.badssl.com` returns `NoAnswer`, confirming both exception types
  needed to be treated as "absent."
- Ran `DNSAgent` against 5 real domains chosen to hit every classification branch:
  `example.com` (PASS/PASS, `-all`/`p=reject`), `github.com` (PASS/PASS, `~all`/
  `p=quarantine`), `gmail.com` (PASS/WARN, `redirect=`/`p=none`), `python.org`
  (PASS/WARN, `~all`/`p=none`), `badssl.com` (FAIL/FAIL, neither record exists) — all
  five matched predictions made from the raw DNS data before running the agent.
- Confirmed graceful (non-crashing) behavior against a fully nonexistent domain —
  `NXDOMAIN` is caught inside `_query_txt` itself, so this agent (unusually, compared
  to Headers/TLS) reports a clean `FAIL`/`FAIL` ("no record found") rather than an
  `AgentResult.error`, which is accurate rather than a bug: there genuinely is no SPF/
  DMARC record for a domain that doesn't exist.
- Ran the full 5-agent pipeline live through `POST /scan` for `github.com`: all five
  agents returned `error: None`, 12 total findings, scored `97/A`. Noted the total
  scan duration is now ~2.2 seconds, sequential — set up explicitly as the number
  A11's concurrency work will improve.
- Verified the note's "reproduce the redirect= mistake" experiment (moving the
  `redirect=` check after the `all`-token search) actually produces the wrong `WARN`
  classification, before writing it into the note as fact.
- Killed the test server after use.

**Other notes:**
- No frontend work — still correctly out of scope until A13.
- Stopping here per your "one task, then talk" instruction. Say "continue" for A11
  (Parallel — `asyncio.gather`, Checkpoint 2).

---

## 2026-07-29 — A11: Parallel (Checkpoint 2)

**Prompt/trigger:** "go for a11"

**What I did:** Changed exactly one thing in `backend/orchestrator.py`'s
`run_scan()`: replaced the sequential list comprehension
(`[await agent_cls().run(context) for agent_cls in AGENTS]`) with
`await asyncio.gather(*(agent_cls().run(context) for agent_cls in AGENTS))`. No
individual agent needed any change — every agent's `run()` (from A3) already
returned an awaitable coroutine; only the orchestrator's *shape of waiting* changed.

**Why:** This is Checkpoint 2 on the roadmap: prove 5 agents take as long as the
slowest, not the sum. `asyncio.gather` starts all five coroutines together and lets
the event loop interleave their waiting time, instead of waiting for each one to fully
finish before starting the next.

**Where:**
- [backend/orchestrator.py](../../backend/orchestrator.py) — `run_scan()`'s agent-
  running line, plus `import asyncio` added
- [docs/ROADMAP.md](../ROADMAP.md) — marked A11 `[x]`, Checkpoint 2 marked REACHED
  with the real measured numbers, "Current position" points at A12
- [docs/learning/11-parallel-agents-and-asyncio-gather.md](learning/11-parallel-agents-and-asyncio-gather.md)
  — new learning note: concurrency vs. parallelism (a kettle/toaster standalone
  example), `asyncio.gather` applied to the real agent list, why individual agent
  durations rose slightly under concurrency (resource contention) without undermining
  the total-time proof, and why bare `gather` (no `return_exceptions=True`) is safe
  here specifically because of A3's `run()` guarantee — demonstrated by deliberately
  breaking that guarantee (see below).

**Verification — real, measured, before and after, not simulated:**
- Captured a **real sequential baseline** using the actual pre-change code, against
  `github.com`: total `2597ms`, sum of the five agents' individual durations
  `2594ms` — total tracked the sum, as expected for one-at-a-time execution.
- Made the `asyncio.gather` change, then measured the **same site** again: total
  `1308ms` (via a standalone script) and `1520ms` (via the real running `/scan`
  endpoint, second measurement) — both times tracking the slowest single agent
  (`1297ms` / `1155ms`), not the sum (`4131ms` / `3662ms`).
- Ran the sequential-vs-parallel comparison a further time, back-to-back in one
  script, confirming the pattern holds: sequential `2749ms` total vs. sum `2746ms`;
  parallel `824ms` total vs. max `823ms` — the same relationship every time, exact
  milliseconds varying with real network conditions as expected.
- Noticed and explicitly investigated something worth being honest about rather than
  smoothing over: individual agent durations were *higher* when run concurrently
  (e.g. `headers` went from 697ms alone to 868-1043ms alongside the others) —
  confirmed this is expected resource contention (shared connection pool, shared
  thread pool for `to_thread`, shared network interface), not a bug, and that it
  doesn't undermine the actual proof (total time collapsing toward the max, not the
  sum).
- **Deliberately proved the `return_exceptions=True` question empirically rather than
  asserting it**: built a `BrokenAgent` that raises directly from a bare `scan()` call
  (bypassing `BaseAgent.run()`'s crash-proofing on purpose), ran it inside
  `asyncio.gather` alongside two real agents going through their real, safe `run()` —
  confirmed the whole call raised and **both successful agents' results were lost**.
  Then re-ran the identical three coroutines with `return_exceptions=True` added and
  confirmed the exception instead came back as a plain item in the results list,
  alongside the two successful `AgentResult`s. This is the concrete demonstration for
  why the real codebase doesn't need `return_exceptions=True`: A3's `run()` already
  guarantees no real agent can raise, so the failure mode `return_exceptions=True`
  protects against has already been designed out one layer down.
- Killed the test server after use.

**Other notes:**
- No frontend work — still correctly out of scope until A13. This was the last
  reminder point before A13 arrives; A12 (next) is the final backend-only
  achievement before frontend work begins.
- Stopping here per your "one task, then talk" instruction. Say "continue" for A12
  (AI analyst — Claude API summary with graceful no-key fallback).

---

## 2026-07-29 — A12: AI analyst (backend complete)

**Prompt/trigger:** "go for it (a12)"

**What I did:** Wrote `backend/ai/analyst.py` — a single `summarize(url, score, grade,
findings)` function, the only place in the whole project a language model is used.
Calls the Claude API (`claude-haiku-4-5-20251001`, chosen deliberately over a larger
model since this is a short structured-summarization task on data that's already
fully computed) with a system prompt instructing plain-English, non-expert-readable
output (2-4 sentences, no markdown, name the worst problem), and a user message built
from the scan's URL/score/grade/findings (capped at 15 findings, to bound token cost).
Wired into `orchestrator.py`'s `run_scan()` right after scoring; added
`from dotenv import load_dotenv; load_dotenv()` near the top of `main.py` so a real
`backend/.env` (if one ever exists) gets loaded into `os.environ` at startup.

**Why:** CLAUDE.md's explicit rule for this achievement: *"The AI layer only enriches.
If ANTHROPIC_API_KEY is missing or the call fails, the scan must still produce a
complete report."* Every design choice here serves that one guarantee: the missing-key
case gets its own explicit early `return ""` (not folded silently into the `try`
block, so the guarantee is visible on the page, not just true by accident of scoping),
and the `try/except Exception: return ""` around the actual API call is deliberately
broad — unlike A8's `TLSAgent`, which deliberately caught narrowly. The two are
consistent, not contradictory: A8 needed to distinguish "a fact about this site's TLS"
from "a connectivity problem," because those need different treatment; here, *every*
possible failure (bad key, rate limit, timeout, network blip, unexpected response
shape) has the exact same correct response — blank summary, keep everything else — so
there's nothing to gain from distinguishing them and real risk in missing one.

**Where:**
- [backend/ai/analyst.py](../../backend/ai/analyst.py) — new file (was empty
  scaffold): `_build_prompt`, `summarize`
- [backend/orchestrator.py](../../backend/orchestrator.py) — `run_scan()` now calls
  `summarize()` and sets `ScanReport.summary` with the real (or empty) result
- [backend/main.py](../../backend/main.py) — added `load_dotenv()` near the top
- [docs/ROADMAP.md](../ROADMAP.md) — marked A12 `[x]`, noted Act 1 & 2 are fully
  complete and the backend is feature-complete, "Current position" now points at A13
  with the frontend-design-first instruction restated
- [docs/learning/12-ai-analyst-and-graceful-degradation.md](learning/12-ai-analyst-and-graceful-degradation.md)
  — new learning note: graceful degradation (a non-security `get_display_name`
  standalone example), the Messages API shape (system vs. messages, why
  `response.content` is a list), prompt design reasoning (why each system-prompt
  sentence exists), `os.environ`/`load_dotenv`, and the deliberate broad-vs-narrow
  exception-handling contrast against A8.

**Verification — and an explicit, honest limit on what could be verified:**
- Confirmed this dev environment currently has **no** `ANTHROPIC_API_KEY` anywhere
  (no `backend/.env` file, nothing in the shell environment) — meaning the "missing
  key" path wasn't simulated, it's this project's actual real current state.
- Called `summarize()` directly with no key present → returned `''`, no exception.
- Set a deliberately invalid key (`sk-ant-definitely-invalid-fake-key...`) and called
  `summarize()` → returned `''`, no exception. Then, separately, called the raw
  `anthropic.AsyncAnthropic` client directly (bypassing our own error handling) with
  the same fake key to see the *actual* real exception before it gets caught:
  confirmed it's a genuine `anthropic.AuthenticationError` — a real `401` response
  from Anthropic's live API (`"invalid x-api-key"`), not a guess about SDK behavior.
- Ran the full `/scan` pipeline end-to-end (real server, real site — `github.com`)
  with no key present: `200`, `summary: ''`, every other field
  (`score`, `grade`, `findings`, `agents`, `counts`) fully populated — 12 real
  findings, scored `97/A`, exactly as before this achievement, proving the AI layer's
  absence changes nothing else.
- Verified `_build_prompt()` directly (no network) against a realistic mixed set of
  findings — confirmed clean, correctly formatted output.
- Inspected the real installed `anthropic` SDK (`0.42.0`) directly — confirmed
  `AsyncAnthropic` exists, confirmed `messages.create`'s actual parameter signature
  (`model`, `max_tokens`, `messages`, `system`, ...) matches what was written, rather
  than assuming API shape from memory.
- **What was NOT verified, stated plainly rather than glossed over**: an actual
  successful call to the real model, producing a real summary paragraph. This
  environment has no working API key, so that specific path rests on the SDK's
  documented method signature and response shape (`response.content[0].text`),
  confirmed structurally, not on an observed live response. Flagged explicitly in
  both the learning note and here, with exact instructions for testing it once a real
  key is available.
- Killed the test server after use.

**Backend is now feature-complete: A1 through A12, all done, all tested end-to-end.**
`POST /scan` returns a real, graded, (optionally AI-enriched) report for any live site
in roughly 1-2 seconds.

**Other notes:**
- No frontend work — and this was the last point where that even needed saying for
  now. A13 is next, and per your standing instruction (saved as the
  `sentinels-frontend-design-first` memory), I'll stop before writing any frontend
  code until you've described your design.

---

## 2026-07-29 — A12 follow-up: swapped Anthropic for Groq, real key added

**Prompt/trigger:** You asked what API keys I needed and why, before I could arrange
anything, then said: *"i want to make it for free i dont have money to pay for
anthropic api key"* and asked if you could give me a Groq API key instead. I confirmed
only one key was ever relevant (the AI analyst, entirely optional) and nothing else in
the project needs one. You then pasted a real Groq key directly in chat
(`gsk_[REDACTED — was committed here in plaintext; redacted 2026-07-30, key rotated]`).

**What I did:**
1. Researched Groq's current API before writing any code (rather than trusting
   possibly-stale memory of "the" default Groq model): confirmed it's an
   OpenAI-compatible REST endpoint
   (`https://api.groq.com/openai/v1/chat/completions`), free tier requires no credit
   card, and — critically — that `llama-3.3-70b-versatile` (a commonly-referenced
   "default" Groq model in older material) was deprecated as of mid-2026, with
   `openai/gpt-oss-20b` as the current free-tier-friendly replacement. Also confirmed
   the current parameter name is `max_completion_tokens` (`max_tokens` still works but
   is the deprecated name).
2. Rewrote [backend/ai/analyst.py](../../backend/ai/analyst.py) to call Groq directly
   via `httpx` (already a project dependency) instead of the `anthropic` SDK — no new
   package needed at all. Same function signature, same graceful-degradation
   guarantee, same prompt design; only the transport and provider changed.
3. Removed `anthropic==0.42.0` from
   [backend/requirements.txt](../../backend/requirements.txt) (genuinely unused now)
   and uninstalled it from the venv to keep the venv matching requirements.txt exactly.
4. Renamed the env var everywhere: `backend/.env.example` now says `GROQ_API_KEY=`;
   [CLAUDE.md](../../CLAUDE.md)'s own rule text ("if `ANTHROPIC_API_KEY` is missing...")
   updated to say `GROQ_API_KEY`, so the project's own instructions stay accurate.
5. Wrote your real key to `backend/.env` — confirmed with
   `git check-ignore -v backend/.env` that it's genuinely gitignored *before* writing
   it to disk, and confirmed `git status` shows nothing after.
6. Substantially rewrote
   [docs/learning/12-ai-analyst-and-graceful-degradation.md](learning/12-ai-analyst-and-graceful-degradation.md)
   to match the real, current code — the old version described Anthropic's
   Messages API in detail, which no longer matches what's actually running. Added an
   explicit new section (1.5) recording the provider swap openly as a real decision,
   not silently editing history.

**Why:** Your reason (no budget for a paid API) is a completely valid constraint, and
Groq's free tier is a good fit for this specific job — a short, cheap, non-reasoning
summarization task run once per scan, which is exactly what a small free-tier model is
good at. Calling Groq via raw `httpx` rather than adding the `groq` or `openai` SDK as
a new dependency keeps the project's dependency list from growing for a single
function, and matches the pattern every other external call in this codebase already
uses.

**Verification — and this time, the full success path, not just the failure paths:**
- Called `summarize()` directly with the real key loaded via `load_dotenv()`: got back
  a real, coherent, correctly-prioritized 239-character summary correctly identifying
  the most severe of three fabricated findings (the `.env` exposure) as the headline
  problem — the actual missing piece from A12's original verification.
- Ran the full `/scan` pipeline live against `github.com` with the real key: `200`,
  score `97/A` (unchanged from before this achievement — confirms the AI layer only
  adds a summary, never touches scoring), and a real 341-character summary that
  accurately named the one real finding that scan produced (the robots.txt warning)
  and correctly characterized the site as "in good shape."
- Hit a console-encoding snag printing the results directly (a non-breaking hyphen,
  `‑`, that this terminal's `cp1252` codepage can't display) — recognized this
  immediately as the same class of issue diagnosed in A9 (a display-only artifact, not
  a real data bug) and wrote output to a UTF-8 file instead of assuming anything was
  broken.
- **Re-verified the no-key path still holds after the swap**, not just assumed it
  still would: temporarily moved `backend/.env` aside, restarted the server, confirmed
  `/scan` against `github.com` still returns `200`, a complete report, `score: 97`,
  `grade: A`, and `summary == ''` — then restored the real `.env` file and confirmed
  its content survived correctly.
- Killed every test server spawned during this verification.

**Other notes:**
- No frontend work — still correctly out of scope until A13 (this was a follow-up to
  an already-completed achievement, not a new one).
- The backend is now not just feature-complete but **verified feature-complete**,
  including the one gap explicitly flagged at the end of the original A12 entry (a
  real successful AI call) — that gap is now closed.
- Ready for A13 whenever you've described your frontend design, per your standing
  instruction.

---

## 2026-07-29 — A13: Next.js up

**Prompt/trigger:** "now start A13" — the frontend-design-first block was cleared first:
three items DESIGN.md had left open (display/mono font pairing, whether the input
screen carries any illustration, landing page vs. straight-to-input) were resolved via
`AskUserQuestion` before any code was written. You picked the recommended option on the
first two (Instrument Serif; fully typographic input) and, on the third, redirected the
question itself: rather than answering landing-vs-input, you said to run A13-A15 in one
pass without stopping for permission between them, keeping one learning note per
achievement. I took the entry-point question as resolved by my own recommendation
(straight to the scan input, no landing layer) since you didn't dispute it — flagged as
an assumption, not silently decided.

**What I did:** Scaffolded `frontend/` with `create-next-app` — Next.js 16.2, React
19.2, Tailwind v4, App Router, TypeScript, no `src/` directory. Before writing any
component, read the Next.js docs bundled in `node_modules/next/dist/docs/` directly
(the scaffold's own generated `AGENTS.md` warns this major version has real breaking
changes from what any model's training data would assume) rather than writing v15-era
patterns from memory. Then:
- `frontend/app/globals.css` — DESIGN.md's palette and hairline/muted tokens encoded as
  a Tailwind v4 `@theme` block (`--color-ink`, `--color-parchment`, `--color-muted`,
  `--color-rule`, `--color-critical`), the three font roles as a second `@theme inline`
  block, and the frosted-panel mechanic as a named `@utility glass`.
- `frontend/app/layout.tsx` — loads Instrument Serif, JetBrains Mono, and Inter via
  `next/font/google` (self-hosted, no runtime request to Google), binds them to the
  theme's font roles.
- `frontend/app/page.tsx` — screen 1, the input screen: wordmark, thesis line, one
  underlined text field, one glass button, a passive-scope footnote. Deliberately inert
  (`type="button"`, no state) — a Server Component, since nothing on it needs to run in
  the browser yet.

**Why:** DESIGN.md is the agreed source of truth for the look; this achievement's whole
job is turning that document's prose into values a component can actually reference,
once, in one place — so a later palette change is a one-line edit in `globals.css`
rather than a hunt through every component. Reading Next 16's actual bundled docs first,
rather than assuming Pages-Router-era or Tailwind-v3-era patterns, avoided writing code
against an API that no longer exists (both of which are real and common in older
tutorials, called out explicitly in the learning note).

**Where:**
- `frontend/` — new Next.js app (package.json, tsconfig, eslint config, etc.)
- [frontend/app/globals.css](../../frontend/app/globals.css),
  [frontend/app/layout.tsx](../../frontend/app/layout.tsx),
  [frontend/app/page.tsx](../../frontend/app/page.tsx) — the real content
- [.claude/launch.json](../../.claude/launch.json) — new, so the dev server can be
  started via the browser preview tool by name
- [docs/ROADMAP.md](../ROADMAP.md) — marked A13 `[x]` (folded into the combined A13-A15
  update, see the A15 entry below)
- [docs/learning/13-react-app-router-and-tailwind.md](learning/13-react-app-router-and-tailwind.md)
  — new learning note, the largest conceptual jump in the project so far: JavaScript
  syntax from scratch (`const`/`let`, arrow functions, template literals, ternaries,
  object literals, imports), what a component and JSX actually are, `className`,
  `export default`, the App Router's file-is-a-route convention, `layout.tsx` and
  `children`, Server Components as the default, Tailwind's utility-class model, and
  Tailwind v4's CSS-based `@theme`/`@utility` config (explicitly contrasted against the
  v3 `tailwind.config.js` pattern most existing tutorials still show).

**Verification:** Started the dev server via the browser preview tool and read the
browser's own *computed* styles rather than trusting a screenshot — confirmed
`body` background is exactly `rgb(14, 14, 13)` (`#0e0e0d`), heading font resolves to
`"Instrument Serif"`, input font to `"JetBrains Mono"`, the button's
`backdrop-filter` is exactly `blur(20px) saturate(1.2)`, matching DESIGN.md's spec
value for value. Also directly investigated why `--color-critical` came back empty from
`getComputedStyle` — confirmed via reading every emitted stylesheet rule that Tailwind
v4 tree-shakes `@theme` tokens nothing currently references, not a bug, and documented
it in the note as a heads-up rather than leaving it as a mystery for later. Zero
console errors.

**Other notes:**
- No `/report` route created — DESIGN.md's three "screens" are conditional views
  within one page, not three URLs (decided at A15, see below); the original scaffold's
  now-orphaned `frontend/app/report/page.tsx` stub is removed there.
- Continuing straight to A14 per your "run several achievements together" instruction —
  no stop here.

---

## 2026-07-29 — A14: It talks

**Prompt/trigger:** Continuation of the same instruction — no separate prompt; proceeded
directly from A13 per "do 2-3 steps at once, just don't skip the learning notes."

**What I did:** Wired the input screen to the real backend.
- `backend/main.py` — added `CORSMiddleware`, allow-listed to
  `http://localhost:3000` / `http://127.0.0.1:3000` specifically (not `"*"`) — noticed
  and fixed a stale comment in the same file still referencing `ANTHROPIC_API_KEY`,
  left over from the A12 Groq swap despite that swap's activity log entry claiming
  every reference was renamed.
- `frontend/lib/api.ts` — new file: hand-written TypeScript mirrors of every
  `backend/models.py` shape (`Finding`, `AgentResult`, `ScanReport`, `Severity`,
  `Status`), plus `runScan(url)`, the one function that calls `POST /scan` and turns a
  non-2xx response into a thrown `Error` carrying FastAPI's own `detail` message.
- `frontend/app/page.tsx` — gained `"use client"`, four `useState` hooks (`url`,
  `isScanning`, `report`, `error`), a `handleSubmit` that calls `runScan` inside
  `try/catch/finally`, a controlled `<input>`, and three conditional blocks (waiting /
  error / minimal result) — the last one deliberately thin, since A15 replaces it.

**Why:** CORS is the one piece of plumbing with no backend-only equivalent to lean on —
`curl` and every prior `verification` step in this project never needed it, so it's a
genuinely new failure mode, not a rehearsal of an old one. Allow-listing two exact
origins instead of `"*"` matters more here than in a typical API: Sentinels' whole job
is making outbound requests to arbitrary third-party URLs, so a wildcard would let any
page on the internet use a visitor's browser as a way to source scan traffic at a
target of the attacker's choosing.

**Where:**
- [backend/main.py](../../backend/main.py) — `CORSMiddleware` added; stale comment
  fixed
- [frontend/lib/api.ts](../../frontend/lib/api.ts) — new
- [frontend/app/page.tsx](../../frontend/app/page.tsx) — client component, real submit
  flow
- [docs/learning/14-state-fetch-and-cors.md](learning/14-state-fetch-and-cors.md) — new
  learning note: `"use client"` as a module-graph boundary (not "runs on the client
  instead"), `useState` built up from why a plain variable doesn't work, event handlers
  and `preventDefault`, the controlled input, JavaScript `async`/`await` explicitly
  contrasted against Python's (a called async function starts immediately in JS — the
  return value is already in flight, unlike an unawaited Python coroutine, which does
  nothing until awaited), why `fetch` never throws on 4xx/5xx, CORS from first
  principles (same-origin policy, why it exists, preflight, and the point that CORS is
  enforced by the browser and does nothing to stop a direct `curl`), and TypeScript
  basics used in `api.ts` (`interface`, union types, generics, optional chaining,
  nullish coalescing).

**Verification, real network calls throughout, no mocking:**
- Confirmed the CORS grant directly with `curl -X OPTIONS`, comparing an allowed origin
  (`http://localhost:3000`, gets `access-control-allow-origin` back) against a hostile
  one (`https://evil.example`, header absent) — proved the allow-list actually
  restricts, not just that *a* header comes back.
- Drove the real browser: typed `example.com`, clicked Inspect, got back a real
  `54/F`/`12 findings`/`1207ms` result — the same 54/F A6 recorded for that site,
  arriving through the browser this time. Zero console errors.
- Typed `ftp://example.com`, confirmed the UI shows *"Unsupported URL scheme: 'ftp'"* —
  FastAPI's own `HTTPException.detail` string, written in `orchestrator.py` back in A5,
  surfacing verbatim in the browser — and confirmed the previous run's stale report was
  cleared, not left on screen alongside the error.
- Killed and restarted the backend after the CORS edit to confirm the change was
  actually picked up, not just written to disk.

**Other notes:**
- Continuing straight to A15, same instruction as above.

---

## 2026-07-29/30 — A15: Report view (Checkpoint 3 reached)

**Prompt/trigger:** Continuation of the same "run several achievements together"
instruction. Session paused overnight after this achievement (user went to sleep); the
final in-flight edit (removing the critical-red colour from agent-error text in
`AgentLog.tsx`) was committed but not yet re-verified in the browser when the session
ended. Picked back up the next day with "you can continue the work now" — re-verified
that edit first, then finished the remaining items (this note, ROADMAP, this log).

**What I did:** Built the real dossier, replacing A14's placeholder result block.
- `frontend/components/ScoreRing.tsx` — the grade as an SVG arc (`strokeDasharray` /
  `strokeDashoffset`), drawn in parchment at every score — deliberately not
  colour-coded green-to-red, since DESIGN.md reserves the accent colour for Critical
  findings alone.
- `frontend/components/FindingRow.tsx` — one finding, with the Critical-red accent
  applied in exactly two places (the severity label, a left border) and every other
  field (`description`, `evidence`, `remediation`, `owasp`) rendered only when present,
  since all four are optional in `models.py`.
- `frontend/components/AgentLog.tsx` — the five agents, their check counts and
  durations, or their error if `AgentResult.error` is set.
- `frontend/lib/findings.ts` — `groupByCategory`: groups findings by category, sorts
  problems within a category worst-severity-first, sorts categories themselves by their
  worst problem — pure functions, no component logic mixed in.
- `frontend/components/Report.tsx` — assembles the above into the full screen: score
  ring + header, AI assessment (rendered only if non-empty — A12's graceful-degradation
  contract holding at the UI layer too), findings by category, agent log.
- `frontend/app/page.tsx` — restructured so the input section shrinks to a header once
  a report exists, and `<Report>` takes over the page. No `/report` route — DESIGN.md's
  three "screens" are conditional views within one page, not three URLs, because moving
  to a real route would mean either re-running a real scan against someone else's site
  on every refresh, or standing up a state store just to carry one object across a
  navigation.
- Removed `frontend/app/report/page.tsx`, the scaffold-era stub left over from before
  the design brief existed, now genuinely dead.

**A real bug, found by testing, not invented for the note:** The first live scan with
the new Report component came back with an empty Assessment section. Traced it, not
guessed at it: called `summarize()` directly four times — one success, three empty
strings. Bypassed its `try/except` and hit Groq's raw API directly, capturing full
responses: all four came back **HTTP 200**, so not a key or network problem. The
`usage` field showed why — `openai/gpt-oss-20b` is a reasoning model, and its internal
"thinking" was consuming 198 of the 200 `max_completion_tokens` budget, leaving nothing
for the actual answer (`finish_reason: "length"`, empty `content`). A12's original
verification never caught this because it sampled `summarize()` successfully exactly
once and never repeated the call. Fixed in `backend/ai/analyst.py`:
`max_completion_tokens` raised to 800, `reasoning_effort: "low"` added. Verified 5/5
repeated calls now return a complete summary (276-506 characters each), where the same
test against the old settings failed 3 times out of 4.

**Why:** Grouping by category, worst-first, means the thing most worth a reader's
attention is always at the top regardless of which agent happened to find it — matching
DESIGN.md's "Grade huge → AI summary → findings by category → agent log" structure
directly. Deriving `groups` fresh from `report.findings` on every render (not storing it
in its own `useState`) rather than deriving it means there's no second copy of the
findings that could ever drift out of sync with the score above it. The agent-error
color decision (finding it, then fixing it) matters because DESIGN.md's restraint rule
is only meaningful if it's actually enforced everywhere the accent color could
plausibly show up, including places (an agent's own connection failure) that aren't
findings at all.

**Where:**
- [frontend/components/ScoreRing.tsx](../../frontend/components/ScoreRing.tsx),
  [FindingRow.tsx](../../frontend/components/FindingRow.tsx),
  [AgentLog.tsx](../../frontend/components/AgentLog.tsx),
  [Report.tsx](../../frontend/components/Report.tsx) — new
- [frontend/lib/findings.ts](../../frontend/lib/findings.ts) — new
- [frontend/app/page.tsx](../../frontend/app/page.tsx) — restructured around
  conditional rendering of `<Report>`
- `frontend/app/report/page.tsx` — deleted (dead scaffold stub)
- [backend/ai/analyst.py](../../backend/ai/analyst.py) — token-budget / reasoning-effort
  fix described above
- [docs/ROADMAP.md](../ROADMAP.md) — A13, A14, A15 all marked `[x]`, Checkpoint 3 marked
  REACHED, current position now points at A16
- [docs/DESIGN.md](../DESIGN.md) — the three "still open" items resolved at A13 kickoff
  now recorded as decided, rather than left stale
- [docs/learning/15-components-props-and-the-report.md](learning/15-components-props-and-the-report.md)
  — new learning note: props as a component's parameters (and that they flow one
  direction only, parent to child), composition, deriving data instead of storing it in
  state (and why that avoids a whole class of bug), `.sort()`'s in-place mutation
  versus Python's `sorted()`, the spread operator, drawing a partial circle with
  `strokeDasharray`/`strokeDashoffset` explained from first principles before the real
  code, ternaries and computed class names in JSX, `Record<K, V>`, and a full section
  walking through the reasoning-token bug as it was actually found.

**Verification:**
- Live browser test against `example.com`: full dossier rendered — `F`, `54/100`,
  correct severity counts, findings grouped Headers-first (its worst finding is High,
  ahead of Recon/TLS/Exposure/DNS which all passed clean), agent log showing
  `485+592+459+590+419ms` of individual agent work against `1095ms` total — A11's
  concurrency claim visible on the actual report now, not just in a backend log.
- Live browser test against `http://neverssl.com` (real Critical finding — no HTTPS):
  confirmed via `getComputedStyle`, not visual inspection, that the accent colour
  (`rgb(139, 58, 47)`) appears in exactly the three places it should (Critical count,
  the Critical finding's left border, its severity label) — an unintended fourth
  usage on agent-error text was caught this way, fixed, and (after the overnight pause)
  re-verified the next session by forcing four real agent errors (an unresolvable
  domain) and confirming all four render in full-strength parchment, not the accent.
- Ran a full production build (`npm run build`) in addition to all the live-browser
  testing above — clean `tsc --noEmit`, clean `eslint` across `app`/`lib`/`components`,
  clean Turbopack production build, `/` correctly prerendering as static content (since
  it's still Server-Component-rooted at the top level, per A13's note).
- Verified the AI-summary fix in isolation before touching the frontend at all (see the
  bug section above) — five repeated direct calls to `summarize()`, all non-empty.

**Checkpoint 3 reached:** browser → real scan → real report. This is the demo.

**Other notes:**
- Committed this work (A13-A15, the analyst fix, both docs) on a new branch,
  `act4-frontend`, rather than directly on `master` — flagged to you as this session's
  default for the repo's default branch, with the fast-forward merge command given if
  you'd rather it just live on `master` directly, given the project's linear,
  single-developer history so far.
- Flagged, unprompted: the real Groq API key pasted into chat during the A12 follow-up
  lives in `backend/.env`, correctly gitignored (verified via `git check-ignore`) but
  exposed in plaintext in the conversation itself — worth rotating from the Groq
  console at some point, independent of anything in this repo.
- Next up: **A16 — Live progress** (Server-Sent Events), which is what turns the input
  screen's current generic five-agent pulse into the real thing DESIGN.md's screen 2
  describes — agents actually lighting up as they finish, not a stand-in animation.

---

## 2026-07-30 — A16: Live progress

**Prompt/trigger:** "continue with whats next" — the next unstarted roadmap item after
A15. Session included two real pauses: one where you left for the gym (picked back up
with "continue i am coming in 10 minutes... complete 2 tasks till i am coming" — SSE
backend and frontend both finished and verified within that window) and one where you
asked directly, mid-work, whether the frontend actually matched the design you'd
described, since it didn't look animated. Answered directly rather than deflecting:
confirmed the static design system (palette, fonts, the `glass` CSS utility) was built
correctly, but the drift mechanic and "set piece" animation DESIGN.md calls for on
screen 2 were never built — that gap is real, not a misunderstanding on your part. You
confirmed you want it fixed eventually and to keep going on what was in progress.

**What I did:** Added a second way to run a scan, alongside A14's `POST /scan`:
- `backend/orchestrator.py` — new `run_scan_stream()`, an async generator using
  `asyncio.as_completed` (not `asyncio.gather`) to yield each agent's `AgentResult`
  the instant it finishes — real completion order, not `AGENTS`' declared order, and
  not the same order twice — followed by one final `ScanReport`. Extracted a shared
  `_finalize()` helper so `run_scan` and `run_scan_stream` compute score/grade/summary
  the same way instead of duplicating that logic.
- `backend/main.py` — new `GET /scan/stream?url=...`, returning a `StreamingResponse`
  with `media_type="text/event-stream"`. Takes `url` as a query parameter rather than a
  JSON body specifically because the browser's `EventSource` (the consumer) can only
  issue GET requests with no body. A rejected URL becomes an `event: failed` message
  inside an otherwise-200 stream, not an HTTP 400 — once the first byte of a streaming
  response is sent the status code is already committed, so `POST /scan`'s "catch
  ValueError, return 400" approach genuinely isn't available here.
- `frontend/lib/api.ts` — new `streamScan(url, handlers)`, wrapping `EventSource` with
  three callbacks (`onAgent`, `onDone`, `onError`) instead of returning a single
  `Promise` — a scan produces six separate values over time (five agents + one report),
  and a Promise can only ever resolve once. Explicitly calls `.close()` on both the
  `"done"` and `"failed"` paths, since `EventSource` auto-reconnects on any closed
  connection by default — including a normal, successful end of stream — which without
  that call would silently re-trigger a second scan a few seconds after the first one
  finished.
- `frontend/app/page.tsx` — the waiting state now tracks `agentResults`, a
  `Record<string, AgentResult>` filled in one key at a time as `onAgent` fires. Each of
  the five agent names individually switches from dim-and-pulsing to
  lit-and-real-duration the instant its own event lands, instead of all five pulsing in
  lockstep until one combined result arrives.

**A near-miss caught before it shipped:** the first draft of the `_finalize()` extraction
moved `duration_ms`'s calculation to after the AI summary call, purely as an accident of
refactoring — not a deliberate choice. That would have silently changed what the field
measures (previously agent time only; newly agent time plus the Groq call on top),
breaking the semantics `AgentLog`'s "total is less than the sum, because concurrent"
claim depends on. Caught by re-diffing against the original before moving on, not by
a later test failing.

**An observation investigated, not fixed:** comparing `duration_ms` against the slowest
individual agent's own duration, repeatedly, showed a consistent ~270-280ms gap on every
run. Checked whether this was new: the *same* gap, same size, shows up in `POST /scan`
too — a path this achievement's timing logic didn't touch. That rules out the streaming
work as the cause; it's a pre-existing property of the whole pipeline (most likely
`httpx.AsyncClient`/thread-pool startup cost), out of scope for this achievement,
recorded rather than chased.

**Why:** `asyncio.as_completed` over `asyncio.gather` is the whole mechanism — same
concurrency A11 already proved, the only change is *when* the caller learns about each
result. SSE over WebSockets: the browser never needs to send anything back once a scan
starts, so a one-way protocol is the right amount of machinery, not less capable, just
better matched to the job. `EventSource` over a hand-rolled `fetch` + `ReadableStream`
implementation: it's the browser's own built-in SSE client, needs no parsing code of our
own, and is the standard tool for exactly this wire format.

**Where:**
- [backend/orchestrator.py](../../backend/orchestrator.py) — `run_scan_stream`,
  `_finalize`
- [backend/main.py](../../backend/main.py) — `GET /scan/stream`, `_sse` helper
- [frontend/lib/api.ts](../../frontend/lib/api.ts) — `streamScan`,
  `ScanStreamHandlers`; `runScan` removed (no longer used anywhere in the frontend —
  `POST /scan` itself stays, for `curl` and non-browser clients)
- [frontend/app/page.tsx](../../frontend/app/page.tsx) — waiting state driven by
  `agentResults` instead of a static pulse
- [docs/ROADMAP.md](../ROADMAP.md) — A16 marked `[x]`, current position updated,
  the design-brief gap recorded openly
- [docs/learning/16-live-progress-and-server-sent-events.md](learning/16-live-progress-and-server-sent-events.md)
  — new learning note: `asyncio.as_completed` vs. `asyncio.gather` (contrasted with a
  kettle/toast-style standalone timing example), async generators (`async def` +
  `yield` together, a standalone `counter()` example before the real code), SSE's wire
  format read directly off a real `curl -N` stream, `StreamingResponse` and why a
  streaming endpoint can't send an HTTP error status once started, `EventSource` from
  first principles (GET-only, auto-reconnect, the `"failed"` vs. built-in `"error"`
  naming collision explained and demonstrated), callback-based async as a genuinely
  different shape from `await`ing one Promise, and computed property names / object
  spread for building `agentResults` up one key at a time without mutating state.

**Verification:**
- Backend, directly via `curl -N`: real SSE wire format confirmed byte-for-byte (blank
  line as message terminator), five `agent` events in real completion order followed by
  one `done` event carrying the full report, status genuinely `200` even for a rejected
  URL (`ftp://example.com`), with the real error message arriving as `event: failed`
  inside the stream instead.
- Frontend, live in the browser, with a DOM-polling instrument (not inferred from
  reading the code): armed a 30ms poll immediately before clicking Inspect against
  `wordpress.org`, then read back exactly when each agent's span first turned
  "lit" — `dns` at 511ms, `tls` at 1170ms, `headers` at 1982ms, `recon` at 2312ms,
  `exposure` at 2463ms, with `done` firing at 3217ms (after the AI summary call on
  top). Five distinct, staggered real timestamps — not simultaneous, not scripted.
- Both error paths triggered live and confirmed distinct: submitted `ftp://example.com`
  and confirmed the UI shows the backend's real detail message via the `"failed"` path;
  separately, stopped the backend process entirely and confirmed the UI shows "Lost
  connection to the scanner." via the built-in `onerror` path — then waited 4 seconds
  and confirmed the button had cleanly reset to "Inspect" with no retry-loop spam,
  proving `.close()` actually stops `EventSource`'s automatic reconnect.
- A stale-console-log false alarm, investigated properly instead of assumed: the
  browser console kept replaying a `runScan` import error from a transient moment
  between two sequential edits (editing `lib/api.ts` before `page.tsx`), even across
  hard reloads. Rather than trust the console, checked the file on disk (correct), then
  fetched the actual served Turbopack bundle byte-for-byte and confirmed `runScan`
  appears nowhere except inside a code comment — the served code was correct the whole
  time; the console tool was replaying stale history.
- Clean `tsc --noEmit` and clean `eslint` across `app`/`lib`/`components` after the
  changes.

**Other notes:**
- **A real, unprompted gap surfaced and owned, not glossed over:** mid-achievement, you
  asked directly whether the current frontend matched the design you'd described,
  noting it didn't look animated. Confirmed plainly: it doesn't, specifically on the
  glass-panel scroll-drift mechanic and the "set piece" animation budget DESIGN.md
  assigns to the scan screen. A16 delivers correct, real data for that screen (genuine
  per-agent timing) but not yet the motion layer. Scoped as separate follow-up work per
  your go-ahead ("if yes then do whatever you were doing no problem") rather than
  silently folded into A16's own done-ness or left unaddressed.
- Not yet committed.
- Next up: **A17 — PDF export** (Playwright, headless browsers) is the next roadmap
  item; the outstanding frontend motion/artifact-styling pass is separate follow-up
  work, not yet scheduled.

---

## 2026-07-30 — A17: PDF export

**Prompt/trigger:** "go with a17" (continue the roadmap to the next achievement).

**What I did:** Added a "Download PDF" path for a finished report:
- `backend/report/pdf.py` — `render_html()` rebuilds a `ScanReport` as one flat HTML
  document by hand: the score-ring arc math copied from `ScoreRing.tsx`, the
  category-grouping rules copied from `lib/findings.ts`, since Python has no way to
  run the React components that already know how to do this. `generate_pdf()` hands
  that HTML to a headless Chromium via Playwright (`page.set_content` then
  `page.pdf()`) and returns the raw bytes.
- `backend/main.py` — new `POST /scan/pdf`, taking a full `ScanReport` as its JSON
  body (not a URL) and returning `application/pdf` with a
  `Content-Disposition: attachment` header so the browser saves it as a file.
- `frontend/lib/api.ts` — new `downloadReportPdf(report)`: POSTs the report, turns
  the response into a `Blob`, mints a temporary object URL, and clicks a throwaway
  `<a download>` to trigger the save.
- `frontend/components/Report.tsx` — new "Download PDF" button next to the score
  ring, with its own loading/error state (`isExporting`, `exportError`); gained a
  `"use client"` directive since it now calls `useState` directly.
- `backend/requirements.txt` — added `playwright==1.61.0`; installed the Chromium
  binary locally via `playwright install chromium`.

**Why:** A PDF is fundamentally "print this page" — reusing a real browser engine
(via Playwright) for that sidesteps re-implementing CSS/layout support (flexbox, our
inline SVG score ring) that a lighter HTML-to-PDF library would likely get wrong.
The endpoint takes the *whole report*, not a URL, deliberately: the frontend already
has a finished `ScanReport` in state the moment the button is visible, and re-scanning
server-side risked a live site changing between the on-screen report and the
downloaded one — the two would then disagree, which defeats the point of a report as
a record. Fonts in the PDF are system fonts (Georgia/Segoe UI/Courier New), not the
Google Fonts the real frontend loads — rejected relying on a network fetch
*during* PDF generation, for a tool whose whole pitch is fast and dependable.

**Where:**
- [backend/report/pdf.py](../../backend/report/pdf.py) — `render_html`,
  `generate_pdf`, `_group_by_category`, `_finding_html`, `_score_ring_svg`,
  `_agent_log_html`
- [backend/main.py](../../backend/main.py) — `POST /scan/pdf`
- [backend/requirements.txt](../../backend/requirements.txt) — `playwright` added
- [frontend/lib/api.ts](../../frontend/lib/api.ts) — `downloadReportPdf`
- [frontend/components/Report.tsx](../../frontend/components/Report.tsx) —
  "Download PDF" button, `isExporting`/`exportError` state, `"use client"`
- [docs/ROADMAP.md](../ROADMAP.md) — A17 marked `[x]`, current position updated
- [docs/learning/17-pdf-export-and-playwright.md](learning/17-pdf-export-and-playwright.md)
  — new learning note: headless browsers, Playwright's `page.pdf()`, `html.escape()`
  against untrusted scanned-site text, `Blob` + object-URL downloads

**Verification:**
- Live in the browser: scanned `wordpress.org` end-to-end, clicked "Download PDF",
  confirmed via the network panel a genuine `POST /scan/pdf` → `200 OK`, and the
  button cleanly returned from "Preparing…" back to "Download PDF" with no error
  banner.
- Directly via `curl`: ran a real scan, piped the resulting `ScanReport` JSON straight
  into `POST /scan/pdf`, and confirmed the response headers
  (`content-disposition: attachment; filename="sentinels-wordpress-org.pdf"`,
  `content-type: application/pdf`) and the file itself — real `%PDF-1.4` magic bytes,
  two pages, opened and rendered correctly.
- HTML-escaping checked both ways, not assumed: POSTed a hand-crafted report with a
  finding title containing `<b>bold text</b>` and confirmed it rendered as the
  literal text `<b>bold text</b>`; then temporarily removed the one `escape()` call
  in `_finding_html`, restarted, and confirmed the same title rendered genuinely
  bold — proof the escaping was doing real work, not decoration.

**Other notes:**
- Committed as `f22ad3f` — "A17: PDF export via headless Chromium (Playwright)".
- One browser launch per `/scan/pdf` call (no warm/reused Chromium instance) —
  measurably the most expensive part of the request, called out explicitly as a
  deliberate simplicity-over-speed tradeoff in the learning note rather than left
  unexplained.
- Next up: **A18 — Ship it**; the frontend motion/artifact-styling pass flagged in
  A16 is still separate, unscheduled follow-up work.

---

## 2026-07-31 — A18: Ship it (roadmap complete, 18/18)

**Prompt/trigger:** "go on b-1" through "continue", across several turns — the
motion pass (`docs/PLAN.md` Part A) had already been finished separately; this
picked up Part B, one step at a time per the plan's own ground rule ("don't
chain steps without checking in"), with explicit "commit this" instructions in
between rather than one big landing.

**What I did:** Six steps, each shipped and verified individually:

- **B-1** — Wrote the repo's first root `README.md`: what Sentinels is, the
  passive-only ethic (stated up front with a concrete never-list: no SQLi, no
  brute force, no fuzzing, no form submission, no DoS traffic), a five-agent
  table, tech-at-a-glance, and a project layout section. Captured two real
  screenshots — `docs/images/input-screen.png`, `docs/images/report-screen.png`
  — via a small Playwright script driving the actual dev frontend against the
  actual backend through a real scan of `wordpress.org` (you chose "both
  screens" when asked).
- **B-2** — Expanded the README's "Running it" section into full Windows/
  PowerShell setup for both halves. Reproduced the documented Chromium gotcha
  for real rather than describing it from memory: temporarily renamed the
  machine's cached `ms-playwright/chromium-*` directories, confirmed
  `/scan/pdf` fails with a bare `500` and zero client-side detail while the
  real cause (`Executable doesn't exist...`, Playwright's own `playwright
  install` tip) only appears in the backend's terminal log, then restored the
  directories and re-verified `/scan/pdf` returns a real `200` with a valid
  3-page PDF.
- **B-3** — Rewrote `backend/.env.example` from a bare `GROQ_API_KEY=` into a
  commented file: what the key powers (only `ai/analyst.py`'s summary
  paragraph), where to get a free one, and that it's optional — cross-checked
  against `analyst.py`'s actual code (`os.environ.get`, empty-string return on
  missing key or any failure) rather than assumed. Confirmed `backend/.env`
  itself is genuinely gitignored via `git check-ignore -v`.
- **B-4** — Replaced `frontend/README.md`, still the untouched `create-next-app`
  boilerplate advertising the Geist font (never used here), with an accurate
  quick start, a real structure map, and the actual three type roles
  (Instrument Serif / JetBrains Mono / Inter), checked directly against
  `app/layout.tsx`.
- **B-5** — The real test: a genuine `git clone` (tracked files only — no
  `.venv`, `node_modules`, or `.env` carried over) into a fresh directory,
  following only what B-1 through B-4 had written down. Both halves came up
  and a real scan of `python.org` completed end-to-end in the browser
  (69/100, grade D) — but **Download PDF failed**, a real bug this test was
  built to catch. Traced it to uvicorn's own source
  (`uvicorn/loops/asyncio.py`): `--reload` deliberately switches Windows to
  `WindowsSelectorEventLoopPolicy`, and a Selector loop cannot start the
  subprocess Playwright needs to launch a browser — surfacing as a blank `500`
  with a message-less `NotImplementedError` visible only server-side. Isolated
  by testing the identical clone with and without `--reload` (without: real
  `200` + valid PDF; with: `500`). Fixed in code, per your explicit choice
  (offered "fix in code" / "document only" / "both") — `backend/report/pdf.py`
  now runs the Playwright work on an explicit `asyncio.ProactorEventLoop`
  inside `asyncio.to_thread`, on Windows only, built directly rather than via
  `new_event_loop()` since the policy is exactly what uvicorn overrode.
  Re-verified under the previously-failing exact command (real `200` + valid
  PDF, via `curl` and the live browser's Download PDF button), and confirmed
  the fix doesn't block the server: `/health` answered 9 times at 2-3ms each
  during a 2209ms PDF render. Two rejected alternatives were tested, not
  assumed — resetting the policy from `main.py` (too late; uvicorn's loop
  already exists before the app is imported) and Playwright's `sync_api` in a
  thread (fails identically — it builds its own loop from the same policy).
- **B-6** — This entry, plus the learning note and roadmap close-out below.

**Why:** A18's roadmap definition is "hand the repo to a stranger and have
them run it" — B-1 through B-4 wrote the docs a stranger needs, and B-5 is
what actually tested them *as* a stranger would: on a machine with none of
this project's accumulated, undocumented setup already sitting on it. That
distinction is exactly what caught the `--reload` bug — every prior
achievement's testing reused an environment that already had a working
non-reload server and an already-installed Chromium, so nothing before this
step had reason to combine `--reload` with Playwright at all.

**Where:**
- [README.md](../README.md) — new file
- [docs/images/input-screen.png](../images/input-screen.png),
  [docs/images/report-screen.png](../images/report-screen.png) — new, real
  screenshots
- [backend/.env.example](../../backend/.env.example) — documented
- [frontend/README.md](../../frontend/README.md) — replaced
- [backend/report/pdf.py](../../backend/report/pdf.py) — `_render_pdf`,
  `_render_pdf_on_own_loop`, `generate_pdf` reworked for the Windows event-loop
  fix
- [docs/ROADMAP.md](../ROADMAP.md) — A18 marked `[x]`, roadmap shows 18/18,
  "Current position" closes out the project
- [docs/PLAN.md](../PLAN.md) — all of B-1 through B-6 marked `[x]` with their
  individual verification notes
- [docs/learning/17f-event-loops-and-the-reload-trap.md](learning/17f-event-loops-and-the-reload-trap.md)
  — new learning note: Windows' two event loop implementations (Proactor vs.
  Selector), event loop *policies* as a global, overridable setting, why
  starting a subprocess is a loop capability rather than a language feature,
  and why a loop belongs to the thread running it — traced to uvicorn's real
  source line, not guessed
- [docs/learning/18-shipping-and-clean-room-verification.md](learning/18-shipping-and-clean-room-verification.md)
  — new learning note: a README as a contract with a reader who has none of
  the project's context, clean-room verification as testing the *instructions*
  rather than the code, and `.gitignore` read as a manifest of everything a
  clean clone has to rebuild

**Verification:** Each step verified on its own as it shipped (screenshots
against a real running app; the Chromium gotcha reproduced and reversed twice,
before and after the B-5 fix; `.env.example`'s claims checked against
`analyst.py`'s real code; `frontend/README.md`'s font claims checked against
`app/layout.tsx`; the B-5 clean-room run itself, twice — once that failed
correctly, once against the fix that passed). No verification was deferred to
this closing entry; it only records what already happened.

**Other notes:**
- Committed across four commits as the steps landed: `630951d` (B-1/B-2),
  `fd508c5` (B-3/B-4), `af0e9d5` (B-5's fix + its learning note).
- One environment caveat, logged but not a project bug: the first clean-room
  clone attempt was made inside a deeply-nested scratch/temp path and
  Turbopack panicked on Windows' path-length limit; re-run from a short path
  (`C:\stest`), it was clean. Worth knowing if this is ever repeated, nothing
  to fix in the repo.
- Reminded, mid-arc, to keep updating learning notes as work happens rather
  than batching them — already the standing rule in `CLAUDE.md` and
  `docs/learning/00-how-to-use-these-notes.md`; `17f` had already been written
  immediately after its fix, before that reminder arrived.
- **This is the last entry needed to close the roadmap.** All 18 achievements
  are done, tested, and documented. `docs/PLAN.md` remains as the record of
  how the motion pass and A18 were actually sequenced, for anyone who wants
  the blow-by-blow rather than this summary.

---

## 2026-08-09 — PLAN-v4: three new attack-surface agents (V1-V10)

**Prompt/trigger:** "start with v9 and fully complete the plan" — picking the
v4 plan back up after V1-V8 had already landed across earlier sessions on
`v4-attack-surface-agents`, to finish V9 (the test matrix) and V10 (docs,
learning notes, end-to-end verification) without stopping in between.

**What shipped, V1-V10** (each already has its own learning note and its own
detailed status block in [`docs/PLAN-v4.md`](PLAN-v4.md) — this entry is the
summary, not the record):

- **V1-V3 (foundation):** `Finding` gained `affected_url`/`confidence`; a
  shared probe layer (`backend/agents/probe.py` — `ResponseCache`,
  `RobotsGate`, `Budget`, `safe_get/head/options`) so three new agents share
  fetches instead of tripling requests; `scoring.py` gained cross-agent
  dedup, an alias table, repeat decay, and a per-new-agent 20-point cap so
  eight agents seeing one real problem still cost one deduction.
- **V4-V6 (the three agents):** `ApiSecurityAgent` (docs/GraphQL exposure,
  CORS, response leaks, auth posture, risky methods — never invoked),
  `MisconfigAgent` (directory listings, backup files, debug output, server
  version, default/setup pages, unsafe caching), `SubdomainAgent`
  (certificate SANs + Certificate Transparency + a 12-name list, every
  candidate DNS-verified; dangling-DNS/takeover findings graded by an honest
  `confidence`, never asserted as fact).
- **V7 (integration):** three new checklist rules; the AI prompt layer
  learned to split findings into confirmed vs. needs-verification by
  `confidence` and carry `affected_url`.
- **V8 (frontend):** the scan UI's agent grid went from 5 to 8 panels, plus a
  new sortable subdomain-inventory table — `AgentReel.tsx`/`FindingRow.tsx`
  needed zero changes, the payoff of having built both generically back in
  earlier milestones.
- **V9 (tests):** 45 new tests (56 → 101) — a proper `test_probe.py` and
  `test_findings_schema.py` (the latter backed by a new `temp_db` fixture
  that points `db.DB_PATH` at a throwaway SQLite file, so storage tests never
  touch the real dev database), a new `test_orchestrator.py` proving the
  project's core crash-isolation guarantee at the 8-agent scale (one agent
  raising leaves the other seven results intact), and targeted failure-case
  tests (malformed JSON, 403/404/429-everywhere, redirect chains, a mid-scan
  DNS exception) added to the three new agents' existing test files.
- **V10 (this entry):** `README.md`'s agent table grew to 8 rows plus a new
  "What Sentinels does *not* do" section; `CLAUDE.md` gained the plan's two
  new non-negotiables (bounded probing, confidence stated never implied) and
  its agent count; this pointer entry and `docs/ROADMAP.md`'s "Beyond this
  roadmap" section; the two remaining learning notes
  ([`54`](learning/54-frontend-attack-surface-ui.md),
  [`55`](learning/55-testing-the-untested-paths.md),
  [`56`](learning/56-shipping-v4.md)); and a full end-to-end verification
  pass (clean-site scan, an agent-kill test, same-site-twice determinism,
  `GROQ_API_KEY` removed, a pre-v4 stored scan still rendering, and every
  export format carrying the new findings).

**Verification:** `pytest backend/tests -q` green (101 passed) offline, no
third-party site touched by the suite; live-scan checks described above run
against the real dev server, not assumed from the code — see V10's own
status block in `PLAN-v4.md` for the exact numbers.

**Where:** every file PLAN-v4.md's "Files at a glance" section names —
`backend/agents/{api_security,misconfig,subdomain,probe,takeover_signatures}.py`,
`backend/storage/subdomains.py`, `backend/tests/*`, `frontend/components/
SubdomainTable.tsx`, plus the modified files it lists (`models.py`, `db.py`,
`scoring.py`, `agents/{base,registry}.py`, `orchestrator.py`,
`checklist/rules.py`, `ai/prompts.py`, the frontend agent-UI files,
`README.md`, `CLAUDE.md`, `docs/ROADMAP.md`) and `docs/learning/47-56-*.md`.

**Other notes:**
- This entry summarizes rather than repeats — `docs/PLAN-v4.md`'s own header
  carries the full milestone-by-milestone record (exact test counts, exact
  live-scan scores, exact files touched per milestone); read that file for
  the detail this entry deliberately compresses.

---
