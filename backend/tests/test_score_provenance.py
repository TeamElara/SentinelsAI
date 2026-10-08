import pytest
import db
from models import ScanReport
from scoring import SCORER_VERSION
from storage.scans import save_scan, get_scan, list_scans
from tests.test_db_migrations import _build_at_version


def test_v14_upgrade_preserves_historical_score(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "old.db")
    _build_at_version(db.DB_PATH, 14)
    conn = db.get_connection()
    conn.execute("INSERT INTO scans(id,url,scanned_at,duration_ms,score,grade,created_at) VALUES ('old','https://example.com','old',1,80,'B','old')")
    conn.commit()
    conn.close()
    db.init_db()
    report = get_scan("old")
    assert (report.score, report.grade, report.scorer_version) == (80, "B", "legacy-v1")
    assert list_scans()[0].scorer_version == "legacy-v1"


def test_new_score_version_round_trips(temp_db):
    save_scan(ScanReport(id="new", url="https://example.com", scanned_at="now", duration_ms=1,
                         score=65, grade="D", scorer_version=SCORER_VERSION))
    assert get_scan("new").scorer_version == SCORER_VERSION
    assert list_scans()[0].scorer_version == SCORER_VERSION


def test_migration_15_can_land_after_16_to_18(tmp_path, monkeypatch):
    """Track A's migration 15 (users.blocked) was reserved while Track C's 16-18
    were built, so a database may already be at 18 without it. The ledger must
    still apply it, once."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "late.db")      # a fresh file, not temp_db's
    everything = list(db.MIGRATIONS)
    monkeypatch.setattr(db, "MIGRATIONS", [m for m in everything if m[0] != 15])
    db.init_db()                                  # a database at 18 that never saw 15
    conn = db.get_connection()
    assert "blocked" not in [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert conn.execute("SELECT version FROM schema_migrations WHERE version=15").fetchone() is None
    conn.close()

    monkeypatch.setattr(db, "MIGRATIONS", everything)
    db.init_db()
    db.init_db()                                  # idempotent
    conn = db.get_connection()
    assert conn.execute("SELECT blocked FROM users").fetchall() == []
    assert conn.execute("SELECT version FROM schema_migrations WHERE version=15").fetchone() is not None
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 18
    conn.close()


def test_failed_migration_rolls_back_schema_and_ledger(temp_db, monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, (99,
        "ALTER TABLE users ADD COLUMN rolled_back INTEGER;\nINVALID SQL;\n")])
    with pytest.raises(Exception):
        db.init_db()
    conn = db.get_connection()
    assert "rolled_back" not in [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    assert conn.execute("SELECT version FROM schema_migrations WHERE version=99").fetchone() is None
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 18
    conn.close()
