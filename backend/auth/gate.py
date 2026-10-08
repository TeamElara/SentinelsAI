"""Who may use this deployment at all (Launch Plan A5).

Two independent checks, both applied to every signed-in request and again at
sign-in so a refused person never gets a session:

* **Invite list** — `SENTINELS_ALLOWED_GITHUB_IDS`, a comma-separated list of
  numeric GitHub ids. Ids, not logins: a login can be renamed and re-registered
  by someone else. Unset means anyone with a GitHub account may sign in, which
  is what local development wants; set it for the beta.
* **Blocked accounts** — `users.blocked`, set with `storage.users.set_blocked`,
  which stops one person without touching their data.
"""
from __future__ import annotations

import os

from fastapi import HTTPException

from models import User
from storage.users import is_blocked, is_github_id_blocked

NOT_INVITED = "Sentinels is invite-only during the beta. Ask the team for an invite."
SUSPENDED = "This account has been suspended."


def allowed_github_ids() -> set[int] | None:
    """The invite list, or None when there isn't one (everyone may sign in).

    A malformed entry is skipped rather than ignored wholesale: a typo must
    not silently turn an invite list into an open door.
    """
    raw = os.environ.get("SENTINELS_ALLOWED_GITHUB_IDS")
    if raw is None or not raw.strip():
        return None
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def sign_in_refusal(github_id: int) -> str | None:
    """Why this GitHub account may not sign in, as a login-page error code, or None."""
    allowed = allowed_github_ids()
    if allowed is not None and github_id not in allowed:
        return "not_invited"
    if is_github_id_blocked(github_id):
        return "suspended"
    return None


def ensure_allowed(user: User) -> None:
    """Refuse a request from someone who is no longer (or was never) allowed in."""
    allowed = allowed_github_ids()
    if allowed is not None and user.github_id not in allowed:
        raise HTTPException(status_code=403, detail=NOT_INVITED)
    if is_blocked(user.id):
        raise HTTPException(status_code=403, detail=SUSPENDED)
