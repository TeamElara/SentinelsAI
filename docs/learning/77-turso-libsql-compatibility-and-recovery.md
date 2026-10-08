# C6: Turso compatibility spike and recovery

Use the maintained libsql 0.1.11 binding, rather than the archived
libsql-client package. A configured TURSO_DATABASE_URL selects a direct remote
primary connection. No embedded replica is used: quota and session decisions
must not depend on stale local reads. Render startup refuses local SQLite.

The storage API needs named rows, parameterized SQL, rowcount/lastrowid,
transactions, foreign keys and partial unique indexes. The SDK returns tuple
rows and ValueError for SQL failures; its executescript implementation can
silence failures. libsql_adapter supplies named rows/SQLite constraint errors
and executes each complete SQL statement with errors propagated. Migration
transactions remain controlled by db.init_db, not the script helper.

Local spike evidence on Python 3.13: all 551 backend tests pass with both
sqlite and the actual libsql binding. Fresh/incremental migrations,
rollback, storage CRUD, session reopening and the partial-index contract pass.
CI now repeats the full suite on Linux with both drivers.

Backups are consistent logical SQLite snapshots of the primary. They include
all tables, schema/index/trigger SQL, migration ledgers and AUTOINCREMENT
high-water marks. The exporter refuses overwrite; restore refuses a nonempty
target and checks foreign keys before committing. Tests prove restored sessions
and scans remain usable and the active-application partial index survives.

    cd backend
    python scripts/database_backup.py export <private-backup-path>.db
    # Configure an EMPTY recovery database, then:
    python scripts/database_backup.py restore <private-backup-path>.db

## Remaining live release evidence

No Turso/Render credentials are present in this workspace, environment or
GitHub repository secrets as of 2026-10-08. Local SDK compatibility is NOT remote
Turso validation. Before production activation:

1. Configure a disposable Turso database in backend/.env with URL/token. Run
   all migrations, CRUD, FK/index and parallel quota checks against its primary.
2. Check real request latency and event-loop responsiveness under failed/slow
   database calls. The binding is synchronous; its connection timeout is a
   SQLite busy timeout, not proof of a bounded remote request. Keep this as a
   launch gate and do not describe local tests as solving it.
3. Export a snapshot; restore it into a second empty disposable Turso database.
   Reopen the session and report with a new process and compare row counts.
4. Set the production URL/token in Render after the reviewed integration is
   ready, snapshot the existing data first, redeploy, and confirm a signed-in
   session and scan report survive the redeploy. Retain the rollback snapshot.

If the remote spike fails any required contract, do not launch the adapter.
Neon requires a separate schema/storage migration for PostgreSQL parameter
style, AUTOINCREMENT, PRAGMAs and conflict clauses; it is not a driver swap.
The user selected Turso, so no new paid database/project was provisioned.

References: [official Python SDK](https://docs.turso.tech/sdk/python/quickstart),
[maintained binding source](https://github.com/tursodatabase/libsql-python),
[Render disk limits](https://render.com/docs/free).
