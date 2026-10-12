# Track C acceptance — 12 October 2026

Track C's implementation is merged through PRs #32 and #31. This follow-up
adds repeatable C6/C7 database acceptance and records the remaining live gates.
The audited main revision is `74e8efb0a51d0d76d8dd1141ccd5f49a2d6a0aa2`.
No provider settings, production rows or teammate branches were changed.

Validation: the updated 1,015 backend tests passed in GitHub CI on Python 3.13
under both SQLite and libSQL, including all nine new acceptance-command tests.
Both push and PR CI runs succeeded; PR run: `38203653945`. Frontend tests
(8), production build and lint passed on Node 24; lint retained two existing
navigation warnings. Both production dependency audits (`pip-audit` and
`npm audit --omit=dev`) reported zero known vulnerabilities. The full npm audit
still reports five high-severity development dependency entries, rooted in
`braces` through the Next ESLint tooling (GHSA-vfj7-8cjw-p6xm); its suggested fix
downgrades the Next ESLint configuration to 14.2.35, so no forced downgrade was
applied. Teammate PR #35 separately handles the AnyIO development pin.

## Database command

From `backend/`, using Python 3.13 with `requirements-dev.txt` installed:

```sh
python scripts/check_database_acceptance.py --local-driver sqlite --output acceptance-sqlite.json
python scripts/check_database_acceptance.py --local-driver libsql --output acceptance-libsql.json
```

Local mode creates temporary databases, ignores remote credentials and Render
settings, and removes the temporary snapshots afterwards. Both drivers verify:

- All migrations, their ledger, foreign-key enforcement, integrity and the active-fix partial index.
- Synthetic session/report storage and transaction rollback.
- Twelve concurrent quota reservations against a limit of five, idempotent refund and request replay.
- Event-loop responsiveness while twelve real driver connections/queries run in a thread.
- Source data in a separate Python process, then backup/restore with exact schema,
  row and AUTOINCREMENT comparisons, followed by another process reading the restore.

The responsiveness gate is a maximum 250 ms event-loop gap during these sample
queries. It is a small deployment smoke check, not a substitute for load testing
or a measurement of Render's cold start.

For **read-only checks against an initialized Turso database**, supply
`TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` through the process environment and run:

```sh
python scripts/check_database_acceptance.py --output acceptance-turso-readonly.json
```

This checks the schema, integrity and query responsiveness. It does not create
users, sessions or reports and does not call quota APIs that perform writes.
The script never automatically loads `backend/.env`.

For **full write acceptance**, configure a separate, empty, disposable Turso
database in those same environment variables, then run:

```sh
python scripts/check_database_acceptance.py --disposable --output acceptance-turso-disposable.json
```

The command refuses any pre-existing user schema before running migrations or
writing fixtures. It leaves its synthetic fixture rows in that disposable source;
do not point it at the deployed database. No real scan, GitHub login or AI call
is made. To also test **remote restore**, set `TURSO_RESTORE_DATABASE_URL` and
`TURSO_RESTORE_AUTH_TOKEN` to a second empty disposable database before this
command. Restore independently refuses a nonempty target. Without those optional
settings, restore uses a temporary local database and the report explicitly keeps
the second-Turso restore gate open. Backups and row manifests remain temporary;
JSON evidence contains no credentials, session hashes or report contents.

Exit codes: `0` checks passed, `1` checks failed/timed out, `2` credentials or a
live application response unavailable. A passed local/readonly report does not
mean that all deployment gates passed. Each child process has a 180-second bound.

## Frontend command

```sh
python scripts/check_frontend_headers.py --url https://YOUR-VERCEL-HOST --output acceptance-headers.json
```

This checks seven application/API/error paths plus browser login rendering,
framing rejection and referrer behavior. `--local-fixtures` additionally tests
the cold-start retry, one stream, provisional coverage and PDF quota/visibility
UI against mocked API responses; those are local fixture checks.
SSO redirects and network failures produce pending JSON evidence. Missing headers
and browser failures produce failed evidence. The checker does not bypass Vercel
deployment protection or authenticate as a user.

## Anonymous backend/proxy preflight

