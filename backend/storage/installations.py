"""Read/write path for `github_installations` -- the table that says which
user granted Sentinels write access to which GitHub account.

The table has existed since Stage 0's migration 10 but nothing read or wrote
it (PLAN-v5.md conflict #10). Stage B is what finally needs it: an
installation token can only be minted against an installation id, and
`remediation/apply.py` will not mint one unless the row backing it belongs to
the user making the request.

`revoked_at` is a *soft* revoke. Sentinels stopping is not the same as GitHub
stopping -- the real revocation happens on github.com, whenever the user
chooses. Writing a timestamp here means "we will no longer use this", which is
the only half of it Sentinels actually controls.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from db import get_connection
from models import GitHubInstallation


class InstallationOwnedByAnotherUser(Exception):
    """Another Sentinels user already holds a live grant on this installation."""


def _row_to_installation(row: sqlite3.Row) -> GitHubInstallation:
    return GitHubInstallation(
        id=row["id"],
        installation_id=row["installation_id"],
        account_login=row["account_login"],
        repo_selection=row["repo_selection"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


def save_installation(
    user_id: int,
    installation_id: int,
    account_login: str,
    repo_selection: str,
    permissions: dict | None = None,
) -> GitHubInstallation:
    """Record (or re-record) an installation for this user.

    `installation_id` is UNIQUE and GitHub only ever has one installation per
    account per App, so there is one row per id. Re-saving it for the *same*
    user updates the row and clears `revoked_at`: a user who revokes and then
    installs again has a live grant, and leaving the old timestamp in place
    would make the new install permanently unusable.

    A *different* user never takes over a live row: that raises
    `InstallationOwnedByAnotherUser` and changes nothing. Whoever holds a grant
    holds the App's write access to that account, so it can't move because
    someone else presented the same number. A row its owner has revoked is
    free to be claimed, since nobody is using it any more.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT user_id, revoked_at FROM github_installations WHERE installation_id = ?",
            (installation_id,),
        ).fetchone()
        if (
            existing is not None
            and existing["user_id"] != user_id
            and existing["revoked_at"] is None
        ):
            raise InstallationOwnedByAnotherUser(installation_id)
        conn.execute(
            """
            INSERT INTO github_installations
                (installation_id, user_id, account_login, repo_selection,
                 permissions_json, created_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(installation_id) DO UPDATE SET
                user_id          = excluded.user_id,
                account_login    = excluded.account_login,
                repo_selection   = excluded.repo_selection,
                permissions_json = excluded.permissions_json,
                revoked_at       = NULL
            """,
            (
                installation_id,
                user_id,
                account_login,
                repo_selection,
                json.dumps(permissions or {}),
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM github_installations WHERE installation_id = ?",
            (installation_id,),
        ).fetchone()
        return _row_to_installation(row)
    finally:
        conn.close()


def list_installations(user_id: int, include_revoked: bool = False) -> list[GitHubInstallation]:
    """Every installation this user has linked, newest first."""
    conn = get_connection()
    try:
        sql = "SELECT * FROM github_installations WHERE user_id = ?"
        if not include_revoked:
            sql += " AND revoked_at IS NULL"
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, (user_id,)).fetchall()
        return [_row_to_installation(row) for row in rows]
    finally:
        conn.close()


def active_installation_for(user_id: int, account_login: str) -> GitHubInstallation | None:
    """The live installation this user holds on `account_login`, or None.

    This is the lookup that gates every write in Stage B (PLAN-v5.md's
    invariant #4). Both halves matter: `user_id` because someone else's grant
    is not yours to use, and `revoked_at IS NULL` because a withdrawn grant
    must stop working the moment it is withdrawn, not at the next restart.

    GitHub account names are case-insensitive, so the comparison is too --
    otherwise a scan of `github.com/OctoCat/demo` would fail to find the
    installation linked as `octocat`.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM github_installations
            WHERE user_id = ?
              AND LOWER(account_login) = LOWER(?)
              AND revoked_at IS NULL
            ORDER BY id DESC
            """,
            (user_id, account_login),
        ).fetchone()
        return _row_to_installation(row) if row is not None else None
    finally:
        conn.close()


def revoke_installation(user_id: int, installation_id: int) -> bool:
    """Mark one of this user's installations as no longer usable by Sentinels.

    Scoped to `user_id` so the route above it cannot be tricked into revoking
    somebody else's grant by guessing an id. False means "you have no live
    installation with that id" -- unknown and already-revoked collapse into
    one answer because the caller does the same thing with both.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE github_installations
            SET revoked_at = ?
            WHERE installation_id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (datetime.now(timezone.utc).isoformat(), installation_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
