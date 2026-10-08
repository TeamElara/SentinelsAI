"""SQLite connection and schema management.

Stdlib `sqlite3`, no ORM — the query surface across the whole v2 plan is
small (~12 queries total), so an ORM would be a dependency for a problem
that doesn't need one. This also keeps Sentinels at zero new third-party
dependencies for persistence.

Migrations are just an ordered list of (version, sql) pairs applied in
order past whatever `schema_version` currently holds. M1 only ships
version 1 (scans, agent_runs, findings); later milestones (M4, M9, M13,
M15) append further versions here rather than editing this one.
"""
from __future__ import annotations

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "sentinels.db"

_V1_SCHEMA = """
CREATE TABLE scans (
    id                TEXT PRIMARY KEY,        -- uuid4
    url               TEXT NOT NULL,
    scanned_at        TEXT NOT NULL,           -- ISO 8601 UTC
    duration_ms       INTEGER NOT NULL,
    score             INTEGER NOT NULL,        -- 0-100 security score
    grade             TEXT NOT NULL,           -- A-F
    summary           TEXT DEFAULT '',         -- AI, may be empty
    readiness_score   INTEGER,                 -- NULL until M10
    deployment_status TEXT,                    -- ready | caution | blocked
    created_at        TEXT NOT NULL
);
CREATE INDEX idx_scans_created ON scans(created_at DESC);

CREATE TABLE agent_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    agent       TEXT NOT NULL,                 -- slug: "headers"
    duration_ms INTEGER NOT NULL,
    error       TEXT,
    verdict     TEXT                           -- clean | issues_found | failed, NULL until M8
);

CREATE TABLE findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    finding_key TEXT NOT NULL,                 -- Finding.id, e.g. "missing-csp"
    agent       TEXT NOT NULL,                 -- which agent produced it
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL,
    status      TEXT NOT NULL,
    owasp       TEXT,
    evidence    TEXT DEFAULT '',
    description TEXT DEFAULT '',
    remediation TEXT DEFAULT ''
);
CREATE INDEX idx_findings_scan ON findings(scan_id);
"""

_V2_SCHEMA = """
CREATE TABLE evidence_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id   INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,   -- request|response_headers|dns_record|certificate|html_snippet|log|screenshot
    label        TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_type TEXT DEFAULT 'text/plain',
    collected_at TEXT NOT NULL,
    agent        TEXT NOT NULL
);
CREATE INDEX idx_evidence_finding ON evidence_items(finding_id);
"""

_V3_SCHEMA = """
CREATE TABLE checklist_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id       TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    item_key      TEXT NOT NULL,
    title         TEXT NOT NULL,
    tier          TEXT NOT NULL,   -- auto | inferred | self_attested
    state         TEXT NOT NULL,   -- pass | warn | fail | unknown
    explanation   TEXT NOT NULL,
    suggested_fix TEXT DEFAULT '',
    agent         TEXT             -- responsible agent slug, NULL for self_attested
);
"""

_V4_SCHEMA = """
CREATE TABLE fix_suggestions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id     INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    prompt_version TEXT NOT NULL,
    model          TEXT NOT NULL,
    content_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE(finding_id, prompt_version)
);
"""

_V5_SCHEMA = """
CREATE TABLE chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_chat_scan ON chat_messages(scan_id, id);
"""

# PLAN-v3 R3: generalize scans/findings to carry repo-scan data too.
# ADD COLUMN ... DEFAULT 'url' backfills every existing row automatically, so
# every scan taken before this migration reads back as target_type="url" --
# exactly what it always was.
_V6_SCHEMA = """
ALTER TABLE scans ADD COLUMN target_type TEXT NOT NULL DEFAULT 'url';
ALTER TABLE findings ADD COLUMN file_path TEXT;
ALTER TABLE findings ADD COLUMN line INTEGER;
"""

# The file-tree browser's backing table (R12 consumes this; nothing writes
# to it yet -- see models.RepoFileEntry).
_V7_SCHEMA = """
CREATE TABLE repo_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id       TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    size          INTEGER NOT NULL,
    language      TEXT,
    finding_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_repo_files_scan ON repo_files(scan_id);
"""

