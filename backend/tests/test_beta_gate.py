"""The beta gate, the two emergency switches and the fixer flags (Launch Plan A5)."""
from __future__ import annotations

import pytest

from tests.test_main_audit import SECRET, _signed_in_cookie


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    for name in ("SENTINELS_ALLOWED_GITHUB_IDS", "SENTINELS_SCANS_PAUSED",
                 "SENTINELS_WRITES_PAUSED", "SENTINELS_ENABLED_FIXERS",
                 "SENTINELS_FRONTEND_ORIGIN"):
        monkeypatch.delenv(name, raising=False)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app, follow_redirects=False)


# --- migration -------------------------------------------------------------


def test_users_get_a_blocked_column_that_defaults_to_not_blocked(temp_db):
    from db import get_connection

    user, _ = _signed_in_cookie(1, "alice")
    conn = get_connection()
    try:
        assert conn.execute("SELECT blocked FROM users WHERE id = ?", (user.id,)).fetchone()["blocked"] == 0
    finally:
        conn.close()


# --- invite list -----------------------------------------------------------


def test_with_no_invite_list_anyone_signed_in_is_allowed(client):
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)
    assert client.get("/auth/me").status_code == 200


def test_an_invited_user_is_allowed_and_an_uninvited_one_is_refused(client, monkeypatch):
    monkeypatch.setenv("SENTINELS_ALLOWED_GITHUB_IDS", "1, 3")
    _, invited = _signed_in_cookie(1, "alice")
    _, uninvited = _signed_in_cookie(2, "bob")

    client.cookies.set("sentinels_session", invited)
    assert client.get("/scans").status_code == 200

    client.cookies.set("sentinels_session", uninvited)
    res = client.get("/scans")
    assert res.status_code == 403
    assert "invite-only" in res.json()["detail"]


def test_a_typo_in_the_invite_list_does_not_open_the_door(client, monkeypatch):
    monkeypatch.setenv("SENTINELS_ALLOWED_GITHUB_IDS", "one,two")   # nothing valid in it
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)
    assert client.get("/scans").status_code == 403


def test_removing_someone_from_the_list_stops_their_existing_session(client, monkeypatch):
    monkeypatch.setenv("SENTINELS_ALLOWED_GITHUB_IDS", "1")
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)
    assert client.get("/scans").status_code == 200

    monkeypatch.setenv("SENTINELS_ALLOWED_GITHUB_IDS", "99")
    assert client.get("/scans").status_code == 403


# --- blocked accounts ------------------------------------------------------


def test_a_blocked_account_is_refused_on_its_next_request(client):
    from storage.users import set_blocked

    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)
    assert client.get("/scans").status_code == 200

    assert set_blocked("Alice", True) is True            # login match ignores case
    res = client.get("/scans")
    assert res.status_code == 403
    assert "suspended" in res.json()["detail"]

    set_blocked("alice", False)
    assert client.get("/scans").status_code == 200


def test_blocking_an_unknown_login_reports_nothing_done(temp_db):
    from storage.users import set_blocked

    assert set_blocked("nobody", True) is False


# --- sign-in refuses before a session exists --------------------------------


@pytest.fixture
def github_sign_in(client, monkeypatch):
    import main
    from auth.github_oauth import GitHubIdentity

    who = {"identity": None}

    async def exchange(code, redirect_uri=None):
        return "tok"

    async def identity(token):
        return who["identity"]

    monkeypatch.setattr(main, "exchange_code", exchange)
    monkeypatch.setattr(main, "fetch_identity", identity)
    monkeypatch.setattr(main, "get_frontend_origin", lambda: "http://localhost:3000")
    who["make"] = lambda gid, login: GitHubIdentity(github_id=gid, login=login, avatar_url=None)
    return who


def _sign_in_callback(client):
    client.cookies.set("sentinels_oauth_state", "s")
    return client.get("/auth/github/callback?code=c&state=s")


def _count(table: str) -> int:
    from db import get_connection

    conn = get_connection()
    try:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    finally:
        conn.close()


def test_an_uninvited_sign_in_creates_no_user_and_no_session(client, github_sign_in, monkeypatch):
    monkeypatch.setenv("SENTINELS_ALLOWED_GITHUB_IDS", "1")
    github_sign_in["identity"] = github_sign_in["make"](2, "bob")

    res = _sign_in_callback(client)

    assert res.headers["location"].endswith("/login?error=not_invited")
    assert _count("users") == 0 and _count("sessions") == 0
    assert "sentinels_session" not in res.headers.get("set-cookie", "")


def test_a_blocked_account_cannot_sign_in_again(client, github_sign_in):
    from storage.users import set_blocked

    _signed_in_cookie(1, "alice")
    set_blocked("alice", True)
    sessions_before = _count("sessions")
    github_sign_in["identity"] = github_sign_in["make"](1, "alice")

    res = _sign_in_callback(client)

    assert res.headers["location"].endswith("/login?error=suspended")
    assert _count("sessions") == sessions_before


def test_an_invited_sign_in_works(client, github_sign_in, monkeypatch):
    monkeypatch.setenv("SENTINELS_ALLOWED_GITHUB_IDS", "1")
    github_sign_in["identity"] = github_sign_in["make"](1, "alice")

    res = _sign_in_callback(client)

    assert res.headers["location"] == "http://localhost:3000"
    assert _count("sessions") == 1


# --- scan switch -----------------------------------------------------------


