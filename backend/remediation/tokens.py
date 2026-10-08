"""How Sentinels gets permission to write to a repository, for one hour at a
time (PLAN-v5 Stage B).

Sign-in (`auth/github_oauth.py`) answers "who is this person". It deliberately
throws its access token away, because knowing who you are does not require
standing permission to act as you. This module answers the *other* question:
"may this program push a branch to that repository right now" -- and it never
holds a long-lived credential to do it either.

The chain, and why each link exists:

  1. The App has an RSA **private key**, a file that lives outside the repo and
     is never sent anywhere. Not a password -- a signing key.
  2. Sentinels signs a short **JWT** with it. A JWT is just three
     base64 chunks joined by dots -- header, claims, signature -- and the
     signature is what makes it unforgeable. Claims here are only `iss` (which
     App), `iat`, and `exp`. Nine minutes, because GitHub rejects anything over
     ten and clocks drift.
  3. That JWT proves "I am the Sentinels App" and nothing more. It cannot read
     or write a single file. It can only be traded, at
     `POST /app/installations/{id}/access_tokens`, for...
  4. ...an **installation token**: a real credential, scoped to exactly the
     repositories that one installation covers, with exactly the permissions
     the App declared, expiring in about an hour.

The whole point of the shape is step 3. The credential that lasts forever
(the private key) cannot touch a repository, and the credential that can touch
a repository does not last.

A tiny standalone version of the same idea, no GitHub involved:

    def day_pass(master_key, room):
        return f"{room}-pass-valid-1h"      # derived, narrow, expiring

    # the master key opens the safe that prints passes -- never a door

`DevTokenProvider` exists because doing steps 1-4 for the first time is a
multi-step setup, and it should be possible to test the *Git* half before the
*auth* half is registered. It refuses to run without an explicit opt-in
environment variable, so it can never be what's running by accident.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt

GITHUB_API = "https://api.github.com"

# GitHub rejects an App JWT whose lifetime exceeds 10 minutes. Nine leaves a
# minute of headroom for a clock that runs slightly fast -- an `exp` in the
# future by our reckoning and in the past by GitHub's is a 401 that looks
# like a broken key.
_JWT_TTL_SECONDS = 9 * 60

# Backdating `iat` covers the opposite drift: a clock that runs slow makes a
# freshly minted token look like it was issued in the future, which GitHub
# also rejects. Sixty seconds is GitHub's own documented suggestion.
_JWT_BACKDATE_SECONDS = 60


class TokenError(RuntimeError):
    """Raised when no usable installation token can be produced. Always
    terminal for the request that asked -- there is no partial write to fall
    back to."""


class InstallationGone(TokenError):
    """GitHub says this App has no such installation: it was uninstalled
    there. Distinct from other token failures because the right response is to
    stop using the grant, not to retry."""


def get_app_id() -> str | None:
    return os.environ.get("GITHUB_APP_ID") or None


def get_private_key() -> str | None:
    """The App's RSA private key, read from the path in the environment.

    A *path*, never the key itself: an environment variable holding a
    multi-line PEM is awkward to set correctly and ends up pasted into shell
    history, CI logs, and screenshots. The file stays outside the repository
    (see PLAN-v5.md's "What only the developer can do").
    """
    path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def app_configured() -> bool:
    return get_app_id() is not None and get_private_key() is not None


def app_jwt(now: int | None = None) -> str:
    """Sign a short-lived assertion that this process is the Sentinels App.

    `now` is injectable purely so a test can assert on exact claim values
    without racing the clock.
    """
    app_id = get_app_id()
    private_key = get_private_key()
    if app_id is None or private_key is None:
        raise TokenError(
            "GitHub App is not configured. Set GITHUB_APP_ID and "
            "GITHUB_APP_PRIVATE_KEY_PATH in backend/.env."
        )
    issued = int(now if now is not None else time.time())
    payload = {
        "iat": issued - _JWT_BACKDATE_SECONDS,
        "exp": issued + _JWT_TTL_SECONDS,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


async def fetch_installation(client: httpx.AsyncClient, installation_id: int) -> dict | None:
    """Read one installation's metadata (which account, which repositories)
    as the App itself.

    Used by the install callback: GitHub's redirect carries only an
    `installation_id`, and a row saying nothing but a number would be useless
    for the `account_login` lookup every write depends on. `None` if GitHub
    doesn't recognize the id as belonging to this App.
    """
    try:
        response = await client.get(
            f"{GITHUB_API}/app/installations/{installation_id}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt()}",
            },
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


@dataclass
class InstallationToken:
    """One minted token and when it stops working."""

    token: str
    expires_at: str      # ISO 8601 UTC, as GitHub reports it


class TokenProvider(ABC):
    """What `remediation/apply.py` depends on instead of depending on
    GitHub's auth model directly -- so a test can hand it a provider that
    returns a fixed string and never think about JWTs at all."""

    @abstractmethod
    async def token_for(self, client: httpx.AsyncClient, installation_id: int) -> InstallationToken:
        """A token authorized to write to this installation's repositories.
        Raises `TokenError` if one cannot be produced."""
        raise NotImplementedError


class AppTokenProvider(TokenProvider):
    """The real one: JWT in, installation token out."""

    async def token_for(self, client: httpx.AsyncClient, installation_id: int) -> InstallationToken:
        try:
            assertion = app_jwt()
        except TokenError:
            raise
        try:
            response = await client.post(
                f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {assertion}",
                },
            )
        except httpx.HTTPError as exc:
            raise TokenError(f"Could not reach GitHub to mint a token: {exc}") from exc

        # 401 means the JWT was rejected (wrong key, wrong app id, clock skew);
        # 404 means this App has no such installation -- most often because the
        # user removed it on github.com since the row was written. Neither is
        # something to retry blindly, so both stop here with a specific message.
        if response.status_code == 401:
            raise TokenError(
                "GitHub rejected the App's signed assertion. Check GITHUB_APP_ID and "
                "that GITHUB_APP_PRIVATE_KEY_PATH points at this App's private key."
            )
        if response.status_code == 404:
            raise InstallationGone(
                f"Installation {installation_id} no longer exists for this App -- "
                "it was probably uninstalled on GitHub."
            )
        if response.status_code != 201:
            raise TokenError(
                f"GitHub refused to mint an installation token (HTTP {response.status_code})."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise TokenError("GitHub's token response was not JSON.") from exc

        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise TokenError("GitHub's token response contained no token.")
        expires = payload.get("expires_at")
        return InstallationToken(
            token=token,
            expires_at=expires if isinstance(expires, str) else "",
        )


class DevTokenProvider(TokenProvider):
    """A personal access token from the environment, for developing the Git
    half before the App is registered.

    Two separate variables on purpose. `SENTINELS_GITHUB_DEV_TOKEN` holds the
    value; `SENTINELS_ALLOW_DEV_TOKEN=1` is a second, deliberate switch that
    has to be flipped as well. A single variable would mean a token left in a
    `.env` from a debugging session quietly stays live -- this way, forgetting
    to remove the token is not enough to keep using it.
    """

    async def token_for(self, client: httpx.AsyncClient, installation_id: int) -> InstallationToken:
        if os.environ.get("SENTINELS_ALLOW_DEV_TOKEN") != "1":
            raise TokenError(
                "The developer token provider is disabled. Set SENTINELS_ALLOW_DEV_TOKEN=1 "
                "to use it -- and only on a throwaway repository."
            )
        token = os.environ.get("SENTINELS_GITHUB_DEV_TOKEN") or ""
        if not token:
            raise TokenError("SENTINELS_GITHUB_DEV_TOKEN is not set.")
        return InstallationToken(token=token, expires_at="")


def default_provider() -> TokenProvider:
    """Which provider a request gets when nothing overrides it.

    The dev path is only chosen when *both* of its switches are on, so the
    real App is the default the moment it's configured -- and an unconfigured
    server fails with `AppTokenProvider`'s clear "not configured" message
    rather than silently doing nothing.
    """
    if os.environ.get("SENTINELS_ALLOW_DEV_TOKEN") == "1" and os.environ.get(
        "SENTINELS_GITHUB_DEV_TOKEN"
    ):
        return DevTokenProvider()
    return AppTokenProvider()
