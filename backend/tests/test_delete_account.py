"""Deleting an account removes the person and everything they own, keeps the
anonymous audit trail, and leaves everyone else alone (Launch Plan A7). The
scope of "everything" is spelled out in `storage/account.py`."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.test_main_audit import SECRET, _signed_in_cookie


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def _count(sql: str, *args) -> int:
    from db import get_connection

    conn = get_connection()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _rows(table: str) -> int:
    return _count(f"SELECT COUNT(*) FROM {table}")


def _populate(user, scan_id: str, installation_id: int) -> None:
    """One user's footprint in every table that holds their data."""
    from models import (
        AgentResult, ChecklistItem, FilePatch, Finding, FixApplicationState, FixPlan, ScanReport,
        Severity, Status,
    )
    from storage.chat import save_message
    from storage.installations import save_installation
    from storage.remediation import save_fix_application, save_fix_plan, write_audit
    from storage.scan_links import save_scan_repo_link
    from storage.scans import save_scan

    finding = Finding(
        id="gitignore-present", title="No .gitignore", category="Repo", severity=Severity.MEDIUM,
        status=Status.FAIL, agent="config",
    )
    save_scan(
        ScanReport(
            id=scan_id, url="https://example.com", target_type="url",
            scanned_at=datetime.now(timezone.utc).isoformat(), duration_ms=1,
            score=80, grade="B", findings=[finding],
            agents=[AgentResult(agent="config", findings=[finding], duration_ms=1)],
            checklist=[ChecklistItem(
                item_key="k", title="t", tier="self_attested", state="unknown", explanation="",
            )],
        ),
        user_id=user.id,
    )
    plan = FixPlan(
        finding_key="gitignore-present", fixer_slug="gitignore-present", tier=1, summary="s",
        patches=[FilePatch(path=".gitignore", action="create", new_content="x\n", diff="+x\n")],
        created_at="2026-10-08T00:00:00+00:00",
    )
    save_fix_plan(scan_id, plan)
    save_fix_application(scan_id, plan, FixApplicationState.PR_OPEN, branch="b", pr_url="u", pr_number=1)
    save_message(scan_id, "user", "hello")
    save_installation(user.id, installation_id, f"acct{installation_id}", "all")
    save_scan_repo_link(scan_id, user.id, installation_id, "octo", "demo")
    write_audit(user.id, scan_id, "gitignore-present", "pr_opened", "PR #1 on octo/demo")


USER_TABLES = [
    "scans", "agent_runs", "findings", "checklist_items", "chat_messages", "fix_plans",
    "fix_applications", "scan_repo_links", "github_installations", "sessions",
]


def test_deleting_removes_the_person_and_everything_they_own(client):
    alice, cookie = _signed_in_cookie(1, "alice")
    _populate(alice, "alice-scan", 500)
    empty = [t for t in USER_TABLES if _rows(t) == 0]
    assert empty == [], f"the seed didn't fill: {empty}"

    client.cookies.set("sentinels_session", cookie)
    res = client.request("DELETE", "/account", json={"confirm_login": "alice"})

    assert res.status_code == 204
    assert [_rows(t) for t in USER_TABLES] == [0] * len(USER_TABLES)
    assert _rows("users") == 0


def test_the_session_stops_working_and_the_cookie_is_cleared(client):
    alice, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)

    res = client.request("DELETE", "/account", json={"confirm_login": "alice"})

    assert "sentinels_session" in res.headers.get("set-cookie", "")
    assert client.get("/auth/me").status_code == 401


def test_the_audit_trail_stays_but_no_longer_names_the_person(client):
    alice, cookie = _signed_in_cookie(1, "alice")
    _populate(alice, "alice-scan", 500)

    client.cookies.set("sentinels_session", cookie)
    client.request("DELETE", "/account", json={"confirm_login": "alice"})

    from db import get_connection

    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT user_id, scan_id, action, detail FROM audit_log"
        ).fetchall()]
    finally:
        conn.close()
    assert rows == [
        {"user_id": None, "scan_id": None, "action": "pr_opened", "detail": "PR #1 on octo/demo"}
    ]


def test_other_people_and_unowned_scans_are_untouched(client):
    from models import ScanReport
    from storage.scans import get_scan, save_scan

    alice, alice_cookie = _signed_in_cookie(1, "alice")
    bob, _ = _signed_in_cookie(2, "bob")
    _populate(alice, "alice-scan", 500)
    _populate(bob, "bob-scan", 501)
    save_scan(
        ScanReport(
            id="legacy", url="https://old.example", scanned_at="2026-01-01T00:00:00+00:00",
            duration_ms=1, score=50, grade="F", findings=[], checklist=[],
        ),
        user_id=None,
    )

    client.cookies.set("sentinels_session", alice_cookie)
    client.request("DELETE", "/account", json={"confirm_login": "alice"})

    assert get_scan("alice-scan") is None
    assert get_scan("bob-scan") is not None and get_scan("legacy") is not None
    assert _count("SELECT COUNT(*) FROM github_installations WHERE user_id = ?", bob.id) == 1
    assert _count("SELECT COUNT(*) FROM chat_messages WHERE scan_id = 'bob-scan'") == 1
    assert _count("SELECT COUNT(*) FROM audit_log WHERE user_id = ?", bob.id) == 1
    assert _count("SELECT COUNT(*) FROM users WHERE id = ?", bob.id) == 1


@pytest.mark.parametrize("typed", ["", "bob", "alic", "alice-extra"])
def test_a_wrong_confirmation_deletes_nothing(client, typed):
    alice, cookie = _signed_in_cookie(1, "alice")
    _populate(alice, "alice-scan", 500)
    client.cookies.set("sentinels_session", cookie)

    res = client.request("DELETE", "/account", json={"confirm_login": typed})

    assert res.status_code == 400
    assert _rows("scans") == 1 and _rows("users") == 1


def test_the_confirmation_ignores_case_and_spaces(client):
    _, cookie = _signed_in_cookie(1, "Alice")
    client.cookies.set("sentinels_session", cookie)

    assert client.request("DELETE", "/account", json={"confirm_login": " alice "}).status_code == 204


def test_a_missing_body_deletes_nothing(client):
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)

    assert client.request("DELETE", "/account").status_code == 422
    assert _rows("users") == 1


def test_deleting_requires_being_signed_in(client):
    assert client.request("DELETE", "/account", json={"confirm_login": "alice"}).status_code == 401


def test_a_failure_part_way_leaves_the_account_exactly_as_it_was(temp_db, monkeypatch):
    import db
    from storage import account
    from storage.scans import get_scan

    alice, _ = _signed_in_cookie(1, "alice")
    _populate(alice, "alice-scan", 500)

    real = db.get_connection

    class Boom:
        """A connection that fails on the final DELETE of the user row."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.startswith("DELETE FROM users"):
                raise RuntimeError("disk on fire")
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(account, "get_connection", lambda: Boom(real()))

    with pytest.raises(RuntimeError):
        account.delete_account(alice.id)

    assert get_scan("alice-scan") is not None
    assert _rows("users") == 1 and _rows("github_installations") == 1
    assert _count("SELECT COUNT(*) FROM audit_log WHERE user_id = ?", alice.id) == 1