# PLAN-v4 V1: two additive columns for the three new URL-scan agents.
# Both are nullable with no default, so every finding written before this
# migration reads back as NULL -- which is exactly what it always meant:
# "this finding is about the scanned site itself" and "nothing to hedge".
_V8_SCHEMA = """
ALTER TABLE findings ADD COLUMN affected_url TEXT;
ALTER TABLE findings ADD COLUMN confidence REAL;
"""

# PLAN-v4 V6: the subdomain agent's structured inventory (models.SubdomainEntry),
# separate from `findings` because it's not a pass/fail check -- it's a list
# of hosts, most of which have no issue at all.
_V9_SCHEMA = """
CREATE TABLE subdomains (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id      TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    host         TEXT NOT NULL,
    record_type  TEXT NOT NULL,
    record_value TEXT NOT NULL,
    source       TEXT NOT NULL,
    http_status  INTEGER,
    scheme       TEXT,
    tls_valid    INTEGER,        -- 0/1/NULL, sqlite has no real boolean
    server       TEXT,
    redirects_to TEXT,
    issue_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_subdomains_scan ON subdomains(scan_id);
"""

# PLAN-v5 Stage 0: identity. Sentinels had no notion of "who" until now — fine
# for a read-only scanner, not fine for something that can open pull requests on
# your repositories. GitHub is the identity provider, so there are no passwords
# here to hash, leak, or reset.
#
# `sessions` stores only a *hash* of the session token, never the token itself —
# the same reason a password table stores hashes: a stolen copy of this database
# yields nothing a thief can present as a valid cookie.
#
# `scans.user_id` is nullable on purpose. Every scan taken before this migration
# has no owner and reads back as NULL, which is exactly what it always was:
# unowned. Those stay readable rather than being orphaned or deleted.
_V10_SCHEMA = """
CREATE TABLE users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id    INTEGER NOT NULL UNIQUE,   -- GitHub's own numeric id, stable across renames
    github_login TEXT NOT NULL,             -- the @handle, which CAN change
    avatar_url   TEXT,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,            -- sha256 of the cookie's token, never the token
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL                -- ISO 8601 UTC, checked on every request
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

CREATE TABLE github_installations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id  INTEGER NOT NULL UNIQUE,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_login    TEXT NOT NULL,
    repo_selection   TEXT NOT NULL,          -- "all" | "selected"
    permissions_json TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL,
    revoked_at       TEXT                    -- NULL while live
);
CREATE INDEX idx_installations_user ON github_installations(user_id);

ALTER TABLE scans ADD COLUMN user_id INTEGER;
ALTER TABLE scans ADD COLUMN commit_sha TEXT;
"""

# PLAN-v5 Stage A: the patch layer. `fix_plans` is what this stage actually
# writes -- one row per (scan, finding) holding the deterministic FixPlan a
# Fixer produced, replaced wholesale on every re-plan (see
# storage/remediation.py's INSERT OR REPLACE). `fix_applications` and
# `audit_log` land in this same migration but stay unwritten until Stage B
# actually applies a plan -- the same precedent `_V7_SCHEMA` set for
# `repo_files`: the table exists ahead of the milestone that first writes to
# it, so that milestone isn't also a migration.
_V11_SCHEMA = """
CREATE TABLE fix_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id     TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    finding_key TEXT NOT NULL,
    fixer_slug  TEXT NOT NULL,
    tier        INTEGER NOT NULL,
    summary     TEXT NOT NULL,
    plan_json   TEXT NOT NULL,               -- serialized FixPlan (patches + diffs)
    created_at  TEXT NOT NULL,
    UNIQUE(scan_id, finding_key)
);
CREATE INDEX idx_fix_plans_scan ON fix_plans(scan_id);

CREATE TABLE fix_applications (
    id          TEXT PRIMARY KEY,            -- uuid4
    scan_id     TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    fix_plan_id INTEGER NOT NULL REFERENCES fix_plans(id) ON DELETE CASCADE,
    finding_key TEXT NOT NULL,
    fixer_slug  TEXT NOT NULL,
    tier        INTEGER NOT NULL,
    state       TEXT NOT NULL,                -- planned|pr_open|merged|verified|failed|abandoned
    pr_url      TEXT,
    branch      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX idx_fix_applications_scan ON fix_applications(scan_id);

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id),
    scan_id     TEXT REFERENCES scans(id) ON DELETE SET NULL,
    finding_key TEXT,
    action      TEXT NOT NULL,               -- e.g. "plan_created", "pr_opened", "pr_merged"
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_audit_log_scan ON audit_log(scan_id);
"""

