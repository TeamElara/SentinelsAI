"""Consistent logical SQLite/libSQL snapshots and empty-target restore.

Backups preserve all application tables, indexes, triggers, migration ledgers
and AUTOINCREMENT high-water marks. Values always use bound parameters.
"""
from pathlib import Path
import sqlite3

from db import get_connection


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _copy(source, target):
    schema = source.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 WHEN 'view' THEN 2 ELSE 3 END,name").fetchall()
    target.execute("PRAGMA foreign_keys = OFF")
    target.execute("BEGIN IMMEDIATE")
    try:
        for item in schema:
            if item["type"] == "table":
                target.execute(item["sql"])
                name = _quote(item["name"])
                rows = source.execute(f"SELECT * FROM {name}")
                columns = [_quote(c[0]) for c in rows.description]
                sql = f"INSERT INTO {name} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
                while batch := rows.fetchmany(500):
                    target.executemany(sql, [tuple(row) for row in batch])
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            target.execute("DELETE FROM sqlite_sequence")
            target.executemany("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)",
                               [tuple(row) for row in source.execute("SELECT name,seq FROM sqlite_sequence").fetchall()])
        for item in schema:
            if item["type"] != "table":
                target.execute(item["sql"])
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError("Backup contains foreign-key violations; restore aborted.")
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        target.execute("PRAGMA foreign_keys = ON")


def export_backup(destination: Path):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting an earlier recovery point.
    with destination.open("xb"):
        pass
    source = target = None
    try:
        source = get_connection()
        target = sqlite3.connect(destination)
        source.execute("BEGIN")
        _copy(source, target)
        source.rollback()
    except Exception:
        if target is not None:
            target.close()
        destination.unlink()
        raise
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()
    return destination


def restore_backup(source_path: Path):
    source = sqlite3.connect(f"{Path(source_path).resolve().as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = get_connection()
    try:
        existing = target.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        if existing:
            raise ValueError("Restore requires an empty database. Existing data will not be overwritten.")
        source.execute("BEGIN")
        _copy(source, target)
    finally:
        source.close()
        target.close()