```sh
python scripts/check_deployment_contract.py --backend https://sentinelsai.onrender.com --frontend https://sentinels-ai-sage.vercel.app --output acceptance-contract.json
```

This sends only GET requests, without cookies, to `/health`, `/auth/me`, `/usage`
and `/export/formats`, directly and through Vercel's `/api` proxy. It requires
working health, a 401 on protected endpoints, and JSON/Markdown formats with PDF
disabled. Use `--allow-pdf` only after explicitly approving/measuring PDF on that
deployment. A healthy old backend cannot pass just because `/health` returns 200.

## Evidence and remaining work

| Gate | Result on 12 October |
| --- | --- |
| Temporary SQLite acceptance | Passed; [report](evidence/c6-sqlite-acceptance-2026-10-12.json). |
| Temporary libSQL acceptance | Passed; [report](evidence/c6-libsql-acceptance-2026-10-12.json). |
| Remote Turso | Pending credentials; [report](evidence/c6-turso-acceptance-2026-10-12.json). |
| Vercel runtime/configuration | Node 24.x verified; preview and production API base both match the audited Render service. Production alias and main commit verified; [metadata audit](evidence/c-vercel-audit-2026-10-12.json). |
| Main's Vercel deployment | Pending: deployment protection redirects to SSO; [report](evidence/c11-production-2026-10-12.json). |
| Public Vercel domain, C11 | Passed all seven HTTP paths and browser login/CSP/framing/referrer checks on retry; [report](evidence/c11-public-2026-10-12.json). Initial request timed out. |
| Render revision | Failed: live deployment is still September 19 commit `c79c285`, not audited main; [service audit](evidence/c-render-audit-2026-10-12.json). |
| Backend/proxy integration | [Contract report](evidence/c-live-contract-2026-10-12.json): `/usage` is missing and PDF is advertised on the old backend. |
| Real Render restart/redeploy | Pending verified database configuration, recovery point and an authenticated test session/report. |
| Deployed cold start and authenticated end-to-end flow | Pending live access; local fixtures cannot prove these. |

Main's deployment URL comes from GitHub deployment `6950930403`; the public
domain comes from the repository's `homepage`, not an assumed host name.
After the Vercel connection was confirmed, API metadata independently verified
that domain is an alias of production deployment `dpl_HYGP4YZZkMXrME9Cxw4wqvutnDzx`
at the audited main commit. PR #43's observed READY preview is recorded by commit
in the metadata audit. This verifies the intended frontend revision, rather than
inferring it from a healthy page. No protection setting was changed.

**Current deployment mismatch:** the connected Render account confirmed service
`SentinelsAI`, root `backend`, one instance and the expected build/start commands.
Its latest live deployment is `dep-dan8k4jm8hqs73aen10g` at September 19 commit
`c79c2851acf9e8b9347dad1c158718e90e719a29`. It has not deployed October's launch
integration, despite `autoDeploy=yes`. Live health therefore proves liveness only.
Do not enable a wider beta based on frontend/header success alone. First preserve
the existing database and confirm Turso configuration; blindly deploying current
main could discard an ephemeral database or fail its mandatory remote-DB startup.
No restart/redeploy was triggered during this audit.

To close C6, run the remote disposable acceptance including its second remote
restore. Then, on the **existing Render service**, deploy the reviewed current main
after the backup/configuration gate and record the deployed revision,
Python version, single-worker command and remote database configuration; create
an authorized test session/report, restart/redeploy, and confirm that session,
report ownership and quota counts survive. Preserve a production recovery point
before any real recovery rehearsal. Record timestamps and restore results;
never restore over a populated database.

Public C11 header/browser checks are complete for the recorded domain and date.
To close C9 and the full live flow, record an idle-service wake-up and an
authenticated scan/report flow against the updated backend. Render read access
became available during the audit; its available tools do not provide environment
reads, service-shell execution or database export. The confirmed Vercel connection
can read project/deployment metadata and the public API configuration through its
default scope. Explicit `elara17`/account scope requests and the authenticated
protected-deployment fetch still return 403. No authenticated Vercel CLI was
available. Turso credentials were absent. The remaining access-dependent checks
need protected-deployment permissions and a secure backend/Turso session.