# PLAN-v5 Stage B (conflict #9): an audit row must never depend on a plan row
# surviving. `fix_plans` is INSERT OR REPLACE'd on every re-plan, which hands the
# row a *new* autoincrement id -- so the old `ON DELETE CASCADE` meant re-planning
# an already-applied finding would silently delete its `fix_applications` row,
# destroying the record of a PR that might still be open or already merged.
#
# Two changes fix that, and one enforces idempotency:
#
#   1. `plan_json` -- an immutable snapshot of the exact FixPlan that was applied.
#      The audit record now carries its own copy of what happened, so it stays
#      complete even if `fix_plans` changes or the row disappears entirely.
#   2. `fix_plan_id` becomes nullable with ON DELETE SET NULL -- a convenient
#      pointer at the live plan, never a dependency.
#   3. A *partial* unique index (the `WHERE` clause) -- SQLite only indexes rows
#      matching it, so a scan can hold many failed/abandoned attempts for one
#      finding but only ever one live application. This is the DB-level backstop
#      for the idempotency check in `remediation/apply.py`, not a replacement:
#      the application-level check runs first and gives a usable error message.
#
# SQLite cannot ALTER a foreign key in place, so the table is rebuilt through the
# standard create-new/copy/drop/rename sequence -- the same dance any SQLite FK
# change requires. `fix_applications` is empty in every database that exists
# today (Stage A never wrote a row), so the copy is a formality that keeps the
# migration correct for anyone who does have data.
_V12_SCHEMA = """
CREATE TABLE fix_applications_new (
    id          TEXT PRIMARY KEY,            -- uuid4
    scan_id     TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    fix_plan_id INTEGER REFERENCES fix_plans(id) ON DELETE SET NULL,
    finding_key TEXT NOT NULL,
    fixer_slug  TEXT NOT NULL,
    tier        INTEGER NOT NULL,
    state       TEXT NOT NULL,                -- planned|pr_open|merged|verified|failed|abandoned
    pr_url      TEXT,
    pr_number   INTEGER,
    branch      TEXT,
    plan_json   TEXT NOT NULL DEFAULT '',     -- frozen snapshot of the applied FixPlan
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

INSERT INTO fix_applications_new
    (id, scan_id, fix_plan_id, finding_key, fixer_slug, tier, state, pr_url, branch, created_at, updated_at)
SELECT id, scan_id, fix_plan_id, finding_key, fixer_slug, tier, state, pr_url, branch, created_at, updated_at
FROM fix_applications;

DROP TABLE fix_applications;
ALTER TABLE fix_applications_new RENAME TO fix_applications;

CREATE INDEX idx_fix_applications_scan ON fix_applications(scan_id);
CREATE UNIQUE INDEX idx_fix_applications_active
    ON fix_applications(scan_id, finding_key)
    WHERE state NOT IN ('failed', 'abandoned');
"""

# V13 (PLAN-v5 Stage C): the verification a fix was actually closed out with --
# the score before, the score after, and which of the agent's findings really
# went away. Stored as JSON on the application row rather than in its own table
# because it is one-to-one with an application and never queried across rows:
# it is the *evidence* for that row's `verified` state, not a separate record.
#
# A plain ADD COLUMN this time -- nothing about the table's constraints changes,
# so none of migration 12's rebuild dance is needed.
_V13_SCHEMA = """
ALTER TABLE fix_applications ADD COLUMN verification_json TEXT;
"""

# PLAN-v5 Stage D: a URL scan has no repository of its own -- `scan_repo_links`
# is what lets one borrow a repository's write path, so a header finding (no
# `file_path`, since it came from observing a live site) can still get a
# deterministic FixPlan. One row per scan (`scan_id` is the primary key): a
# URL scan targets one site, so at most one repository is ever "the thing
# that serves it." `ref` NULL means the repository's own default branch,
# same convention `fix_applications.branch` and friends already use.
_V14_SCHEMA = """
CREATE TABLE scan_repo_links (
    scan_id          TEXT PRIMARY KEY REFERENCES scans(id) ON DELETE CASCADE,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    installation_id  INTEGER NOT NULL,
    owner            TEXT NOT NULL,
    repo             TEXT NOT NULL,
    ref              TEXT,
    linked_at        TEXT NOT NULL
);
"""

