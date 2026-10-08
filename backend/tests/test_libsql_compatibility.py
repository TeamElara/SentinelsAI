import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
import db
from database_backup import export_backup, restore_backup
from storage.users import sign_in, user_for_token_hash
from storage.scans import save_scan, get_scan
from models import ScanReport


@pytest.mark.parametrize("driver", ["sqlite", "libsql"])
def test_backup_restore_retains_sessions_scans_and_partial_indexes(driver, monkeypatch, tmp_path):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("SENTINELS_DB_DRIVER", driver)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "source.db")
    db.init_db()
    user = sign_in(123, "fixture", None, "fixture-token-hash", "2099-01-01T00:00:00+00:00")
    save_scan(ScanReport(id="backup-scan", url="https://example.com", scanned_at="now", duration_ms=1, score=65, grade="D"), user_id=user.id)
    backup = export_backup(tmp_path / "snapshot.db")
    with pytest.raises(FileExistsError):
        export_backup(backup)
    # Opening fresh connections simulates a new process, not a Render redeploy.
    assert user_for_token_hash("fixture-token-hash").id == user.id
    assert get_scan("backup-scan").score == 65
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "restored.db")
    restore_backup(backup)
    db.init_db()
    assert user_for_token_hash("fixture-token-hash").id == user.id
    assert get_scan("backup-scan").score == 65
    conn = db.get_connection()
    assert conn.execute("SELECT sql FROM sqlite_master WHERE name='idx_fix_applications_active'").fetchone()["sql"].count("WHERE") == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
    with pytest.raises(ValueError, match="empty"):
        restore_backup(backup)


def test_libsql_named_rows_transactions_and_script_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("SENTINELS_DB_DRIVER", "libsql")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "contract.db")
    conn = db.get_connection()
    conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT UNIQUE); INSERT INTO t VALUES (1, 'a;b');")
    conn.commit()
    row = conn.execute("SELECT value AS Name FROM t").fetchone()
    assert row["name"] == row[0] == "a;b"
    assert dict(row) == {"Name": "a;b"}
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO t VALUES (2,'second')")
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) AS n FROM t").fetchone()["n"] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO t VALUES (3,'a;b')")
    with pytest.raises(sqlite3.OperationalError):
        conn.executescript("INVALID SQL;")
    conn.close()


def test_render_cannot_silently_use_ephemeral_sqlite(monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SENTINELS_DB_DRIVER", "sqlite")
    with pytest.raises(RuntimeError, match="remote Turso"):
        db.get_connection()


def test_configured_turso_requires_credentials(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://fixture.turso.io")
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("SENTINELS_DB_DRIVER", raising=False)
    with pytest.raises(RuntimeError, match="TURSO_AUTH_TOKEN"):
        db.get_connection()


def test_libsql_contended_writer_waits_without_starving_lock_owner(monkeypatch, tmp_path):
    monkeypatch.setenv('SENTINELS_DB_DRIVER', 'libsql')
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'writers.db')
    first = db.get_connection()
    first.execute('CREATE TABLE t(value INTEGER)')
    first.commit()
    first.execute('BEGIN IMMEDIATE')
    first.execute('INSERT INTO t VALUES (1)')
    started = Event()
    def second_writer():
        second = db.get_connection()
        try:
            started.set()
            second.execute('BEGIN IMMEDIATE')
            second.execute('INSERT INTO t VALUES (2)')
            second.commit()
        finally:
            second.close()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(second_writer)
        assert started.wait(1)
        time.sleep(.03)
        first.commit()
        future.result(timeout=2)
    assert first.execute('SELECT SUM(value) FROM t').fetchone()[0] == 3
    first.close()


def test_libsql_never_retries_ambiguous_network_failure(monkeypatch):
    from libsql_adapter import _call
    calls = []
    def fails():
        calls.append(True)
        raise ValueError('remote HTTP operation failed')
    with pytest.raises(sqlite3.OperationalError, match='verify database connectivity'):
        _call(fails)
    assert len(calls) == 1


def test_libsql_contention_retry_has_a_deadline(monkeypatch):
    import libsql_adapter
    monkeypatch.setattr(libsql_adapter, '_BUSY_RETRY_SECONDS', 0)
    def locked():
        raise ValueError('database is locked')
    with pytest.raises(sqlite3.OperationalError, match='database is locked'):
        libsql_adapter._call(locked)


def test_libsql_does_not_replay_a_partially_executed_batch():
    from libsql_adapter import Connection
    written = []
    class Raw:
        def executemany(self, sql, rows):
            written.append(rows[0])
            raise ValueError('database is locked')
    with pytest.raises(sqlite3.OperationalError):
        Connection(Raw()).executemany('INSERT INTO t VALUES (?)', [(1,), (2,)])
    assert written == [(1,)]
