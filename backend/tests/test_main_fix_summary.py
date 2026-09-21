"""The Autofix badge must not keep counting findings a merged/verified fix
already handled (the gap PLAN-v5 recorded on 2026-08-13)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.test_main_audit import SECRET, _signed_in_cookie  # noqa: F401


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def _scan_with_gitignore_finding(user_id: int, monkeypatch=None):
    from models import Finding, ScanReport, Severity, Status
    from storage.scans import save_scan

    finding = Finding(
        id="gitignore-present", title="No .gitignore", category="Configuration",
        severity=Severity.MEDIUM, status=Status.FAIL, agent="repo-config",
    )
    report = ScanReport(
        id="scan1", url="https://github.com/octo/demo", target_type="repo",
        scanned_at=datetime.now(timezone.utc).isoformat(), duration_ms=1,
        score=80, grade="B", findings=[finding], checklist=[],
    )
    save_scan(report, user_id=user_id)  # the scans row (fix_applications' FK target)
    # Findings persist per agent run, which a bare report doesn't carry --
    # so hand the route the report itself rather than round-tripping it.
    import main

    monkeypatch.setattr(main, "get_scan", lambda scan_id: report)


def _plan():
    from models import FilePatch, FixPlan

    return FixPlan(
        finding_key="gitignore-present", fixer_slug="gitignore-present", tier=1,
        summary="s", created_at="2026-01-01T00:00:00+00:00",
        patches=[FilePatch(path=".gitignore", action="create", new_content=".env\n", diff="+.env")],
    )


def _count(client, cookie) -> int:
    client.cookies.set("sentinels_session", cookie)
    res = client.get("/scans/scan1/fix/summary")
    assert res.status_code == 200
    return res.json()["fixable_count"]


def test_badge_counts_an_unhandled_finding(client, monkeypatch):
    user, cookie = _signed_in_cookie(1, "owner")
    _scan_with_gitignore_finding(user.id, monkeypatch)
    assert _count(client, cookie) == 1


def test_badge_keeps_counting_an_open_pr(client, monkeypatch):
    from models import FixApplicationState
    from storage.remediation import save_fix_application

    user, cookie = _signed_in_cookie(1, "owner")
    _scan_with_gitignore_finding(user.id, monkeypatch)
    save_fix_application("scan1", _plan(), FixApplicationState.PR_OPEN)
    assert _count(client, cookie) == 1


def test_badge_drops_a_merged_fix(client, monkeypatch):
    from models import FixApplicationState
    from storage.remediation import save_fix_application

    user, cookie = _signed_in_cookie(1, "owner")
    _scan_with_gitignore_finding(user.id, monkeypatch)
    save_fix_application("scan1", _plan(), FixApplicationState.MERGED)
    assert _count(client, cookie) == 0