_V16_SCHEMA = """
ALTER TABLE scans ADD COLUMN scorer_version TEXT NOT NULL DEFAULT 'legacy-v1';
"""

_V18_SCHEMA = """
ALTER TABLE agent_runs ADD COLUMN coverage_json TEXT NOT NULL DEFAULT '[]';
"""

_V17_SCHEMA = """
CREATE TABLE usage (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0),
    PRIMARY KEY(user_id, day, kind)
);
CREATE TABLE global_usage (
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0),
    PRIMARY KEY(day, kind)
);
CREATE TABLE usage_reservations (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    request_key TEXT NOT NULL,
    worker TEXT NOT NULL,
    created_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','completed','refunded')),
    UNIQUE(user_id, kind, request_key)
);
CREATE INDEX idx_usage_reservations_active ON usage_reservations(created_at) WHERE state='active';
CREATE TABLE scan_jobs (
    id TEXT PRIMARY KEY REFERENCES usage_reservations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    scan_id TEXT REFERENCES scans(id) ON DELETE SET NULL,
    error TEXT
);
"""

# Version 15 is reserved for Track A's users.blocked migration. The ledger
# below records individual versions so a later integration of 15 is not lost.
MIGRATIONS: list[tuple[int, str]] = [
    (1, _V1_SCHEMA),
    (2, _V2_SCHEMA),
    (3, _V3_SCHEMA),
    (4, _V4_SCHEMA),
    (5, _V5_SCHEMA),
    (6, _V6_SCHEMA),
    (7, _V7_SCHEMA),
    (8, _V8_SCHEMA),
    (9, _V9_SCHEMA),
    (10, _V10_SCHEMA),
    (11, _V11_SCHEMA),
    (12, _V12_SCHEMA),
    (13, _V13_SCHEMA),
    (14, _V14_SCHEMA),
    (16, _V16_SCHEMA),
    (17, _V17_SCHEMA),
    (18, _V18_SCHEMA),
]


def get_connection() -> sqlite3.Connection:
    """One connection per call — sqlite3 connections aren't safe to share
    across threads, and FastAPI can run request handlers on different
    threads, so callers open, use, and close rather than holding one open."""
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    driver = (os.environ.get("SENTINELS_DB_DRIVER") or ("libsql" if url else "sqlite")).strip()
    if driver not in ("sqlite", "libsql"):
        raise RuntimeError("SENTINELS_DB_DRIVER must be sqlite or libsql.")
    if os.environ.get("RENDER") == "true" and (driver != "libsql" or not url):
        raise RuntimeError("Render requires a remote Turso database; ephemeral SQLite cannot store production sessions or scans.")
    if url and driver != "libsql":
        raise RuntimeError("A configured Turso URL cannot be ignored by the sqlite driver.")
    if driver == "libsql":
        from urllib.parse import urlsplit
        import libsql
        from libsql_adapter import Connection
        if url:
            parsed = urlsplit(url)
            if parsed.scheme not in ("libsql", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise RuntimeError("TURSO_DATABASE_URL must be a libsql:// or https:// database origin without embedded credentials.")
            if not token:
                raise RuntimeError("TURSO_AUTH_TOKEN is required for the remote database.")
            # Connect to the primary directly: no local replica, sync delay or
            # ephemeral disk can weaken quotas, ownership or session revocation.
            conn = Connection(libsql.connect(url, auth_token=token))
        else:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = Connection(libsql.connect(str(DB_PATH)))
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise RuntimeError("The database must enforce foreign keys.")
    return conn


def init_db() -> None:
    """Bring the schema up to the latest version. Safe to call on every
    process startup — a fresh DB gets every migration, an existing one only
    gets the ones it's missing, and a fully up-to-date one does nothing."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row["version"] if row is not None else 0
        ledger_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone() is not None
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
        if not ledger_exists:
            for version in range(1, current + 1):
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}

        for version, sql in sorted(MIGRATIONS):
            if version in applied:
                continue
            # executescript implicitly commits in sqlite3. Execute complete
            # statements instead, so schema and ledger advance atomically.
            statement = ""
            for line in sql.splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ""
            if statement.strip():
                raise ValueError(f"Incomplete SQL in migration {version}")
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            current = max(current, version)
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (current,))
                row = True  # sentinel: subsequent iterations should UPDATE, not INSERT
            else:
                conn.execute("UPDATE schema_version SET version = ?", (current,))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
