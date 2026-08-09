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

# Each entry is (version, schema sql to apply to go from version-1 to version).
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
]


def get_connection() -> sqlite3.Connection:
    """One connection per call — sqlite3 connections aren't safe to share
    across threads, and FastAPI can run request handlers on different
    threads, so callers open, use, and close rather than holding one open."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Bring the schema up to the latest version. Safe to call on every
    process startup — a fresh DB gets every migration, an existing one only
    gets the ones it's missing, and a fully up-to-date one does nothing."""
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row["version"] if row is not None else 0

        for version, sql in MIGRATIONS:
            if version <= current:
                continue
            conn.executescript(sql)
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
                row = True  # sentinel: subsequent iterations should UPDATE, not INSERT
            else:
                conn.execute("UPDATE schema_version SET version = ?", (version,))
            current = version

        conn.commit()
    finally:
        conn.close()
