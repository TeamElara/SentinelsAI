"""Read/write path for `domain_claims`: the hosts a person is proving, or has
proven, they control (see `domains.py`)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db import get_connection
from domains import (
    CHECK_COOLDOWN_SECONDS,
    MAX_CLAIMS_PER_USER,
    covers,
    dns_name,
    dns_value,
    file_url,
    new_token,
)
from models import DomainClaim


class TooManyClaims(Exception):
    pass


class CheckedTooRecently(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claim(row) -> DomainClaim:
    return DomainClaim(
        host=row["host"],
        created_at=row["created_at"],
        verified_at=row["verified_at"],
        method=row["method"],
        dns_name=dns_name(row["host"]),
        dns_value=dns_value(row["token"]),
        file_url=file_url(row["host"]),
        file_contents=row["token"],
    )


def list_claims(user_id: int) -> list[DomainClaim]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM domain_claims WHERE user_id = ? ORDER BY host", (user_id,)
        ).fetchall()
        return [_claim(row) for row in rows]
    finally:
        conn.close()


def get_claim(user_id: int, host: str) -> DomainClaim | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM domain_claims WHERE user_id = ? AND host = ?", (user_id, host)
        ).fetchone()
        return _claim(row) if row is not None else None
    finally:
        conn.close()


def add_claim(user_id: int, host: str) -> DomainClaim:
    """Start proving `host`, or return the claim already started, token and all,
    so adding the same host twice doesn't invalidate a record already published."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM domain_claims WHERE user_id = ? AND host = ?", (user_id, host)
        ).fetchone()
        if row is not None:
            return _claim(row)
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM domain_claims WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
        if total >= MAX_CLAIMS_PER_USER:
            raise TooManyClaims
        conn.execute(
            "INSERT INTO domain_claims (user_id, host, token, created_at) VALUES (?, ?, ?, ?)",
            (user_id, host, new_token(), _now().isoformat()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM domain_claims WHERE user_id = ? AND host = ?", (user_id, host)
        ).fetchone()
        return _claim(row)
    finally:
        conn.close()


def start_check(user_id: int, host: str) -> None:
    """Record that a check is starting, refusing one within the cooldown.

    The conditional UPDATE is the whole check-and-set, so two checks sent at
    once can't both pass it.
    """
    now = _now()
    cutoff = (now - timedelta(seconds=CHECK_COOLDOWN_SECONDS)).isoformat()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE domain_claims SET last_checked_at = ?
            WHERE user_id = ? AND host = ?
              AND (last_checked_at IS NULL OR last_checked_at < ?)
            """,
            (now.isoformat(), user_id, host, cutoff),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise CheckedTooRecently
    finally:
        conn.close()


def mark_verified(user_id: int, host: str, method: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE domain_claims SET verified_at = ?, method = ? WHERE user_id = ? AND host = ?",
            (_now().isoformat(), method, user_id, host),
        )
        conn.commit()
    finally:
        conn.close()


def delete_claim(user_id: int, host: str) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM domain_claims WHERE user_id = ? AND host = ?", (user_id, host)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def is_verified(user_id: int, host: str) -> bool:
    """Whether one of this person's verified claims covers `host`."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT host, method FROM domain_claims WHERE user_id = ? AND verified_at IS NOT NULL",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return any(covers(row["host"], row["method"], host) for row in rows)
