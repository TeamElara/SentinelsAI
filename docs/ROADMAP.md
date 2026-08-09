  # Sentinels — Roadmap

The project broken into **18 achievements** across 5 acts. Each achievement is one
sitting's worth of work, ends in something you can *run and see*, and gets its own
learning note in `docs/learning/`.

**Rule for every achievement:** no achievement is "done" until (a) the code runs,
and (b) its learning note explains every new concept it introduced.

Legend — `[ ]` not started · `[~]` in progress · `[x]` done

---

## Act 1 — Backend foundation

Goal: one real agent, end to end, proving the whole pipeline shape works.

| # | Achievement | You'll be able to... | New concepts |
|---|---|---|---|
| `[x]` **A1** | Environment up | Run `python -c "import fastapi"` with no error | virtualenv, pip, `requirements.txt` |
| `[x]` **A2** | Server breathes | Open `localhost:8000/health` in a browser and see JSON | ASGI, FastAPI app object, uvicorn, decorators |
| `[x]` **A3** | The contract | Explain what `BaseAgent` and `ScanContext` are for | abstract base classes, dependency passing, `async def` |
| `[x]` **A4** | First real agent | Detect a missing `Content-Security-Policy` on a live site | HTTP response headers, `httpx`, building a `Finding` |
| `[x]` **A5** | Orchestrator v1 | POST a URL, get back a `ScanReport` with real findings | URL normalization, request lifecycle, Pydantic response models |
| `[x]` **A6** | Scoring | See a number and a letter grade that change per site | pure functions, deterministic logic, dict aggregation |

> **Checkpoint 1:** `curl -X POST localhost:8000/scan -d '{"url":"https://example.com"}'`
> returns a graded report. This is already a working (if narrow) product.

---

## Act 2 — The agent team

Goal: five agents, running at the same time, in under 60 seconds.

| # | Achievement | You'll be able to... | New concepts |
|---|---|---|---|
| `[x]` **A7** | Recon agent | Fingerprint what a site is built with | HTML parsing (BeautifulSoup), `robots.txt` |
| `[x]` **A8** | TLS agent | Read a real certificate's expiry date | sockets, the TLS handshake, stdlib `ssl` |
| `[x]` **A9** | Exposure agent | Detect a publicly exposed `/.env` — safely | HTTP status codes, ethical probing limits |
| `[x]` **A10** | DNS agent | Explain whether a domain can be spoofed | DNS records, TXT lookups, SPF/DMARC |
| `[x]` **A11** | **Parallel** | Prove 5 agents take as long as the slowest, not the sum | `asyncio.gather`, the event loop, concurrency vs parallelism |

> **Checkpoint 2 — REACHED:** Full 5-agent scan completes in ~1.3-1.5s (well under
> 60s). Measured directly: sequential total (2597ms) tracked the sum of all five agents
> (2594ms); parallel total (1308ms) tracked the single slowest agent (1297ms) instead —
> the printed proof.

---

## Act 3 — Intelligence

| # | Achievement | You'll be able to... | New concepts |
|---|---|---|---|
| `[x]` **A12** | AI analyst | Get a plain-English summary — and still work with no API key | Claude API, prompt design, graceful degradation |

---

## Act 4 — Frontend

| # | Achievement | You'll be able to... | New concepts |
|---|---|---|---|
| `[x]` **A13** | Next.js up | See a styled page at `localhost:3000` | React, App Router, Tailwind |
| `[x]` **A14** | It talks | Type a URL, hit scan, see a loading state | `fetch`, React state, CORS |
| `[x]` **A15** | Report view | See the score ring and tagged findings list | components, props, conditional rendering |

> **Checkpoint 3 — REACHED:** Browser → real scan → real report. Scanning
> `example.com` from `localhost:3000` returns a full dossier (score ring, AI
> assessment, findings grouped by category, agent log) sourced from a real,
> live `POST /scan` against the real backend.

