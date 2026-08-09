"""Read/write path for the `subdomains` table (PLAN-v4 §V6)."""
from __future__ import annotations

import sqlite3

from models import SubdomainEntry


def save_subdomains(conn: sqlite3.Connection, scan_id: str, entries: list[SubdomainEntry]) -> None:
    """Insert one row per discovered host. Takes an open connection so the
    caller (`storage.scans.save_scan`) can run this in the same transaction
    as the parent `scans` row."""
    for entry in entries:
        conn.execute(
            """
            INSERT INTO subdomains (
                scan_id, host, record_type, record_value, source,
                http_status, scheme, tls_valid, server, redirects_to, issue_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id,
                entry.host,
                entry.record_type,
                entry.record_value,
                entry.source,
                entry.http_status,
                entry.scheme,
                None if entry.tls_valid is None else int(entry.tls_valid),
                entry.server,
                entry.redirects_to,
                entry.issue_count,
            ),
        )


def load_subdomains(conn: sqlite3.Connection, scan_id: str) -> list[SubdomainEntry]:
    """Reconstruct the subdomain inventory for a scan from the DB."""
    rows = conn.execute(
        "SELECT * FROM subdomains WHERE scan_id = ? ORDER BY id", (scan_id,)
    ).fetchall()
    return [
        SubdomainEntry(
            host=row["host"],
            record_type=row["record_type"],
            record_value=row["record_value"],
            source=row["source"],
            http_status=row["http_status"],
            scheme=row["scheme"],
            tls_valid=None if row["tls_valid"] is None else bool(row["tls_valid"]),
            server=row["server"],
            redirects_to=row["redirects_to"],
            issue_count=row["issue_count"],
        )
        for row in rows
    ]
