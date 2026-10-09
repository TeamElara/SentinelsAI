# C2: coverage is an execution record

Each declared agent check records completed/partial/skipped/unavailable/failed
with a reason. Coverage is independent of finding severity or pass/fail: a
completed check may have found a serious issue. Per-task context isolates
concurrent agents and propagates into DNS/TLS worker threads.

Probe failures, robots refusals, request/time budgets, OSV failures/incomplete
responses, invalid dependency manifests, unresolvable dependency specifications,
file-read failures and findings/discovery limits record explicit limitations.
HTTP TLS checks record skipped certificate/protocol checks, and failed verified
handshakes record which certificate details remain unavailable. Subdomain
discovery failures and unobserved inventory hosts cannot silently become clean.
Supported manifests with no pinned packages record a non-required scope skip.

When a shared observation is unavailable, this implementation conservatively
marks the agent's declared checks partial/unavailable rather than guessing
which checks depended on it. A leaf entry retains the exact limitation.

Migration 18 adds agent_runs.coverage_json; versions 15 and 17 remain reserved
for Track A and C7. Old rows keep empty coverage and therefore become
provisional when read; their stored scores and scorer versions stay unchanged.
Checklist pass/unknown items from incomplete agents become unknown; observed
failures/warnings remain visible. Incomplete reports cannot have ready status.

JSON, Markdown and the shared HTML/PDF renderer carry coverage and reasons.
The dashboard, progress, agent cards/details and checklist display limitations.
The checklist now uses the backend readiness rules, including repo blockers.
AI summaries receive the incomplete-coverage constraint, and a deterministic
notice remains present even if the provider is unavailable.

Validation: 546 backend tests pass; Node 24 build and lint pass. A real
Chromium-generated PDF was rendered and visually reviewed, and extracted text
contains provisional, unavailable and skipped states. Reproduce with:

    cd backend
    python scripts/check_report_exports.py ../tmp/coverage-exports

Track B integration must retain coverage calls in probe/TLS/subdomain files and
the narrow finalization changes in both orchestrators. Track A integration must
retain agent coverage serialization and the coverage-aware report read path.