---

## Act 5 — Polish

| # | Achievement | You'll be able to... | New concepts |
|---|---|---|---|
| `[x]` **A16** | Live progress | Watch agents light up one by one | Server-Sent Events, streaming responses |
| `[x]` **A17** | PDF export | Download the report as a file | headless browsers, Playwright |
| `[x]` **A18** | Ship it | Hand the repo to a stranger and have them run it | READMEs, env docs, clean-room verification |

---

## How each achievement runs

1. **I explain first** — what we're about to build and why, before any code.
2. **I write the code** — in small pieces, never a giant dump.
3. **I write the learning note** — `docs/learning/AN-name.md`, covering every new
   concept with a tiny standalone example you can run on its own.
4. **You run it** — I give you the exact command.
5. **You ask anything** — we don't move to the next achievement until it's clear.

## Current position

**A6 done.** `backend/scoring.py` replaces the A5 placeholders with real, pure-function
scoring: `example.com` now reports `54/F`, `github.com` reports `100/A`, verified live
through `/scan`. Determinism confirmed both as pure-function unit tests (50 calls, one
result) and end-to-end (same site scanned twice in a row, identical score). Notes:
[`learning/01-environment-and-dependencies.md`](learning/01-environment-and-dependencies.md),
[`learning/02-fastapi-and-the-server.md`](learning/02-fastapi-and-the-server.md),
[`learning/03-the-agent-contract.md`](learning/03-the-agent-contract.md),
[`learning/04-headers-and-first-live-request.md`](learning/04-headers-and-first-live-request.md),
[`learning/05-orchestrator-and-the-scan-endpoint.md`](learning/05-orchestrator-and-the-scan-endpoint.md),
[`learning/06-scoring.md`](learning/06-scoring.md).

**Checkpoint 1 reached:** `curl -X POST localhost:8000/scan -d '{"url":"https://example.com"}'`
now returns a complete, real, graded report — a working (if narrow) product.

