"""Two holes found reviewing the A+B+C integration.

1. `POST /scans/{id}/link-repo` stored whatever `repo`/`ref` it was given, and
   both are pasted into GitHub API URLs that carry the installation token. A
   `repo` of `../../user/emails#` turned into `https://api.github.com/user/emails#…`.
2. Deleting an account cascades away its `usage` rows, so deleting and signing
   in again handed out a fresh daily allowance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from tests.test_main_audit import SECRET, _signed_in_cookie
from tests.test_scan_ownership import _save_url_scan

SCAN_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def _linkable(client):
    """A signed-in owner of a URL scan who holds a live installation."""
    from storage.installations import save_installation

    alice, cookie = _signed_in_cookie(1, "alice")
    _save_url_scan(SCAN_ID, alice.id)
    save_installation(alice.id, 4242, "alice-org", "selected")
    client.cookies.set("sentinels_session", cookie)
    return alice


def _link(client, **body):
    return client.post(
        f"/scans/{SCAN_ID}/link-repo", json={"installation_id": 4242, **body}
    )


@pytest.mark.parametrize(
    "repo",
    ["../../user/emails#", "ok/../../../orgs/x?", "a/b", "..", ".", "x?y", "a%2Fb", "a b", ""],
)
def test_link_repo_refuses_a_repo_that_is_not_a_plain_name(client, repo):
    from storage.scan_links import get_scan_repo_link

    _linkable(client)

    assert _link(client, repo=repo).status_code == 422
    assert get_scan_repo_link(SCAN_ID) is None


@pytest.mark.parametrize("ref", ["../x", "a//b", "/x", "x/", "x y", "x?y", "x#y", "a/../b"])
def test_link_repo_refuses_a_ref_that_is_not_a_plain_name(client, ref):
    from storage.scan_links import get_scan_repo_link

    _linkable(client)

    assert _link(client, repo="demo", ref=ref).status_code == 422
    assert get_scan_repo_link(SCAN_ID) is None


@pytest.mark.parametrize(
    "repo, ref",
    [("demo", None), ("my-repo.js", "main"), ("under_score", "release/1.2"), ("demo", "v1.0.0")],
)
def test_link_repo_still_accepts_ordinary_names(client, repo, ref):
    _linkable(client)

    res = _link(client, repo=repo, ref=ref)

    assert res.status_code == 200
    assert res.json()["repo"] == repo and res.json()["ref"] == ref


def test_a_bad_link_saved_before_validation_is_not_used(temp_db):
    """Rows written by the old route can hold anything; the pipeline refuses them."""
    from models import ScanReport
    from remediation.linking import NoRepoTarget, repo_target
    from storage.installations import save_installation
    from storage.scan_links import save_scan_repo_link
    from storage.scans import get_scan

    alice, _ = _signed_in_cookie(1, "alice")
    _save_url_scan(SCAN_ID, alice.id)
    save_installation(alice.id, 4242, "alice-org", "selected")
    report: ScanReport = get_scan(SCAN_ID)

    save_scan_repo_link(SCAN_ID, alice.id, 4242, "alice-org", "../../user/emails#")
    with pytest.raises(NoRepoTarget):
        repo_target(report)

    save_scan_repo_link(SCAN_ID, alice.id, 4242, "alice-org", "demo", "../main")
    with pytest.raises(NoRepoTarget):
        repo_target(report)

    save_scan_repo_link(SCAN_ID, alice.id, 4242, "alice-org", "demo", "main")
    assert repo_target(report) == ("alice-org", "demo", "main")


# --- deleting an account must not reset the day's budgets ----------------------


def _sign_in_again(github_id: int):
    import db
    from storage.users import upsert_user

    conn = db.get_connection()
    user = upsert_user(conn, github_id, "returning", None)
    conn.commit()
    conn.close()
    return user


def _fresh_user(github_id: int):
    return _sign_in_again(github_id)


def test_deleting_an_account_does_not_hand_out_a_fresh_daily_allowance(temp_db, monkeypatch):
    import usage
    from storage.account import delete_account

    monkeypatch.setenv("SENTINELS_DAILY_URL_SCAN", "3")
    before = _fresh_user(100)
    for _ in range(3):
        usage.reserve(before.id, "url_scan").complete()

    delete_account(before.id)
    after = _sign_in_again(100)

    assert after.id != before.id
    assert usage.usage_for(after.id)["budgets"]["url_scan"]["used"] == 3
    with pytest.raises(HTTPException) as exc:
        usage.reserve(after.id, "url_scan")
    assert exc.value.status_code == 429


def test_the_carried_over_count_is_per_person_and_per_day(temp_db, monkeypatch):
    import usage
    from storage.account import delete_account

    monkeypatch.setenv("SENTINELS_DAILY_URL_SCAN", "3")
    leaver = _fresh_user(100)
    bystander = _fresh_user(200)
    usage.reserve(leaver.id, "url_scan").complete()
    delete_account(leaver.id)

    # Someone else is unaffected ...
    assert usage.usage_for(bystander.id)["budgets"]["url_scan"]["used"] == 0
    # ... and the same person gets a clean slate when the UTC day rolls over.
    returning = _sign_in_again(100)
    assert usage.usage_for(returning.id)["budgets"]["url_scan"]["used"] == 1
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1, minutes=1)
    monkeypatch.setattr(usage, "_now", lambda: tomorrow)
    assert usage.usage_for(returning.id)["budgets"]["url_scan"]["used"] == 0


def test_deleting_twice_in_a_day_keeps_the_larger_count_and_prunes_old_rows(temp_db):
    import db
    import usage
    from storage.account import delete_account

    conn = db.get_connection()
    conn.execute(
        "INSERT INTO usage_carryover(github_id,day,kind,count) VALUES (999,'2000-01-01','chat',5)"
    )
    conn.commit()
    conn.close()

    first = _fresh_user(100)
    for _ in range(2):
        usage.reserve(first.id, "chat").complete()
    delete_account(first.id)
    second = _sign_in_again(100)
    usage.reserve(second.id, "chat").complete()       # restored 2, now 3
    delete_account(second.id)

    conn = db.get_connection()
    try:
        rows = {
            (r["github_id"], r["kind"]): r["count"]
            for r in conn.execute("SELECT * FROM usage_carryover").fetchall()
        }
    finally:
        conn.close()
    assert rows == {(100, "chat"): 3}
