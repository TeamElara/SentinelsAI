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
import net.policy

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


# {(method, path): (status_code, json body)}
ApiRoutes = dict[tuple[str, str], tuple[int, object]]


@pytest.fixture
def mock_api() -> Callable[[ApiRoutes], httpx.AsyncClient]:
    """Like `mock_site`, but keyed on (method, path) and speaking JSON.

    PLAN-v5 Stage B needs this because GitHub's Git Data API distinguishes
    `POST /git/refs` (create a branch) from `DELETE /git/refs/heads/x` (remove
    one) at the same-ish path — a path-only mock cannot tell a write from its
    cleanup, which is precisely what the orphan-branch tests have to check.

    Every call is also recorded on `client.calls`, so a test can assert on the
    *order* things happened in — blob before tree before commit before ref is
    a correctness property of this flow, not an implementation detail.
    """

    def _make(routes: ApiRoutes, base_url: str = "https://api.github.com") -> httpx.AsyncClient:
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            key = (request.method, request.url.path)
            calls.append(key)
            entry = routes.get(key)
            if entry is None:
                return httpx.Response(404, json={"message": "Not Found"})
            status, body = entry
            if body is None:
                return httpx.Response(status)
            return httpx.Response(status, json=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)
        client.calls = calls  # type: ignore[attr-defined]
        return client

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


# A public address that stands in for "whatever this name resolves to".
FAKE_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    """Keep the outbound policy off real DNS for every test.

    `net.policy` resolves a scan target before anything connects to it. In
    tests every name answers with one public address, so code under test
    behaves as if its target were an ordinary public site. A test about the
    policy itself passes its own resolver or replaces these again.
    """

    async def resolve(host: str) -> list[str]:
        return [FAKE_PUBLIC_IP]

    monkeypatch.setattr(net.policy, "system_resolver", resolve)
    monkeypatch.setattr(net.policy, "system_resolver_sync", lambda host: [FAKE_PUBLIC_IP])
