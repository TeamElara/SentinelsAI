"""A website scan needs the person to say they may test the site, and the
backend records that they did (Launch Plan A6)."""
from __future__ import annotations

import pytest

from tests.test_main_audit import SECRET, _signed_in_cookie


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


@pytest.fixture
def scans_started(client, monkeypatch):
    """Replaces the scanners with recorders so nothing touches the network."""
    import main
    from models import ScanReport

    started = []

    async def fake_run_scan(url, user_id=None):
        started.append(url)
        return ScanReport(
            id="r1", url=url, scanned_at="2026-10-08T00:00:00+00:00", duration_ms=1,
            score=90, grade="A", findings=[], checklist=[],
        )

    async def fake_stream(url, user_id=None):
        started.append(url)
        if False:
            yield  # pragma: no cover

    monkeypatch.setattr(main, "run_scan", fake_run_scan)
    monkeypatch.setattr(main, "run_scan_stream", fake_stream)
    monkeypatch.setattr(main, "run_repo_scan", fake_run_scan)
    monkeypatch.setattr(main, "run_repo_scan_stream", fake_stream)
    return started


def _audit_rows():
    from db import get_connection

    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT user_id, scan_id, action, detail FROM audit_log ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()


def _as(client, github_id=1, login="alice"):
    user, cookie = _signed_in_cookie(github_id, login)
    client.cookies.set("sentinels_session", cookie)
    return user


def test_a_scan_without_confirmation_is_refused_and_starts_nothing(client, scans_started):
    _as(client)

    res = client.post("/scan", json={"url": "https://example.com"})

    assert res.status_code == 400
    assert "permission" in res.json()["detail"]
    assert scans_started == []
    assert _audit_rows() == []


def test_an_explicit_false_is_refused_too(client, scans_started):
    _as(client)

    res = client.post("/scan", json={"url": "https://example.com", "permission_confirmed": False})

    assert res.status_code == 400 and scans_started == []


def test_a_confirmed_scan_runs_and_is_recorded(client, scans_started):
    user = _as(client)

    res = client.post("/scan", json={"url": "https://example.com", "permission_confirmed": True})

    assert res.status_code == 200
    assert scans_started == ["https://example.com"]
    assert _audit_rows() == [
        {"user_id": user.id, "scan_id": None, "action": "scan_permission_confirmed",
         "detail": "https://example.com"}
    ]


def test_the_streaming_scan_needs_it_as_well(client, scans_started):
    user = _as(client)

    refused = client.get("/scan/stream", params={"url": "https://example.com"})
    allowed = client.get(
        "/scan/stream", params={"url": "https://example.com", "permission_confirmed": "true"}
    )

    assert refused.status_code == 400
    assert allowed.status_code == 200
    assert scans_started == ["https://example.com"]
    assert [r["action"] for r in _audit_rows()] == ["scan_permission_confirmed"]
    assert _audit_rows()[0]["user_id"] == user.id


def test_a_cross_site_request_cannot_supply_the_confirmation_for_the_user(client, scans_started):
    """The confirmation is in the URL, so a hostile page could include it. The
    cross-site check is what stops that, and it runs first."""
    _as(client)

    res = client.get(
        "/scan/stream",
        params={"url": "https://example.com", "permission_confirmed": "true"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert res.status_code == 403
    assert scans_started == [] and _audit_rows() == []


def test_repo_scans_do_not_ask_for_it(client, scans_started):
    _as(client)

    res = client.post("/repo/scan", json={"url": "https://github.com/octo/demo"})

    assert res.status_code == 200
    assert scans_started == ["https://github.com/octo/demo"]
    assert _audit_rows() == []
