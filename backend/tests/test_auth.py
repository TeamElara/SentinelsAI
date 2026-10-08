"""Tests for PLAN-v5 Stage 0 — identity: session cookies, the current_user
dependency, and scan ownership.

No network involved: GitHub's OAuth endpoints (`auth/github_oauth.py`) are
exercised only through their pure helpers here (`authorize_url`,
`oauth_configured`) — the same "nothing here ever makes a real network call"
rule the rest of this suite follows (see conftest.py's docstring). Actually
exchanging a code for a token is one httpx call and is deliberately left
untested against the real GitHub API; it would need a mock GitHub server to
test meaningfully and isn't where this stage's risk lives — the risk is in
`session.py`'s crypto and `deps.py`'s failure-closed behavior, which is what
this file spends its weight on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, Request

import db
from auth import deps, session
from auth.github_oauth import authorize_url, oauth_configured
from models import Finding, ScanReport, Severity, Status
from storage.scans import list_scans, save_scan, scan_owner
from storage.users import (
    create_session,
    delete_session,
    purge_expired_sessions,
    sign_in,
    upsert_user,
    user_for_token_hash,
)

SECRET = "test-signing-secret"


def _scope(cookies: dict[str, str]) -> dict:
    """The minimal ASGI scope `Request` needs to expose `.cookies`."""
    header_value = "; ".join(f"{k}={v}" for k, v in cookies.items())
    headers = [(b"cookie", header_value.encode())] if cookies else []
    return {"type": "http", "headers": headers}


def _request(cookies: dict[str, str] | None = None) -> Request:
    return Request(_scope(cookies or {}))


# --- session.py: signing and verification -----------------------------------


def test_cookie_value_round_trips_to_the_same_token():
    token = session.new_token()
    cookie = session.cookie_value(token, SECRET)
    assert session.token_from_cookie(cookie, SECRET) == token


def test_tampered_token_is_rejected():
    token = session.new_token()
    cookie = session.cookie_value(token, SECRET)
    # Flip the token half but keep the (now-mismatched) signature.
    forged = "x" + cookie
    assert session.token_from_cookie(forged, SECRET) is None


def test_tampered_signature_is_rejected():
    token = session.new_token()
    tok, _, sig = session.cookie_value(token, SECRET).partition(".")
    forged = f"{tok}.{'0' * len(sig)}"
    assert session.token_from_cookie(forged, SECRET) is None


def test_cookie_signed_with_a_different_secret_is_rejected():
    token = session.new_token()
    cookie = session.cookie_value(token, "some-other-secret")
    assert session.token_from_cookie(cookie, SECRET) is None


@pytest.mark.parametrize("garbage", ["", "no-dot-at-all", ".", "token.", ".signature"])
def test_malformed_cookies_are_rejected(garbage):
    assert session.token_from_cookie(garbage, SECRET) is None


def test_hash_token_is_deterministic_and_does_not_return_the_token():
    token = session.new_token()
    h1 = session.hash_token(token)
    h2 = session.hash_token(token)
    assert h1 == h2
    assert h1 != token


def test_session_secret_reads_from_environment(monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", "abc123")
    assert session.get_session_secret() == "abc123"


def test_blank_session_secret_is_treated_as_absent(monkeypatch):
    # Mirrors ai.client.get_api_key's `or None` — an empty string in a
    # committed .env.example must never be read as "configured".
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", "")
    assert session.get_session_secret() is None


def test_is_expired_true_for_a_past_timestamp():
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert session.is_expired(past) is True


def test_is_expired_false_for_a_future_timestamp():
    future = session.session_expiry()
    assert session.is_expired(future) is False


def test_is_expired_treats_unparseable_timestamp_as_expired():
    # Fail closed: corrupt data must never be read as "still valid".
    assert session.is_expired("not-a-timestamp") is True


# --- storage/users.py: DB round trip -----------------------------------------


def test_upsert_user_creates_then_updates_on_second_call(temp_db):
    conn = db.get_connection()
    try:
        first = upsert_user(conn, github_id=1, github_login="octocat", avatar_url="a.png")
        conn.commit()
        assert first.github_login == "octocat"

        renamed = upsert_user(conn, github_id=1, github_login="octocat2", avatar_url="b.png")
        conn.commit()
    finally:
        conn.close()

    assert renamed.id == first.id  # same row, matched on github_id not login
    assert renamed.github_login == "octocat2"


def test_sign_in_creates_a_session_that_resolves_back_to_the_user(temp_db):
    token = session.new_token()
    user = sign_in(
        github_id=42,
        github_login="ariha",
        avatar_url=None,
        token_hash=session.hash_token(token),
        expires_at=session.session_expiry(),
    )

    found = user_for_token_hash(session.hash_token(token))
    assert found is not None
    assert found.id == user.id
    assert found.github_login == "ariha"


def test_unknown_token_hash_resolves_to_none(temp_db):
    assert user_for_token_hash("not-a-real-hash") is None


def test_expired_session_resolves_to_none_and_is_deleted(temp_db):
    conn = db.get_connection()
    try:
        user = upsert_user(conn, github_id=7, github_login="stale", avatar_url=None)
        conn.commit()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        token_hash = session.hash_token(session.new_token())
        create_session(conn, user.id, token_hash, past)
        conn.commit()
    finally:
        conn.close()

    assert user_for_token_hash(token_hash) is None

    # The stale row should be gone, not just skipped.
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_delete_session_revokes_it_immediately(temp_db):
    token = session.new_token()
    sign_in(
        github_id=9, github_login="bye", avatar_url=None,
        token_hash=session.hash_token(token), expires_at=session.session_expiry(),
    )
    token_hash = session.hash_token(token)
    assert user_for_token_hash(token_hash) is not None

    assert delete_session(token_hash) is True
    assert user_for_token_hash(token_hash) is None
    assert delete_session(token_hash) is False  # already gone


def test_purge_expired_sessions_removes_only_expired_rows(temp_db):
    conn = db.get_connection()
    try:
        user = upsert_user(conn, github_id=11, github_login="mixed", avatar_url=None)
        conn.commit()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        expired_hash = session.hash_token(session.new_token())
        live_hash = session.hash_token(session.new_token())
        create_session(conn, user.id, expired_hash, past)
        create_session(conn, user.id, live_hash, session.session_expiry())
        conn.commit()
    finally:
        conn.close()

    removed = purge_expired_sessions()
    assert removed == 1
    assert user_for_token_hash(live_hash) is not None


# --- auth/deps.py: the FastAPI dependency ------------------------------------


def test_current_user_raises_401_with_no_cookie(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    with pytest.raises(HTTPException) as exc_info:
        deps.current_user(_request())
    assert exc_info.value.status_code == 401


def test_current_user_raises_401_with_a_forged_cookie(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    with pytest.raises(HTTPException) as exc_info:
        deps.current_user(_request({session.COOKIE_NAME: "garbage.value"}))
    assert exc_info.value.status_code == 401


def test_current_user_succeeds_with_a_valid_cookie(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    token = session.new_token()
    sign_in(
        github_id=100, github_login="valid", avatar_url=None,
        token_hash=session.hash_token(token), expires_at=session.session_expiry(),
    )
    cookie = session.cookie_value(token, SECRET)

    user = deps.current_user(_request({session.COOKIE_NAME: cookie}))
    assert user.github_login == "valid"


def test_current_user_raises_503_when_secret_is_unset(temp_db, monkeypatch):
    monkeypatch.delenv("SENTINELS_SESSION_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        deps.current_user(_request())
    assert exc_info.value.status_code == 503


def test_optional_user_returns_none_rather_than_raising_when_unconfigured(temp_db, monkeypatch):
    monkeypatch.delenv("SENTINELS_SESSION_SECRET", raising=False)
    assert deps.optional_user(_request()) is None


def test_optional_user_returns_none_with_no_cookie(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    assert deps.optional_user(_request()) is None


def test_optional_user_returns_the_user_with_a_valid_cookie(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    token = session.new_token()
    sign_in(
        github_id=101, github_login="opt", avatar_url=None,
        token_hash=session.hash_token(token), expires_at=session.session_expiry(),
    )
    cookie = session.cookie_value(token, SECRET)
    user = deps.optional_user(_request({session.COOKIE_NAME: cookie}))
    assert user is not None
    assert user.github_login == "opt"


# --- github_oauth.py: pure helpers, no network -------------------------------


def test_oauth_configured_false_with_no_env(monkeypatch):
    monkeypatch.delenv("GITHUB_APP_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_CLIENT_SECRET", raising=False)
    assert oauth_configured() is False


def test_oauth_configured_true_with_both_set(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "abc")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "def")
    assert oauth_configured() is True


def test_authorize_url_carries_state_and_redirect_uri(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "abc")
    url = authorize_url("the-state-value", "http://localhost:8011/auth/github/callback")
    assert "state=the-state-value" in url
    assert "client_id=abc" in url
    assert "github.com/login/oauth/authorize" in url


# --- storage/scans.py: ownership scoping (additive to Stage 0) --------------


def _report(scan_id: str) -> ScanReport:
    return ScanReport(
        id=scan_id,
        url="https://example.com",
        scanned_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=10,
        score=90,
        grade="A",
        findings=[Finding(id="x", title="X", category="Test", severity=Severity.LOW, status=Status.PASS)],
    )


def test_save_scan_with_no_user_id_is_unowned(temp_db):
    save_scan(_report("scan-unowned"))
    assert scan_owner("scan-unowned") is None


def test_save_scan_records_the_owning_user(temp_db):
    save_scan(_report("scan-owned"), user_id=5)
    assert scan_owner("scan-owned") == 5


def test_scan_owner_is_none_for_a_missing_scan(temp_db):
    assert scan_owner("does-not-exist") is None


def test_list_scans_for_a_user_includes_only_their_own(temp_db):
    save_scan(_report("mine"), user_id=1)
    save_scan(_report("legacy"))  # unowned, pre-Stage-0
    save_scan(_report("someone-elses"), user_id=2)

    ids = {s.id for s in list_scans(user_id=1)}
    assert ids == {"mine"}


def test_list_scans_with_no_user_id_returns_everything(temp_db):
    save_scan(_report("a"), user_id=1)
    save_scan(_report("b"), user_id=2)
    ids = {s.id for s in list_scans(user_id=None)}
    assert ids == {"a", "b"}
