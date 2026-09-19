"""Signing in with GitHub — the user-authorization half of the GitHub App.

The same App that will later be installed on a repository to open pull
requests (PLAN-v5 Stage B) also acts as an OAuth provider here. That is why
Sentinels has no password anywhere: GitHub already knows who this person is,
and asking them to invent a second password for a local security tool would
add a credential to steal without adding any safety.

The dance, in order:

  1. We send the browser to GitHub with our client id and a random `state`.
  2. The user approves (or doesn't) on github.com. We never see that page.
  3. GitHub sends the browser back to our callback with a short-lived `code`
     and the same `state` we sent.
  4. We swap that `code` for an access token, server-to-server, using our
     client *secret* — a request the browser is never part of.
  5. We use the token once to read the user's identity, then throw it away.

Step 5 is a deliberate choice. Sentinels needs to know *who you are*, which
this token answers permanently; it does not need standing permission to act
as you. Storing the token would mean holding a credential that can read your
repositories, for no feature that exists.

Nothing in this module ever logs a code, a token, or the client secret.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_API_URL = "https://api.github.com/user"

# The GitHub App's user-authorization flow needs no scopes to read a public
# profile, and asking for none is the point: the sign-in consent screen should
# say "read your public profile" and nothing more alarming.
SCOPES = ""


def get_client_id() -> str | None:
    return os.environ.get("GITHUB_APP_CLIENT_ID") or None


def get_client_secret() -> str | None:
    return os.environ.get("GITHUB_APP_CLIENT_SECRET") or None


def get_frontend_origin() -> str:
    """Where to send the browser once sign-in succeeds.

    Defaults to the Next.js dev server, matching `API_BASE`'s fallback on the
    frontend side — so a fresh clone works with no configuration at all.
    """
    return os.environ.get("SENTINELS_FRONTEND_ORIGIN") or "http://localhost:3000"


def oauth_configured() -> bool:
    """True when both halves of the App's OAuth credentials are present."""
    return get_client_id() is not None and get_client_secret() is not None


def missing_settings() -> list[str]:
    """Which environment variables still need setting, for a useful error.

    Returned to the frontend on the login page. Names of variables are not
    secrets; their values are, and none of those are ever included here.
    """
    missing = []
    if get_client_id() is None:
        missing.append("GITHUB_APP_CLIENT_ID")
    if get_client_secret() is None:
        missing.append("GITHUB_APP_CLIENT_SECRET")
    return missing


def get_app_slug() -> str | None:
    """The App's URL name — the `<slug>` in github.com/apps/<slug>.

    Not the same string as the App's display name and not derivable from it
    (GitHub lowercases and hyphenates, but also disambiguates collisions), so
    it is configured rather than guessed.
    """
    return os.environ.get("GITHUB_APP_SLUG") or None


def install_url(state: str) -> str:
    """Where to send a user who wants to grant Sentinels write access.

    A *different* flow from `authorize_url` above, and the distinction is the
    whole point: signing in proves who someone is, installing grants
    permission to write to their repositories. Someone can sign in and never
    install — the app has to work for them, just without autofix.

    `state` is round-tripped exactly as in sign-in, and checked on the way
    back for exactly the same reason: it ties GitHub's redirect to the browser
    session that actually started the flow.
    """
    return f"https://github.com/apps/{get_app_slug() or ''}/installations/new?state={state}"


@dataclass
class GitHubIdentity:
    """The only three things Sentinels keeps from a sign-in."""

    github_id: int
    login: str
    avatar_url: str | None


def authorize_url(state: str, redirect_uri: str) -> str:
    """The github.com URL to send the browser to, with CSRF state attached.

    `state` is echoed back to us unchanged on the callback. Comparing it to
    what we sent is what stops an attacker from feeding *their* authorization
    code into *your* browser and quietly logging you into their account.
    """
    params = httpx.QueryParams(
        {
            "client_id": get_client_id() or "",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    if SCOPES:
        params = params.set("scope", SCOPES)
    return f"{AUTHORIZE_URL}?{params}"


async def exchange_code(code: str, redirect_uri: str) -> str | None:
    """Trade the callback's `code` for an access token. None on any failure.

    `Accept: application/json` matters — without it GitHub answers this
    endpoint in `application/x-www-form-urlencoded`, and `response.json()`
    would raise on a body that is not actually broken.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                ACCESS_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": get_client_id() or "",
                    "client_secret": get_client_secret() or "",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
        except httpx.HTTPError:
            return None

    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None

    # GitHub reports a bad or reused code as a 200 with an `error` key, not as
    # an HTTP error status — so the status check above is not enough on its own.
    token = payload.get("access_token")
    return token if isinstance(token, str) and token else None


async def fetch_identity(access_token: str) -> GitHubIdentity | None:
    """Read who this token belongs to. None if GitHub won't say."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(
                USER_API_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                },
            )
        except httpx.HTTPError:
            return None

    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None

    github_id = payload.get("id")
    login = payload.get("login")
    if not isinstance(github_id, int) or not isinstance(login, str):
        return None
    avatar = payload.get("avatar_url")
    return GitHubIdentity(
        github_id=github_id,
        login=login,
        avatar_url=avatar if isinstance(avatar, str) else None,
    )
