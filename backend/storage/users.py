"""Read/write path for the `users` and `sessions` tables.

Same convention as the rest of `storage/`: functions that take a `conn` are
meant to be composed inside someone else's transaction; the ones that open
their own connection are entry points called straight from a route.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from auth.session import is_expired
from db import get_connection
from models import User


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        github_id=row["github_id"],
        github_login=row["github_login"],
        avatar_url=row["avatar_url"],
    )


def upsert_user(
    conn: sqlite3.Connection,
    github_id: int,
    github_login: str,
    avatar_url: str | None,
) -> User:
    """Create the user on first sign-in, or refresh them on every one after.

    Matching on `github_id` rather than `github_login` is deliberate: GitHub
    handles can be changed and re-registered by someone else, while the numeric
    id never moves. Keying on the handle would eventually hand one person's
    scans to whoever claimed their old name.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO users (github_id, github_login, avatar_url, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(github_id) DO UPDATE SET
            github_login = excluded.github_login,
            avatar_url   = excluded.avatar_url,
            last_seen_at = excluded.last_seen_at
        """,
        (github_id, github_login, avatar_url, now, now),
    )
    row = conn.execute(
        "SELECT * FROM users WHERE github_id = ?", (github_id,)
    ).fetchone()
    return _row_to_user(row)


def create_session(
    conn: sqlite3.Connection, user_id: int, token_hash: str, expires_at: str
) -> None:
    """Record a newly minted session so it can later be looked up and revoked."""
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_hash, user_id, datetime.now(timezone.utc).isoformat(), expires_at),
    )


def user_for_token_hash(token_hash: str) -> User | None:
    """The signed-in user for a session token, or None if there isn't one.

    Returns None for every failure — unknown token, deleted user, expired
    session — because the caller's response is identical in all three cases
    (401) and distinguishing them in the API would tell an attacker which of
    their guesses was closest.

    An expired row is deleted on the way past. There is no scheduled cleanup
    job in this project, so the sessions table is kept from growing forever by
    the requests that stumble over stale rows anyway.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT u.*, s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        if is_expired(row["expires_at"]):
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            conn.commit()
            return None
        return _row_to_user(row)
    finally:
        conn.close()


def sign_in(github_id: int, github_login: str, avatar_url: str | None,
            token_hash: str, expires_at: str) -> User:
    """Upsert the user and open their session in one transaction.

    One connection and one commit, so a crash between the two can't leave a
    session pointing at a user row that was never written.
    """
    conn = get_connection()
    try:
        user = upsert_user(conn, github_id, github_login, avatar_url)
        create_session(conn, user.id, token_hash, expires_at)
        conn.commit()
        return user
    finally:
        conn.close()


def delete_session(token_hash: str) -> bool:
    """Revoke one session. Returns False if it was already gone."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def purge_expired_sessions() -> int:
    """Delete every session past its expiry. Returns how many went."""
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def is_blocked(user_id: int) -> bool:
    """Whether this account has been stopped. False for an unknown id."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT blocked FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row["blocked"]) if row is not None else False
    finally:
        conn.close()


def is_github_id_blocked(github_id: int) -> bool:
    """Same check before a session exists, keyed on the GitHub id sign-in has."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT blocked FROM users WHERE github_id = ?", (github_id,)
        ).fetchone()
        return bool(row["blocked"]) if row is not None else False
    finally:
        conn.close()


def set_blocked(github_login: str, blocked: bool) -> bool:
    """Stop (or release) the account with this GitHub login. False if no such
    user. Existing sessions stop working on their next request."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE users SET blocked = ? WHERE LOWER(github_login) = LOWER(?)",
            (1 if blocked else 0, github_login),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