**A7 done.** `backend/agents/recon.py` — `ReconAgent` (generator meta tag + robots.txt
sensitive-path check) now runs alongside `HeadersAgent`. Verified against
`wordpress.org` (both checks trip), `github.com` (robots.txt only), `example.com`
(neither); combined 6-finding report scores 71/C correctly. A real duplication bug
was found and fixed during testing (repeated `robots.txt` paths across multiple
`User-agent` blocks weren't deduplicated). Notes:
[`learning/01-environment-and-dependencies.md`](learning/01-environment-and-dependencies.md)
through
[`learning/07-recon-agent-and-html-parsing.md`](learning/07-recon-agent-and-html-parsing.md).

**A8 done.** `backend/agents/tls.py` — `TLSAgent` (HTTPS-in-use check, certificate
verification, expiry, protocol version) now runs alongside the other two. Verified
against real valid certs, plus expired/self-signed/hostname-mismatched certs via
`badssl.com`'s purpose-built test domains, plus a plain-HTTP target. Also directly
proved (not assumed) that the blocking `ssl`/`socket` handshake, run via
`asyncio.to_thread`, doesn't freeze the event loop — a concurrent ticker kept running
throughout the real handshake. Notes:
[`learning/01-environment-and-dependencies.md`](learning/01-environment-and-dependencies.md)
through
[`learning/08-tls-agent-and-sockets.md`](learning/08-tls-agent-and-sockets.md).

**A9 done.** `backend/agents/exposure.py` — `ExposureAgent` (`/.env` and `/.git/HEAD`
exposure checks, content-shape verified, not just status-code) now runs alongside the
other three. Verified against a genuine-exposure local fixture, a "soft-404" local
simulator (proving no false positive when a server returns 200 for everything), and
two real clean sites. All four agents now combine in one report. Notes:
[`learning/01-environment-and-dependencies.md`](learning/01-environment-and-dependencies.md)
through
[`learning/09-exposure-agent-and-ethical-limits.md`](learning/09-exposure-agent-and-ethical-limits.md).

**A10 done.** `backend/agents/dns_email.py` — `DNSAgent` (SPF + DMARC checks) is the
fifth and final Act 2 agent; all five now run in every scan. Verified against 5 real
domains covering every classification outcome, including two genuine real-world
surprises found by testing (gmail.com's `redirect=` SPF delegation, and gmail.com/
python.org both having a `p=none` — unenforced — DMARC policy on their apex domain).
Sequential 5-agent scan now takes ~2.2s, mostly DNS latency — exactly what A11 exists
to fix. Notes:
[`learning/01-environment-and-dependencies.md`](learning/01-environment-and-dependencies.md)
through
[`learning/10-dns-agent-and-email-spoofing.md`](learning/10-dns-agent-and-email-spoofing.md).

**A11 done — Checkpoint 2 reached.** `orchestrator.py`'s `run_scan()` now runs all
five agents via `asyncio.gather` instead of sequentially. Proven with real, measured
before/after timing on the same live site (see above), plus a deliberate experiment
showing why bare `gather` (no `return_exceptions=True`) is safe here — only because
A3's `run()` wrapper already guarantees no agent can raise. Notes:
[`learning/01-environment-and-dependencies.md`](learning/01-environment-and-dependencies.md)
through
[`learning/11-parallel-agents-and-asyncio-gather.md`](learning/11-parallel-agents-and-asyncio-gather.md).

**A12 done — Act 1 & 2 fully complete.** `backend/ai/analyst.py` calls an LLM for a
plain-English scan summary, wired into `orchestrator.py`. Originally built against the
Anthropic API; **swapped to Groq's free-tier, OpenAI-compatible API** (model
`openai/gpt-oss-20b`) per Arihant's request to avoid a paid provider — calling it via
plain `httpx` instead of a vendor SDK, so the `anthropic` package was removed from
`requirements.txt` entirely. Verified with a **real, working `GROQ_API_KEY`**: a real
scan of `github.com` produced a genuine, correctly-prioritized model-written summary;
with the key temporarily removed, the same scan still returned a complete report with
`summary` empty. Env var is `GROQ_API_KEY` now (in `.env.example`, `CLAUDE.md`, and the
code) — `ANTHROPIC_API_KEY` no longer appears anywhere in the project. Notes:
[`learning/01-environment-and-dependencies.md`](learning/01-environment-and-dependencies.md)
through
[`learning/12-ai-analyst-and-graceful-degradation.md`](learning/12-ai-analyst-and-graceful-degradation.md)
(the A12 note was rewritten to match the real, current implementation, with the
provider swap recorded openly rather than silently overwritten).

**The backend is now feature-complete.** Every achievement through A12 is done, tested
end-to-end, and documented — AI enrichment included, with a real key actually
configured and verified. `POST /scan` returns a real, graded, AI-enriched report for
any live site, in about 1-2 seconds.

