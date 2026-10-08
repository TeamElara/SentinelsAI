"""Every route that takes a scan id only works for that scan's owner, and the
stream routes refuse a scan started from another website (Launch Plan A1).

The "another user, unowned scan and missing scan all get the same 404, and no
sign-in gets 401" checks for every route live in `test_access_control.py`,
which walks `app.routes` so a new route can't be left out. This file keeps what
that one can't derive from a route: the owner still being able to use their own
scan, writes by another user changing nothing, the scan list, and the stream
routes' cross-site check.

Uses the same `TestClient` + signed-cookie setup as `test_main_audit.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.test_main_audit import SECRET, _signed_in_cookie  # noqa: F401

SCAN_ID = "scan-owned-by-alice"

# Routes the owner can call here without reaching GitHub, Groq or the network.
OWNER_READ_ROUTES = [
    "/scans/{id}",
    "/scans/{id}/files",
    "/scans/{id}/checklist",
    "/scans/{id}/fix/summary",
    "/scans/{id}/audit",
    "/scans/{id}/chat",
    "/scans/{id}/export/json",
    "/scans/{id}/link-repo",
]


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    # A configured key means the AI routes reach their ownership check
    # instead of stopping at "AI not configured"; nothing here calls Groq.
    monkeypatch.setenv("GROQ_API_KEY", "test-key-never-used")
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def _save_url_scan(scan_id: str, user_id: int | None) -> None:
    from models import ChecklistItem, ScanReport
    from storage.scans import save_scan

    report = ScanReport(
        id=scan_id, url="https://example.com", target_type="url",
        scanned_at=datetime.now(timezone.utc).isoformat(), duration_ms=1,
        score=90, grade="A", findings=[],
        checklist=[
            ChecklistItem(
                item_key="some-item", title="Backups tested",
                tier="self_attested", state="unknown", explanation="",
            )
        ],
    )
    save_scan(report, user_id=user_id)


@pytest.mark.parametrize("path", OWNER_READ_ROUTES)
def test_the_owner_can_still_read_their_scan(client, path):
    alice, cookie = _signed_in_cookie(1, "alice")
    _save_url_scan(SCAN_ID, alice.id)

    client.cookies.set("sentinels_session", cookie)
    res = client.get(path.format(id=SCAN_ID))

    assert res.status_code == 200


def test_another_user_cannot_delete_the_scan(client):
    from storage.scans import get_scan

    alice, _ = _signed_in_cookie(1, "alice")
    _, bob_cookie = _signed_in_cookie(2, "bob")
    _save_url_scan(SCAN_ID, alice.id)

    client.cookies.set("sentinels_session", bob_cookie)
    client.delete(f"/scans/{SCAN_ID}")

    assert get_scan(SCAN_ID) is not None


def test_another_user_cannot_change_a_checklist_answer(client):
    from storage.scans import get_scan

    alice, _ = _signed_in_cookie(1, "alice")
    _, bob_cookie = _signed_in_cookie(2, "bob")
    _save_url_scan(SCAN_ID, alice.id)

    client.cookies.set("sentinels_session", bob_cookie)
    client.post(f"/scans/{SCAN_ID}/checklist/some-item", json={"state": "pass"})

    item = get_scan(SCAN_ID).checklist[0]
    assert item.state == "unknown"


def test_the_owner_can_change_a_checklist_answer(client):
    alice, cookie = _signed_in_cookie(1, "alice")
    _save_url_scan(SCAN_ID, alice.id)

    client.cookies.set("sentinels_session", cookie)
    res = client.post(f"/scans/{SCAN_ID}/checklist/some-item", json={"state": "pass"})

    assert res.status_code == 200
    assert res.json()["state"] == "pass"


def test_the_scan_list_shows_only_the_caller_s_scans(client):
    alice, cookie = _signed_in_cookie(1, "alice")
    bob, _ = _signed_in_cookie(2, "bob")
    _save_url_scan("alices", alice.id)
    _save_url_scan("bobs", bob.id)
    _save_url_scan("legacy", None)

    client.cookies.set("sentinels_session", cookie)
    res = client.get("/scans")

    assert res.status_code == 200
    assert [s["id"] for s in res.json()] == ["alices"]


# --- Starting a scan from another website ---------------------------------

STREAM_ROUTES = [
    ("/scan/stream", "https://example.com"),
    ("/repo/stream", "https://github.com/octo/demo"),
]


@pytest.fixture
def stream_spy(client, monkeypatch):
    """Replaces the scan generators and the rate limiter with recorders, so
    a test can tell whether a request actually started a scan or used quota."""
    import main

    calls = {"scans": 0, "rate_limit": 0}

    async def fake_stream(url, user_id=None):
        calls["scans"] += 1
        if False:
            yield  # pragma: no cover - makes this an async generator

    def fake_rate_limit(user_id):
        calls["rate_limit"] += 1

    monkeypatch.setattr(main, "run_scan_stream", fake_stream)
    monkeypatch.setattr(main, "run_repo_scan_stream", fake_stream)
    monkeypatch.setattr(main, "enforce_scan_rate_limit", fake_rate_limit)
    return calls


@pytest.mark.parametrize("path,target", STREAM_ROUTES)
@pytest.mark.parametrize("site", ["cross-site", "none"])
def test_a_scan_started_from_another_site_is_refused(client, stream_spy, path, target, site):
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)

    res = client.get(path, params={"url": target}, headers={"Sec-Fetch-Site": site})

    assert res.status_code == 403
    assert stream_spy == {"scans": 0, "rate_limit": 0}


@pytest.mark.parametrize("path,target", STREAM_ROUTES)
@pytest.mark.parametrize("site", ["same-origin", "same-site", None])
def test_a_scan_started_from_sentinels_itself_goes_ahead(client, stream_spy, path, target, site):
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)
    headers = {"Sec-Fetch-Site": site} if site else {}

    res = client.get(
        path, params={"url": target, "permission_confirmed": "true"}, headers=headers
    )

    assert res.status_code == 200
    assert stream_spy == {"scans": 1, "rate_limit": 1}
