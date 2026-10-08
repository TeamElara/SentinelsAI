"""HTTP-level tests for the two Stage E audit routes.

Every other route in this project is verified live/by hand rather than
through `fastapi.testclient` (see the "Live-verified" notes throughout
PLAN-v5.md) -- but Stage E's own verification section explicitly asks for a
`TestClient` pass over these two endpoints (401 unauthenticated, 404 on
`GET /scans/{id}/audit` for another user's scan), and unlike a route that
writes to GitHub, importing `main` here does nothing except define routes
against a `temp_db` -- no installation token, no network call, nothing to
mock. That combination makes a `TestClient` pass cheap and honest for this
one pair of routes, even though it isn't this codebase's usual pattern.

`main` is imported lazily inside each test, after `temp_db` has already
pointed `db.DB_PATH` at a throwaway file (see conftest.py) -- `main.py`
calls `db.init_db()` at import time, so importing it before the monkeypatch
would create tables in the real dev database once and reuse that cached
module for every later import in the process.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

SECRET = "test-signing-secret"


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def _signed_in_cookie(github_id: int, login: str):
    from auth import session
    from storage.users import sign_in

    token = session.new_token()
    user = sign_in(
        github_id=github_id, github_login=login, avatar_url=None,
        token_hash=session.hash_token(token), expires_at=session.session_expiry(),
    )
    return user, session.cookie_value(token, SECRET)


def _save_scan(scan_id: str, user_id: int | None):
    from models import ScanReport
    from storage.scans import save_scan

    report = ScanReport(
        id=scan_id, url="https://github.com/octo/demo", target_type="repo",
        scanned_at=datetime.now(timezone.utc).isoformat(), duration_ms=1,
        score=80, grade="B", findings=[], checklist=[],
    )
    save_scan(report, user_id=user_id)


def test_get_audit_requires_sign_in(client):
    res = client.get("/audit")
    assert res.status_code == 401


def test_get_scan_audit_requires_sign_in(client):
    res = client.get("/scans/nope/audit")
    assert res.status_code == 401


def test_get_audit_returns_only_the_caller_s_rows(client):
    from storage.remediation import write_audit

    owner, cookie = _signed_in_cookie(1, "owner")
    _, _ = _signed_in_cookie(2, "someone-else")
    _save_scan("scan1", owner.id)
    write_audit(owner.id, "scan1", "gitignore-present", "pr_opened", "mine")
    write_audit(2, "scan1", "gitignore-present", "pr_opened", "not mine")

    client.cookies.set("sentinels_session", cookie)
    res = client.get("/audit")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["detail"] == "mine"
    assert rows[0]["scan_url"] == "https://github.com/octo/demo"


def test_get_scan_audit_404s_for_an_unknown_scan(client):
    _, cookie = _signed_in_cookie(1, "owner")
    client.cookies.set("sentinels_session", cookie)
    res = client.get("/scans/does-not-exist/audit")
    assert res.status_code == 404


def test_get_scan_audit_404s_for_another_user_s_scan(client):
    from storage.remediation import write_audit

    owner, _ = _signed_in_cookie(1, "owner")
    _, other_cookie = _signed_in_cookie(2, "someone-else")
    _save_scan("scan1", owner.id)
    write_audit(owner.id, "scan1", "gitignore-present", "pr_opened", "detail")

    client.cookies.set("sentinels_session", other_cookie)
    res = client.get("/scans/scan1/audit")
    assert res.status_code == 404


def test_get_scan_audit_succeeds_for_the_owner(client):
    from storage.remediation import write_audit

    owner, cookie = _signed_in_cookie(1, "owner")
    _save_scan("scan1", owner.id)
    write_audit(owner.id, "scan1", "gitignore-present", "pr_opened", "detail")

    client.cookies.set("sentinels_session", cookie)
    res = client.get("/scans/scan1/audit")
    assert res.status_code == 200
    assert [row["action"] for row in res.json()] == ["pr_opened"]


def test_get_scan_audit_404s_for_an_unowned_legacy_scan(client):
    from storage.remediation import write_audit

    _save_scan("scan1", None)
    write_audit(None, "scan1", "gitignore-present", "pr_opened", "detail")

    _, cookie = _signed_in_cookie(1, "anyone")
    client.cookies.set("sentinels_session", cookie)
    res = client.get("/scans/scan1/audit")
    assert res.status_code == 404
