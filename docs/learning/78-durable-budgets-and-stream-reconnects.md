# C7 — durable budgets and reconnects

Migration 17 adds atomic per-account UTC-day counters, reservations, global AI
attempt counters and stored scan-job identities. URL REST and streams share
`url_scan`; repository REST and streams share `repo_scan`. Defaults are 10 each
for URL, repo, verification and PDF, and 20 each for AI fixes and chat. These
are beta operating choices, configurable with `SENTINELS_DAILY_<KIND>`.
`SENTINELS_DAILY_GLOBAL_AI` defaults to 100 actual provider attempts per UTC day.

Reservations charge when work starts. Completion keeps the charge. Internal
failure, cancellation, timeout, absent AI result or malformed AI fix refunds
exactly once. Rejected targets after starting keep their charge. Global AI
attempts are not refunded, because failed calls can still consume provider
quota. Cache reads and report/history reloads do not call AI or use AI budgets.

A stream uses a UUID bound to account, kind and target. A reconnect receives
the buffered events of the original detached task. A completed job can replay
its owned stored report after a restart. A different target with the same ID
returns 409. An orphaned job from another worker is refunded after 180 seconds;
work has a 150-second deadline. Reconnects never launch another runner. The
browser uses fetch SSE to expose HTTP 429 details and Retry-After, retries at
most three times with the same ID, and cancels its transport on unmount.

Beta uses **one Uvicorn worker**. At most two scans and one PDF render execute
concurrently; stream admission is bounded to eight pending jobs. Distributed
worker leases and event delivery are required before scaling to multiple
workers. The previous short-window scan limiter remains a separate burst guard.
The UI shows only server-confirmed remaining budgets and the UTC reset time.

## Activation gate — still open

The adopted plan explicitly says “Wire into main.py after A1/A2 merge.” PR #2
provides A1; its description says A2 is next, and neither is merged. Therefore
`main.py` is unchanged. `integration/c7-main-after-ownership.patch` is the
prepared integration against A1 commit 8273a5a. Regenerate it against the merged
tree with `python backend/scripts/prepare_usage_integration.py --output
integration/c7-main-after-ownership.patch`, review it, then apply with `git apply`.
The generator refuses a tree without the ownership helper and cross-site guard.
The generator preserves A5's route dependencies and A6's request type,
permission parameter and permission checks before charging. A4 removes the
PDF alias; regeneration never restores it. If the alias still exists before
A4 lands, the draft makes it reload only an owned stored report.

The route seam harness tests the prepared patch, including ownership before
charging, cross-site rejection, REST/stream sharing, reconnect, stored PDF
reload and failure refund. It does **not** replace A2's route-walking audit.
Run that audit after applying the patch and before enabling public scans.
Until activation, the old routes do not enforce the new daily budgets and the
usage UI stays hidden when `/usage` is unavailable. Do not deploy the current
head as a public beta on the strength of these offline tests.

Additional integration seams: B4's `ScanBusy` is refunded and translated to
HTTP 429; malformed target ValueErrors still keep their started allowance.
The detached job closes its runner at completion/failure/deadline. A27's browser
disconnect cancellation must be adapted when C7 activates: a disconnect closes
only the subscriber; the bounded job stays alive so reconnect can join it.
Adapt that regression test to assert no duplicate runner and eventual cleanup.
After A6's checkbox lands, pass its actual boolean as the third argument to
`streamScan`; C7's default is false and never invents a permission confirmation.
`integration/c7-permission-ui.patch` prepares that call. The generator accepts
`--frontend-source frontend/components/ScanLauncher.tsx --frontend-output
integration/c7-permission-ui.patch` and refuses a launcher without A6's checkbox.
PDF budget errors now retain the server's specific message on the report page.

## Evidence and remaining live checks

Twenty simultaneous connections competing for a limit of five yield exactly
five accepted reservations on SQLite and the actual local libSQL SDK. Tests
also cover restart/day rollover, stale-worker recovery, idempotent refunds,
all six operation budgets, cache races, AI outages and global exhaustion.
The full backend suite passes 570 cases on libSQL; frontend streaming tests
pass four cases, with a passing Node 24 production build and lint (two existing
auth-navigation warnings). CI now runs those frontend tests too.

After Turso credentials are configured, repeat concurrent reservation tests
against the real primary and the Render redeploy gate. The C6 remote SDK/GIL
and latency gate remains open. No real remote quota, billing cap or redeploy
durability has been claimed from local SDK tests.
