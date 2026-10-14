"""How Sentinels talks to GitHub's API as an anonymous reader of public repositories.

Two things that every such call needs, kept in one place so the repo scan
(`repo/fetch.py`) and the fix preview (`remediation/source.py`) agree:

* `public_api_headers()`: the optional `SENTINELS_GITHUB_API_TOKEN`.
* `check_response()`: a GitHub failure turned into `GitHubUnavailable`, with a
  message a person can act on, instead of an `httpx.HTTPStatusError` that nothing
  catches and that surfaces as a bare 500.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx


class GitHubUnavailable(Exception):
    """GitHub refused or failed a read. `message` is for the person; `status` is
    the HTTP status Sentinels should answer with (503 for a limit or outage)."""

    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def public_api_headers() -> dict[str, str]:
    """Credentials for GitHub's API, only when `SENTINELS_GITHUB_API_TOKEN` is set.

    Without one, every request counts against GitHub's limit for anonymous
    callers: 60 an hour *per IP address*. On a shared host such as Render's free
    plan that address is shared with other customers, so the allowance is often
    gone before the first request. A token raises it to 5,000 an hour for that
    token alone; a fine-grained token with no permissions reads public
    repositories and nothing else.

    Sent per request, never set on a client: the same clients also talk to other
    hosts (OSV, Docker Hub), which must not receive it. httpx drops an
    `Authorization` header on a redirect to another origin.
    """
    token = os.environ.get("SENTINELS_GITHUB_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def check_response(response: httpx.Response, *, what: str = "this repository") -> None:
    """Raise `GitHubUnavailable` for a failed response; return for a good one.

    A 404 is the caller's to interpret (a missing file is often "nothing to
    fix"), so it is left alone here; call this after the caller's own 404 check.
    """
    if response.is_success:
        return
    limited = response.status_code == 429 or (
        response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
    )
    if limited:
        when = ""
        reset = response.headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            when = f" It resets at {datetime.fromtimestamp(int(reset), tz=timezone.utc):%H:%M UTC}."
        raise GitHubUnavailable(f"GitHub is limiting how often Sentinels can read {what} right now.{when} Try again then.")
    if response.status_code in (401, 403):
        raise GitHubUnavailable(f"GitHub refused access to {what} (HTTP {response.status_code}).", 502)
    raise GitHubUnavailable(f"GitHub returned an error (HTTP {response.status_code}) reading {what}.", 502)
