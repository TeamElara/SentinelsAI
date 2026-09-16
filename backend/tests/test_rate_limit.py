"""Tests for rate_limit.py's per-user sliding window.

All pure — no HTTP, no clock mocking beyond passing an explicit `now` isn't
needed since the window (300s) is far longer than a test run takes.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from rate_limit import SlidingWindowLimiter, enforce_scan_rate_limit


def test_allows_up_to_the_limit():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60)
    assert limiter.allow(1) is True
    assert limiter.allow(1) is True
    assert limiter.allow(1) is True


def test_blocks_the_call_past_the_limit():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        limiter.allow(1)
    assert limiter.allow(1) is False


def test_different_keys_have_independent_budgets():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    assert limiter.allow(1) is True
    assert limiter.allow(2) is True  # a different user, not throttled by user 1's call
    assert limiter.allow(1) is False


def test_enforce_scan_rate_limit_raises_429_once_exhausted():
    # Uses the real module-level limiter and a user id unlikely to collide
    # with another test in the same process.
    user_id = 999_999_001
    from rate_limit import SCAN_LIMIT

    for _ in range(SCAN_LIMIT):
        enforce_scan_rate_limit(user_id)  # should not raise

    with pytest.raises(HTTPException) as exc_info:
        enforce_scan_rate_limit(user_id)
    assert exc_info.value.status_code == 429
