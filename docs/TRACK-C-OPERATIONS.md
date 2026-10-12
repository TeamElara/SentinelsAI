# Track C: remaining live operations

This supplements the acceptance tooling merged in PR #43 and
[`LAUNCH-RUNBOOK.md`](LAUNCH-RUNBOOK.md), merged in PR #39. Its row numbers are
that runbook's smoke-test rows. The team is handling the production deployment;
merging a PR does not prove that Render runs its code.

## Order and identities

1. Accept the Vercel invitation using **Mahatva's own Vercel account** when it
   arrives. The current connector identifies as `organizationelara-9811`, not
   Mahatva; its metadata access is not proof that Mahatva accepted an invitation.
   The available browser currently requires sign-in. No invite was accepted.
2. Put disposable Turso source and restore-target credentials directly into
   the **same process environment that runs the command**. Variables entered
   in a different terminal do not automatically propagate to an already running
   agent process. Never paste them into chat, record them in evidence, or use
   `set -x`/a transcript while entering them. Required names:
   `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `TURSO_RESTORE_DATABASE_URL`,
   `TURSO_RESTORE_AUTH_TOKEN`. Both databases must be empty and disposable.
3. With current `main` in the checkout, run from `backend/`:

   ```sh
   python scripts/check_database_acceptance.py --disposable --output acceptance-turso.json
   ```

   Require exit 0, `backup_restore.target = remote`, and successful source and
   restored-data reads in new processes. This is separate from production.
4. Preserve existing production data and confirm production Turso settings
   before updating Render. The service was still at September 19 `c79c285`
   during the latest audit; current main requires a remote database at startup.
5. After the team deploys the reviewed backend, run:

   ```sh
   python scripts/check_deployment_contract.py --backend https://sentinelsai.onrender.com --frontend https://sentinels-ai-sage.vercel.app --output acceptance-contract.json
   ```

   Require `/usage` 401 without a cookie and no PDF in `/export/formats`, both
   directly and through the frontend proxy. Health 200 alone is insufficient.

**Account C is intentionally not invited.** Mahatva's row 2 test must finish
the real GitHub sign-in and return to `/login` with the invite-only message,
no usable Sentinels session, and protected APIs still denying access. A mocked
OAuth test or manually opening an error URL does not close this row. Do not
temporarily invite C to make other smoke tests pass.

Rows 14 and 17 require a separate **invited account A** session. Use its
authenticated browser after the account owner signs in; keep cookies out of
chat and artifacts. Row 14 records the allowance before/after real scans of a
team-owned target, the quota badge reaching zero, the next request's 429 and
Retry-After, then a disconnect/reconnect using the same request ID. Confirm
one stored report and one charge for that job. Rate-limit rejections alone are
not evidence of the daily quota gate. If the burst limit is hit, wait for its
reset rather than changing quotas or bypassing the limiter.

For row 17, record a test report ID, owner, pre-redeploy usage count and working
session privately. After the team's authorized redeploy, confirm the same
session still works, the same report/ownership remains and usage did not reset.
Do not publish cookies, session hashes, scan content or raw network traces.

## Weekly encrypted production backups

`.github/workflows/database-backup.yml` is a manual/weekly **project workflow**,
not a Codex reminder. It runs from reviewed `main` only, on Sundays at 03:17 UTC
(08:47 IST), with one backup at a time and a 15-minute bound. It does not restore,
migrate, restart, redeploy, scan or call the Render API. The scheduled source is
Turso directly, so a backup does not wake a sleeping Render service.

The repository is public. GitHub artifacts can be downloaded by signed-in
people with repository read access, so the workflow uploads **only encrypted
`.sbk` archives**, never raw SQLite, environment files or decryption keys.
Archives use AES-256-GCM with a fresh random nonce and an authenticated version
header. Export validates SQLite integrity/foreign keys and verifies encryption
before publishing. It refuses an existing destination and a blank/non-Sentinels
database. Plaintext snapshots live only in a private temporary directory.
The initial archive limit is 256 MiB; oversized snapshots fail without uploading
a partial recovery point. Revisit the memory/storage approach before increasing it.

Before enabling, configure GitHub environment `production-backups` restricted
to `main`, with the following **environment secrets** entered securely:

| Secret | Purpose |
| --- | --- |
| `TURSO_BACKUP_DATABASE_URL` | The actual production database, never either disposable DB. |
| `TURSO_BACKUP_AUTH_TOKEN` | A production read-only token with permission for consistent transactional reads. Validate it with the first real export. |
| `SENTINELS_BACKUP_KEY` | Base64 of 32 cryptographically random bytes; separate from the session secret and Turso token. |

Generate/store the encryption key through a trusted terminal or secret manager;
keep an independent recovery copy in the team's approved secret manager.
Do not print it into chat/logs. Old archives still require their original key
after rotation; retain key versions for the retained recovery points.
The agent has not generated/uploaded production credentials or enabled the job.

Set repository variable `SENTINELS_BACKUPS_ENABLED=true` only after the workflow
is merged, the environment/secrets are ready and a recovery copy of the key is
stored. Dispatch one manual run; verify a successful encrypted artifact, download
it, decrypt privately and restore into an empty disposable Turso target. Record
the successful run ID and restore evidence before counting backups as operational.
Monitor workflow failures through the owner's GitHub Actions notifications.
Rehearse restores periodically. Scheduled workflows can be delayed or disabled
after inactivity; check actual run history rather than treating the schedule as
proof that backups exist. Retention is 90 days, subject to repository policy.

For a one-time production backup, select production Turso credentials in a
separate terminal context and set the same encryption key securely:

```sh
cd backend
python scripts/encrypted_database_backup.py export /private/place/production-backup.sbk
python scripts/encrypted_database_backup.py decrypt /private/place/production-backup.sbk /private/place/restored-snapshot.db
```

The CLI reads the process environment and never loads `.env`. To restore, switch
`TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` to an **empty disposable restore target**,
then use `python scripts/database_backup.py restore /private/place/restored-snapshot.db`.
The existing restore CLI loads `.env` without overriding variables already in
the process; set the target variables explicitly before running it. Never run
restore against production. Remove the private plaintext snapshot after recovery
verification according to the team's data handling practice.

## UptimeRobot and row 19

The UptimeRobot dashboard currently requires sign-in; no account/API credential
or connected UptimeRobot integration was available, so no monitor was created.
Use the team's existing account and create or reuse **one** monitor with:

| Setting | Value |
| --- | --- |
| Name | Sentinels API health |
| Type | HTTP(S) |
| URL | `https://sentinelsai.onrender.com/health` |
| Interval | 5 minutes / 300 seconds |
| Expected response | HTTP 200; verify its JSON reports `status: ok` |
| TLS verification | Enabled |
| Alerts | Existing verified account email/contact; confirm selected contact and delivery. |

A 5-minute monitor prevents the service reaching 15 minutes idle. Run row 19
**before activating the monitor**, or pause this monitor and any other probes
for that test, then resume them. Do not poll `/health` to see whether it has gone
to sleep: that polling itself keeps it awake. After at least 15 minutes genuinely
idle, open the URL-scan flow with account A and record "Waking up the scanner…",
then successful completion. Simply loading a static frontend page may not call
the backend. Record the observed wake duration; do not substitute local fixture
timings. Activate/resume monitoring afterwards and verify the 300-second setting,
successful check history and notification contact. Health monitoring proves
liveness; the deployment-contract and database gates remain separate.

References: [GitHub artifact access](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts),
[scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[artifact retention](https://docs.github.com/actions/configuring-and-managing-workflows/persisting-workflow-data-using-artifacts),
[UptimeRobot monitoring intervals](https://help.uptimerobot.com/en/articles/11360876-what-is-a-monitoring-interval-in-uptimerobot).
