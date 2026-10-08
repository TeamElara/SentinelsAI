"""The dependency every protected route hangs off.

FastAPI's `Depends` runs a function before the route body and hands the result
in as an argument. Written once here, `Depends(current_user)` on a route means
"this endpoint does not exist for anyone who isn't signed in" — the route body
never runs, so it cannot forget to check.

A tiny standalone shape of the same idea, no FastAPI involved:

    def needs_ticket(check):
        def wrapper():
            holder = check()          # raises if there's no valid ticket
            return f"welcome, {holder}"
        return wrapper

The check happens outside the thing being protected, so the protected thing
has no way to skip it.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from auth.session import COOKIE_NAME, get_session_secret, hash_token, token_from_cookie
from auth.gate import ensure_allowed
from models import User
from storage.users import user_for_token_hash

_UNCONFIGURED_DETAIL = (
    "Authentication is not configured on this server. Set SENTINELS_SESSION_SECRET "
    "in backend/.env (any long random string) and restart."
)


def _resolve(request: Request) -> User | None:
    """Cookie → verified token → session row → user. None at any failure.

    Deliberately silent about *which* step failed. A response that
    distinguished "no cookie" from "bad signature" from "expired" would be a
    free oracle for anyone probing the endpoint.
    """
    secret = get_session_secret()
    if secret is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)

    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None

    token = token_from_cookie(raw, secret)
    if token is None:
        return None

    return user_for_token_hash(hash_token(token))


def current_user(request: Request) -> User:
    """Require a signed-in user, or fail the request with 401."""
    user = _resolve(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    ensure_allowed(user)
    return user


def optional_user(request: Request) -> User | None:
    """Identify the caller if they're signed in, without requiring it.

    For routes that are legitimately public but behave differently when they
    know who is asking. Kept separate from `current_user` rather than adding a
    flag to it, so a route's signature makes its access rule obvious at a
    glance instead of hiding it in an argument.
    """
    try:
        return _resolve(request)
    except HTTPException:
        # An unconfigured server is not a reason to break a public route.
        return None
