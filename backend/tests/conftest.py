"""Shared pytest fixtures for the backend test suite.

Nothing here, or in any file under tests/, ever makes a real network call —
every agent test builds its own tiny fake site with `mock_site` and points a
mocked `httpx.AsyncClient` at it.
"""
from __future__ import annotations

from typing import Callable

import httpx
import pytest

import db

# {path: (status_code, headers, body)}
Routes = dict[str, tuple[int, dict[str, str], str]]


def _build_transport(routes: Routes) -> httpx.MockTransport:
    """Turn a `{path: (status, headers, body)}` map into a fake transport.

    Matches on path only (query string ignored) — every agent in this project
    probes fixed paths, so that's all a test ever needs to describe. Anything
    not listed answers a plain 404, so a test only writes the paths it cares
    about.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        entry = routes.get(request.url.path)
        if entry is None:
            return httpx.Response(404, text="")
        status, headers, body = entry
        return httpx.Response(status, headers=headers, text=body)

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_site() -> Callable[[Routes], httpx.AsyncClient]:
    """`mock_site({"/": (200, {}, "hi")})` -> an `httpx.AsyncClient` wired to
    that fake site, ready to hand to an agent's `ScanContext`.
    """

    def _make(routes: Routes, base_url: str = "https://example.com") -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=_build_transport(routes), base_url=base_url)

    return _make


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point `db.DB_PATH` at a throwaway file for this test only, so storage
    tests never touch the real dev database at `backend/data/sentinels.db`.

    `storage/scans.py` and friends import `get_connection` from `db` and call
    it fresh per operation, so patching `db.DB_PATH` before `init_db()` is
    enough — every subsequent `get_connection()` call in the test opens the
    temp file instead.
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db.DB_PATH
