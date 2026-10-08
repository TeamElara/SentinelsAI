"""Emergency off-switches (Launch Plan A5).

Two environment variables, read on every request so no code path caches them:

* `SENTINELS_SCANS_PAUSED` — starting a scan or re-verifying a fix returns 503.
* `SENTINELS_WRITES_PAUSED` — opening a fix pull request returns 503. Previews
  still work: they read, they don't write.

On Render, changing an environment variable restarts the service, so the time
from flipping it to it taking effect is a restart, not instant; measure it on
the real service and write the number in DEPLOY.md.
"""
from __future__ import annotations

import os

from fastapi import HTTPException

_TRUE = {"1", "true", "yes", "on"}


def _is_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def scans_paused() -> bool:
    return _is_on("SENTINELS_SCANS_PAUSED")


def writes_paused() -> bool:
    return _is_on("SENTINELS_WRITES_PAUSED")


def require_scans_running() -> None:
    """Route dependency: refuse to start scan work while scans are paused."""
    if scans_paused():
        raise HTTPException(
            status_code=503, detail="Scanning is paused for maintenance. Try again later."
        )


def require_writes_running() -> None:
    """Call before any write to GitHub."""
    if writes_paused():
        raise HTTPException(
            status_code=503,
            detail="Opening pull requests is paused for maintenance. You can still preview fixes.",
        )
