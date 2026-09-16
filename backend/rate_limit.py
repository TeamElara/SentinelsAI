"""A per-user request throttle for the endpoints that actually cost money —
each `/scan` or `/repo/scan` call makes real outbound HTTP requests and, if
GROQ_API_KEY is set, a real model call. Nothing upstream of this stops one
signed-in user from calling either in a tight loop.

Deliberately in-memory, not Redis or a database table: Sentinels runs as one
process (see `main.py`'s own docstring), so a dict keyed by user id is enough
— and it means this file has no new service to deploy, just like the rest of
the project's "plain Python over a vendor SDK" choices (PyJWT/cryptography
over a GitHub SDK, httpx over the anthropic SDK).

A tiny standalone version of the same idea, no FastAPI involved:

    from collections import defaultdict
    from time import monotonic

    calls = defaultdict(list)

    def allowed(key, limit=3, window=10):
        now = monotonic()
        calls[key] = [t for t in calls[key] if now - t < window]
        if len(calls[key]) >= limit:
            return False
        calls[key].append(now)
        return True

Call `allowed("alice")` more than `limit` times inside `window` seconds and it
starts returning `False` until the oldest call ages out.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from threading import Lock
from time import monotonic

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# How many scans one user may start within WINDOW_SECONDS. Generous enough
# that a real user manually re-scanning after fixing something never notices
# it, tight enough that a scripted loop can't run the outbound-request budget
# or the Groq bill up unattended.
SCAN_LIMIT = 10
WINDOW_SECONDS = 300.0


class SlidingWindowLimiter:
    """Tracks recent call timestamps per key, in memory, guarded by a lock.

    The lock matters because uvicorn's `--workers`/async model can run this
    from more than one request concurrently; without it two requests racing
    on the same user's list could both read it before either appends, letting
    both through when only one should have been.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._calls: dict[int, list[float]] = defaultdict(list)
        self._lock = Lock()

    def allow(self, key: int) -> bool:
        now = monotonic()
        with self._lock:
            recent = [t for t in self._calls[key] if now - t < self._window]
            if len(recent) >= self._limit:
                self._calls[key] = recent
                return False
            recent.append(now)
            self._calls[key] = recent
            return True


_scan_limiter = SlidingWindowLimiter(SCAN_LIMIT, WINDOW_SECONDS)


def enforce_scan_rate_limit(user_id: int) -> None:
    """Raise 429 if `user_id` has started too many scans too recently.

    A plain function, not a FastAPI dependency, so it can be called after
    `current_user` has already resolved the user — no need to re-parse the
    session cookie just to get an id to key on.
    """
    if not _scan_limiter.allow(user_id):
        logger.warning("rate limit hit: user_id=%s exceeded %s scans/%ss", user_id, SCAN_LIMIT, int(WINDOW_SECONDS))
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many scans — at most {SCAN_LIMIT} per "
                f"{int(WINDOW_SECONDS // 60)} minutes. Wait a moment and try again."
            ),
        )
