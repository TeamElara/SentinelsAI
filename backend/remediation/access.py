"""Whether the person asking may write to this repository (Launch Plan A3).

An installation is a grant to an *account*. The App's installation token can
write to every repository that installation covers, so having an installation
linked says nothing about whether this particular person is allowed to push to
this particular repository: someone with read-only access to one repo in an
organization must not be able to use the App's wider write access through it.

So before any pull request is opened, Sentinels asks GitHub, with the
installation token, what role this user holds on this exact repository, and
accepts only roles that can push. Anything it can't confirm counts as no.
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

GITHUB_API = "https://api.github.com"

# `role_name` values that can push to a branch. `triage` and `read` cannot.
WRITE_ROLES = {"admin", "maintain", "write"}


class RepoAccessError(Exception):
    """The user's access to the repository couldn't be confirmed as write.
    `status` is the HTTP status the caller should answer with."""

    def __init__(self, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


async def require_write_access(
    client: httpx.AsyncClient, token: str, owner: str, repo: str, login: str
) -> str:
    """Return the user's role on `owner/repo`, or raise `RepoAccessError`
    unless it is one that can push."""
    try:
        response = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/collaborators/{quote(login, safe='')}/permission",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            },
        )
    except httpx.HTTPError as exc:
        raise RepoAccessError(
            f"Could not check your access to {owner}/{repo} on GitHub. Try again.", status=502
        ) from exc

    denied = RepoAccessError(
        f"Sentinels could not confirm that @{login} has write access to {owner}/{repo}. "
        "Only people who can push to a repository can open fix pull requests for it."
    )
    if response.status_code != 200:
        raise denied
    try:
        payload = response.json()
    except ValueError as exc:
        raise denied from exc

    # `role_name` is the precise role; `permission` is the older four-value
    # summary (admin, write, read, none) that can't tell triage from read.
    role = payload.get("role_name") or payload.get("permission")
    if role not in WRITE_ROLES:
        raise denied
    return str(role)