SCAN_START_ROUTES = [
    ("POST", "/scan", {"url": "https://example.com"}),
    ("POST", "/repo/scan", {"url": "https://github.com/octo/demo"}),
    ("GET", "/scan/stream?url=https://example.com", None),
    ("GET", "/repo/stream?url=https://github.com/octo/demo", None),
    ("POST", "/scans/s/findings/missing-csp/verify", None),
]


@pytest.mark.parametrize("method,path,body", SCAN_START_ROUTES)
def test_pausing_scans_stops_every_way_of_starting_one(client, monkeypatch, method, path, body):
    monkeypatch.setenv("SENTINELS_SCANS_PAUSED", "1")
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)

    res = client.request(method, path, json=body)

    assert res.status_code == 503
    assert "paused" in res.json()["detail"]


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_only_a_true_looking_value_pauses_scans(client, monkeypatch, value):
    from auth.switches import scans_paused

    monkeypatch.setenv("SENTINELS_SCANS_PAUSED", value)
    assert scans_paused() is False


def test_reading_still_works_while_scans_are_paused(client, monkeypatch):
    monkeypatch.setenv("SENTINELS_SCANS_PAUSED", "true")
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)
    assert client.get("/scans").status_code == 200


# --- write switch ----------------------------------------------------------


@pytest.fixture
def apply_spy(client, monkeypatch):
    import main

    calls = []

    async def fake_apply(report, user, keys, dry_run=True, provider=None):
        calls.append(dry_run)
        from models import FixApplyPreview

        return FixApplyPreview(
            repo="octo/demo", base_branch="main", branch="sentinels/x", commit_message="m",
            pr_title="t", pr_body="b", finding_keys=list(keys), patches=[],
        )

    monkeypatch.setattr(main, "apply_fixes", fake_apply)
    from tests.test_scan_ownership import SCAN_ID, _save_url_scan

    user, cookie = _signed_in_cookie(1, "alice")
    _save_url_scan(SCAN_ID, user.id)
    client.cookies.set("sentinels_session", cookie)
    return calls, f"/scans/{SCAN_ID}/fix/apply"


def test_pausing_writes_stops_a_real_apply_but_not_a_preview(client, monkeypatch, apply_spy):
    calls, path = apply_spy
    monkeypatch.setenv("SENTINELS_WRITES_PAUSED", "1")

    real = client.post(path, json={"finding_keys": ["k"], "dry_run": False})
    preview = client.post(path, json={"finding_keys": ["k"], "dry_run": True})

    assert real.status_code == 503
    assert preview.status_code == 200
    assert calls == [True]                                 # the real apply never ran


def test_a_real_apply_goes_through_when_writes_are_not_paused(client, apply_spy):
    calls, path = apply_spy
    assert client.post(path, json={"finding_keys": ["k"], "dry_run": False}).status_code == 200
    assert calls == [False]


# --- fixer flags -----------------------------------------------------------


def test_every_fixer_is_enabled_on_a_local_machine_by_default(monkeypatch):
    from remediation.flags import enabled_fixers, fixer_enabled

    monkeypatch.delenv("SENTINELS_ENABLED_FIXERS", raising=False)
    monkeypatch.delenv("SENTINELS_FRONTEND_ORIGIN", raising=False)
    assert enabled_fixers() is None
    assert fixer_enabled("gitignore-present")


def test_no_fixer_is_enabled_on_a_deployment_until_one_is_listed(monkeypatch):
    from remediation.flags import fixer_enabled

    monkeypatch.delenv("SENTINELS_ENABLED_FIXERS", raising=False)
    monkeypatch.setenv("SENTINELS_FRONTEND_ORIGIN", "https://sentinels.example.com")
    assert not fixer_enabled("gitignore-present")

    monkeypatch.setenv("SENTINELS_ENABLED_FIXERS", "gitignore-present, ci-pin-actions")
    assert fixer_enabled("gitignore-present") and fixer_enabled("ci-pin-actions")
    assert not fixer_enabled("dockerfile-user")


def test_an_empty_list_enables_nothing_even_locally(monkeypatch):
    from remediation.flags import fixer_enabled

    monkeypatch.setenv("SENTINELS_ENABLED_FIXERS", "")
    assert not fixer_enabled("gitignore-present")


async def test_a_disabled_fixer_can_be_previewed_but_not_applied(temp_db, monkeypatch):
    from remediation.apply import ApplyError, apply_fixes
    from tests.test_remediation_apply import (
        _FakeProvider, _happy_routes, _patch_transport, _seed,
    )

    monkeypatch.setenv("SENTINELS_ENABLED_FIXERS", "some-other-fixer")
    user, report = _seed(temp_db)
    calls = _patch_transport(monkeypatch, _happy_routes())

    with pytest.raises(ApplyError) as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 403 and "preview" in str(exc.value)
    assert not [c for c in calls if c[0] != "GET"]

    preview = await apply_fixes(report, user, ["gitignore-present"], dry_run=True, provider=_FakeProvider())
    assert preview.repo == "octo/demo"


async def test_an_enabled_fixer_can_be_applied(temp_db, monkeypatch):
    from remediation.apply import apply_fixes
    from tests.test_remediation_apply import (
        _FakeProvider, _happy_routes, _patch_transport, _seed,
    )

    monkeypatch.setenv("SENTINELS_ENABLED_FIXERS", "gitignore-present")
    user, report = _seed(temp_db)
    _patch_transport(monkeypatch, _happy_routes())

    result = await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert result.pr_number == 7