**A13, A14, A15 done — Act 4 complete, Checkpoint 3 reached.** Built in one pass, per
Arihant's instruction to run several achievements together without stopping for
permission between them, with one learning note per achievement regardless.
`frontend/` is a real Next.js 16 / React 19 / Tailwind v4 app: `docs/DESIGN.md`'s
graphite palette and three type roles (Instrument Serif, JetBrains Mono, Inter) are
encoded as Tailwind `@theme` tokens; `localhost:3000` is the input screen (fully
typographic, no landing page — `/` *is* the scan input, matching the brief's "fast
tool, not a brochure" position); typing a URL and submitting runs a real scan against
the real backend (CORS added to `main.py`, allow-listed to the two dev origins rather
than `"*"`, since this API makes outbound requests and a wildcard would let any page
use a visitor's browser to source scan traffic); and a finished scan renders as the
full dossier — score ring, AI assessment, findings grouped by category worst-first,
agent log. Verified with a production `next build` (clean, `/` prerenders statically)
in addition to live browser testing. Notes:
[`learning/13-react-app-router-and-tailwind.md`](learning/13-react-app-router-and-tailwind.md),
[`learning/14-state-fetch-and-cors.md`](learning/14-state-fetch-and-cors.md),
[`learning/15-components-props-and-the-report.md`](learning/15-components-props-and-the-report.md).

A real, non-frontend bug was found and fixed along the way: A12's AI summary was
silently empty on roughly three scans out of four. `openai/gpt-oss-20b` is a reasoning
model, and its internal "thinking" was consuming 198 of the 200
`max_completion_tokens` budget, leaving nothing for the actual answer
(`finish_reason: "length"`, empty `content`) — every HTTP-level signal said success (200,
valid JSON, no exception), so A12's original single-sample verification never caught
it. Fixed in `backend/ai/analyst.py` (budget raised to 800, `reasoning_effort: "low"`
added); verified 5/5 repeated calls now return a complete summary. Full story in
[`learning/15-components-props-and-the-report.md`](learning/15-components-props-and-the-report.md#3-a-real-bug-found-by-testing--not-invented-for-the-note).

**A16 done — Act 5 begins.** `backend/orchestrator.py` gained `run_scan_stream()`, an
async generator using `asyncio.as_completed` (not `asyncio.gather`) to yield each
agent's result the instant it finishes, in real completion order, followed by a final
report — same concurrency A11 already proved, just observed as it happens instead of
all at once. `GET /scan/stream` turns that into Server-Sent Events. The frontend's
`streamScan()` (`lib/api.ts`) opens the connection via the browser's built-in
`EventSource` and the input screen's waiting state now lights up each agent
individually, with its real duration, instead of a uniform pulse. Verified with a
DOM-polling instrument in the real browser (not assumed from the code): scanning
`wordpress.org` lit up `dns → tls → headers → recon → exposure` at
511/1170/1982/2312/2463ms — genuinely staggered. Both error paths (a rejected URL, a
lost connection) verified live and confirmed distinct. Note:
[`learning/16-live-progress-and-server-sent-events.md`](learning/16-live-progress-and-server-sent-events.md).

**A real gap, raised directly rather than glossed over:** `docs/DESIGN.md` describes
screen 2 as "the set piece" where "the animation budget goes," and describes glass
panels drifting on scroll. A16 delivers the real *data* behind that screen — genuine
per-agent completion timing, nothing fabricated — but not the drift mechanic or any
dedicated motion treatment. Arihant asked directly whether the frontend matched the
design brief; it was confirmed it does not, specifically on this point, and the
motion/artifact-styling layer was scoped as separate follow-up work rather than folded
silently into A16's own done-ness.

**A17 done.** `backend/report/pdf.py` rebuilds a finished `ScanReport` as a flat
HTML document — hand-reimplementing `Report.tsx`'s score-ring math and
`findings.ts`'s category grouping in Python, since there's no React on the
server — then hands that HTML to a headless Chromium via Playwright and asks it
to print. `POST /scan/pdf` takes the *whole report* as its body (not just a
URL), so the PDF always matches what's already on screen instead of risking a
second, possibly-different live scan. The frontend's new "Download PDF" button
(`Report.tsx`) POSTs the report it's already holding in state and saves the
response via a `Blob` + object-URL download — `frontend/lib/api.ts`'s
`downloadReportPdf()`. Verified end-to-end in the real browser (scanning
`wordpress.org`, clicking Download PDF, confirming a genuine `200 OK` with
`Content-Disposition: attachment` and a valid two-page `%PDF-1.4` file) and
directly via `curl` against `/scan/pdf`. Untrusted scanned-site text
(finding titles, evidence) is HTML-escaped before being embedded in the
generated page — verified by round-tripping a crafted finding with an HTML
tag in its title, both escaped (renders as literal text) and, deliberately
un-escaped for the test, executing as real markup. Note:
[`learning/17-pdf-export-and-playwright.md`](learning/17-pdf-export-and-playwright.md).

The outstanding frontend motion/artifact-styling pass flagged above was completed
separately as `docs/PLAN.md` Part A (A-0 through A-4), before A18 began.

**A18 done — 18/18, the roadmap is complete.** Broken into six small steps
(`docs/PLAN.md` Part B, B-1 through B-6), each shipped and verified on its own
rather than landed as one batch:

- **B-1:** root [`README.md`](../README.md) — didn't exist before this. What
  Sentinels is, the passive-only ethic stated up front with a concrete
  never-list, the five-agent table, tech-at-a-glance, and two real screenshots
  (`docs/images/input-screen.png`, `docs/images/report-screen.png`) captured
  from an actual live scan via Playwright, not mocked up.
- **B-2:** Windows setup/run instructions folded into that same README —
  venv creation, `pip install`, `playwright install chromium`, `uvicorn`, then
  `npm install` + `npm run dev`. The Chromium gotcha was reproduced for real
  (temporarily removed the cached install, confirmed the exact blank-500
  failure, restored it, re-verified) rather than described from assumption.
- **B-3:** [`backend/.env.example`](../backend/.env.example) rewritten from a
  bare `GROQ_API_KEY=` into a commented file — what it powers, where to get a
  free one, and that the app is designed to work without it, cross-checked
  against `ai/analyst.py`'s actual graceful-degradation code.
- **B-4:** [`frontend/README.md`](../frontend/README.md) replaced — was still
  untouched `create-next-app` boilerplate advertising an unused font.
- **B-5:** a genuine clean-room test — fresh `git clone`, only the documented
  commands, nothing else — that **found a real bug**: `uvicorn --reload`
  silently breaks PDF export on Windows (uvicorn switches the process to
  `WindowsSelectorEventLoopPolicy` in reload mode; that loop can't start the
  subprocess Playwright needs). Fixed in
  [`backend/report/pdf.py`](../backend/report/pdf.py) — Playwright now runs on
  an explicit Proactor event loop inside `asyncio.to_thread` — and verified
  under the previously-failing exact command, plus confirmed the fix doesn't
  block the server (`/health` answered 9 times at 2-3ms each during a 2209ms
  PDF render). Full mechanism in
  [`learning/17f-event-loops-and-the-reload-trap.md`](learning/17f-event-loops-and-the-reload-trap.md).
- **B-6:** this note,
  [`learning/18-shipping-and-clean-room-verification.md`](learning/18-shipping-and-clean-room-verification.md),
  plus this roadmap update and the matching `ACTIVITY_LOG.md` entry.

**The project is genuinely finished per its own definition of done:** every
achievement's code runs, every achievement has a learning note, and A18's own
bar — a stranger's clean clone completing a real scan with no undocumented
step — was met and proven, not assumed.

---

## Beyond this roadmap

The original 18 achievements above are the whole of Act 1-5's scope, and
this file's `[x]`/18/18 status is unchanged since A18. Further work happens
in its own numbered plan, each with the same "no code without its learning
note" discipline, tracked in its own status header rather than back here:

- [`docs/PLAN-v2.md`](PLAN-v2.md) — architecture extension (scans, evidence,
  the deployment checklist, AI fix suggestions). Complete.
- [`docs/PLAN-v3.md`](PLAN-v3.md) — GitHub repo scanning as a second scan
  type. Complete.
- [`docs/PLAN-v4.md`](PLAN-v4.md) — three new passive attack-surface agents
  (API Security, Misconfiguration, Subdomain Security), scoring dedup/decay/
  caps to keep an 8-agent scan honest, and the full test matrix behind it.
  Complete, V1-V10 — see that file's own header for the milestone-by-milestone
  record.
