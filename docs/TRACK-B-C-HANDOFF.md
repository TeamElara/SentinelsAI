# Track B/C handoff and PR reviews

Reviewed 8 October 2026. This branch builds on `integration/launch` (PR #31),
which contains the A ownership, route audit, permission and pause controls,
the B network/limits stack and the C stack. It is a draft integration change;
teammate review and live release gates still apply.

## Handoff behavior

- Internal discovered hosts stay in the inventory, receive `skipped` coverage
  with the policy reason, and get no HTTP request or TLS handshake. The
  temporary `subdomain-internal-skipped` PASS finding is removed.
- Unresolved discovered hosts receive `unavailable`, rather than being
  described as private hosts. TLS policy refusals use the same distinction
  and produce no fabricated certificate finding.
- Deadline results retain their existing error for compatibility and now
  include explicit `failed` coverage. Reports remain provisional; coverage
  persists through the existing C2 storage and export paths.
- `/export/formats` advertises PDF only while `pdf_enabled()` is true. The
  dashboard loads that list and hides Download PDF when disabled or when
  the formats request fails. Disabled PDF never reserves quota.
- Actual URL/repo REST and streaming routes now use C7, as do PDF, verify,
  AI fix misses and chat. Ownership checks, website permission, cross-site
  rejection and A5 route dependencies remain before expensive work.
- `ScanBusy` refunds both the durable daily reservation and the original
  five-minute throttle ticket. Unique tickets prevent refunding another
  concurrent request. REST returns 429 with Retry-After; an already-open
  stream reports the busy message in a failed event.
- One request UUID resumes one account/operation/target-bound job. Closing
  the subscriber preserves that bounded job; reconnect does not charge or
  start again. Completion/deadline closes and awaits the orchestrator.
  Disconnect tests now verify this C7 contract. Direct orchestrator cleanup
  tests still verify cancellation and socket closure.
- The obsolete integration patches and skipped seam tests are removed;
  real-route tests replace them. `/usage` is part of A2's signed-in inventory.

## B2 review: PR #20

Exact reviewed head: `e47197b0dc30368798e382f7f6a08953d0698307`.

**P2: connect timeout does not bound DNS and address retries.**
`PolicyBackend.connect_tcp` awaits DNS outside the timeout and passes the
full timeout to each candidate address. The deterministic, socket-free
reproducer `backend/scripts/review_b_connect_timeout.py` loads that exact
head. A 5 ms budget waited about 52 ms for injected DNS and then connected;
two attempts each received a fresh 20 ms budget. B4/B7 bound normal DNS
lookups separately, but do not fix this per-connect budget.

Fixed on this branch: one cancellable asyncio deadline covers DNS and all
socket attempts; each attempt receives only its remaining allowance.
Regression tests verify DNS cancellation before connecting and a reduced
timeout on the second address. The pinned-address, redirect, private-address,
proxy and hostname-certificate tests remain in the integrated suite.

No SSRF bypass was demonstrated by this finding. Approval of the original
head should account for this fix or a Track B equivalent.

## B3 review: PR #21

Exact reviewed head: `ea2517a046c4414da15bc71c506242dac659fb3f`.

**P2: unresolved DNS is reported as an internal skipped host.**
`_is_scannable` collapses every `BlockedTarget` into False; the temporary
finding then claims all refused hosts have private addresses. The C2 handoff
above replaces that claim with explicit skipped/unavailable observations.

**P2: raw TLS address retries restart the socket timeout.**
Each vetted address previously received the full timeout, so a long DNS
answer could keep a worker thread alive after its agent was cancelled.
This branch gives TCP retries and the subsequent TLS handshake a shared
remaining budget, checked after the independently bounded synchronous DNS
lookup. The socket is still opened to a vetted IP and SNI remains the
hostname. Synchronous DNS itself has B7's per-query lifetime; this does not
claim that a cancelled Python task can stop a running worker thread.

No outbound-policy bypass was found in the reviewed wiring. Review tests
exercise actual loopback TCP/TLS under an injected public-address backend;
no real DNS or target scans are used.

## Database contention discovered during verification

The actual local libSQL SDK intermittently rejected `BEGIN IMMEDIATE` in
the 20-connection quota race. The adapter now retries only explicit SQLite
lock failures for at most five seconds, with the SDK's native busy timeout
disabled and sleeping in Python so the lock-owning
thread can commit. Constraint failures and ambiguous remote/network failures
are not retried. A real two-writer test verifies both writes commit, and the
existing quota race must still admit exactly five starts.

The SDK maps its `timeout` argument to SQLite's busy timeout, rather than
an HTTP connection timeout ([libSQL 0.1.11 source](https://github.com/tursodatabase/libsql-python/blob/v0.1.11/src/lib.rs#L181)).

## Evidence and limits

- Python 3.13 and Node 24 are configured in CI. Both database-driver jobs
  and frontend tests/lint/build are required on the pushed head.
- [Chromium UI/header evidence](evidence/b-c-browser.json) checks the real
  local production build: permission checkbox, two wake probes roughly five
  seconds apart, one stream, provisional coverage, PDF 429, disabled PDF
  button, framing rejection and origin-only referrer.
- A real sandboxed Chromium PDF was generated with PDF enabled only in the
  verification process. Text extraction and rendered-page inspection verified
  skipped/failed coverage and provisional messaging. The server default stays off.
- Production npm and Python dependency audits found no known vulnerabilities.
  The previously documented dev-only ESLint dependency advisory remains.

This does not verify remote Turso transactions/restore/latency, real Render
redeploy or wake-up, Vercel SSO-protected application behavior, OAuth, or
Chromium memory on Render. Credentials/dashboard access are still absent.
No main merge, public beta launch or formal GitHub review approval is implied.
