# Mahatva — Track C implementation and release status

Updated 8 October 2026. All prepared code is committed and pushed. These are
draft review branches; no changes have been merged into main or enabled as a
public beta. The adopted final plan requires teammate review and the live gates.

Latest follow-up: [Track B/C handoff and reviews](TRACK-B-C-HANDOFF.md) on
`codex/b-c-handoff`, based on the team's combined integration PR #31. It
implements the requested B/C coverage/PDF/quota contracts and activates C7
on the actual A-protected routes in the draft integration branch. C7 is
still inactive on main until the reviewed integration is merged. Original
task PRs below retain their historical dependency gates.

| Task | Review | Implemented evidence / remaining gate |
| --- | --- | --- |
| C1 | [PR #1](https://github.com/TeamElara/SentinelsAI/pull/1) | Canonical penalties, deterministic duplicate handling, permutation and stream parity tests. |
| C4 | [PR #3](https://github.com/TeamElara/SentinelsAI/pull/3) | Python 3.13 / Node 24 alignment; patched production dependencies; audit checks in CI. Hosting runtime settings need deployment access. |
| C10 | [PR #6](https://github.com/TeamElara/SentinelsAI/pull/6) | Fixed system instructions, marked user-role evidence, history filtering, prompt version v4; injection tests. |
| C8 | [PR #8](https://github.com/TeamElara/SentinelsAI/pull/8) | Wrong key, 429, cached fix and stored-summary regressions. C7's prepared route patch removes the early key check; until activation, the old HTTP cache guard remains. |
| C3 | [PR #10](https://github.com/TeamElara/SentinelsAI/pull/10) | Migration 16, scorer provenance, historical score preservation, migration ledger, verification/export version fields. |
| C2 | [PR #14](https://github.com/TeamElara/SentinelsAI/pull/14) | Explicit coverage/reasons, provisional incomplete grades, checklist and stored/export/UI parity; migration 18 leaves 15/17 available. Review coverage seams with Track B. |
| C6 | [PR #15](https://github.com/TeamElara/SentinelsAI/pull/15) | Actual maintained libSQL SDK, named-row/error adapter, full local compatibility suite, consistent snapshot/restore tooling. **Remote Turso acceptance, latency, restore and Render redeploy remain open.** |
| C5 | [PR #16](https://github.com/TeamElara/SentinelsAI/pull/16) | Existing Vercel + Render + Turso decision and provider constraints documented. Account owner must confirm Hobby eligibility/dashboard settings. |
| C7 | [PR #19](https://github.com/TeamElara/SentinelsAI/pull/19) | Migration 17, atomic budgets, global AI cap, idempotent refunds, resumable bounded jobs, clear 429 UI. **Route activation waits for A1/A2 merges.** Tested main/permission patches preserve A5/A6/A4 contracts; adapt A27 disconnect tests for bounded detached jobs. |
| C9 | [PR #26](https://github.com/TeamElara/SentinelsAI/pull/26) | Three-second health timeout, five-second retry cadence, visible wake state, cancellation and bounded wait; real Chromium fixture check. Live Render sleep/wake pending. |
| C11 | [PR #30](https://github.com/TeamElara/SentinelsAI/pull/30) | Reuses [PR #13](https://github.com/TeamElara/SentinelsAI/pull/13) headers. Direct production-build HTTP/browser evidence is committed. Vercel SSO blocks live app checks; CSP inline-script allowance remains an explicit limitation. |

## Review order

The C stack is #1 → #3 → #6 → #8 → #10 → #14 → #15 → #16 → #19 → #26 → #30.
Each PR is based on its preceding branch so reviewers can inspect the task's
own diff. After a predecessor merges, retarget its successor and rerun CI.
The top branch `codex/c11-header-verification` contains the whole C stack and
the existing C11 header implementation. Do not bypass teammate review.

## Verified on the final code

- 574 backend tests pass with SQLite and with the actual local libSQL SDK on Python 3.13.
- Eight frontend transport/health tests, Node 24 production build and lint pass. Lint retains two auth-navigation warnings; backend tests retain one TestClient deprecation warning.
- Chromium verifies wake-up timing, one stream after waking, visible provisional coverage/reasons, clear PDF 429 messaging, framing rejection and origin-only referrers. See [local evidence](evidence/c11-local.json).
- A real Chromium PDF was generated and rendered during C2 verification; its JSON/Markdown/HTML/PDF contain the same incomplete coverage.
- Migration, sessions, scans, foreign keys, partial indexes and logical backup/restore round-trip through both local database drivers.

## Remaining release requirements

Turso/Render/Vercel deployment credentials or dashboard access are not available
in this workspace. The live preview responds with Vercel SSO, recorded as
[pending evidence](evidence/c11-live.json). No live database durability, provider
eligibility, OAuth flow or public-beta readiness is claimed from offline tests.

Follow the C6 recovery runbook, configure the remote primary and single-worker
Render runtime, repeat remote quota races and restore checks, then test real
sign-in, streams, PDF, settings and sleep/wake behavior through Vercel. The
libSQL native SDK's remote blocking/GIL behavior needs a measured latency and
event-loop responsiveness gate. Keep PDF off on Render until Track B's sandbox
and Chromium resource checks pass. Integrate C7 only after ownership safeguards
and their route audit land; preserve beta, permission and write kill switches.

The public-beta gates remain open. Production dependency fixes are in the
review stack; GitHub's default-branch alerts can remain until those PRs merge.
The dev-only ESLint chain also has an unresolved braces advisory without a
compatible upstream patch, documented with C4.
