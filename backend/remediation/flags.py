"""Which fixers may open a pull request (Launch Plan A5).

A fixer is only as trustworthy as the real merge-and-verify cycle it has been
through, so a deployment lists the ones that have earned it in
`SENTINELS_ENABLED_FIXERS` (comma-separated fixer slugs). Everything else is
still planned and previewed; it just can't be applied.

* unset, on a local machine: every fixer is enabled, so development works.
* unset, on a deployment (SENTINELS_FRONTEND_ORIGIN starts with https://, the
  same signal main.py uses for secure cookies): none is. Writing to people's
  repositories must be switched on, not left on by forgetting a setting.
* set to an empty string: none is, anywhere.
"""
from __future__ import annotations

import os


def _deployed() -> bool:
    return (os.environ.get("SENTINELS_FRONTEND_ORIGIN") or "").startswith("https://")


def enabled_fixers() -> set[str] | None:
    """The fixer slugs that may apply, or None meaning all of them."""
    raw = os.environ.get("SENTINELS_ENABLED_FIXERS")
    if raw is None:
        return set() if _deployed() else None
    return {part.strip() for part in raw.split(",") if part.strip()}


def fixer_enabled(slug: str) -> bool:
    enabled = enabled_fixers()
    return enabled is None or slug in enabled
