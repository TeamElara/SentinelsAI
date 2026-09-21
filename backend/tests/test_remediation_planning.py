"""Tests for remediation/planning.py -- the orchestration layer main.py's
fix/plan endpoints call.

`preview_plan`/`plan_and_save` build their own `httpx.AsyncClient`
internally (no injection point), so -- same technique test_orchestrator.py
already uses for exactly this reason -- these tests monkeypatch
`httpx.AsyncClient` for the duration of one test.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from db import get_connection
from models import Finding, ScanReport, Severity, Status
from remediation.planning import NotARepoScan, build_bundle_zip, plan_and_save, preview_plan
from storage.remediation import get_fix_plan


def _insert_bare_scan(scan_id: str) -> None:
    """`fix_plans.scan_id` is a foreign key -- plan_and_save/build_bundle_zip
    tests need a real `scans` row to point at, not the full save_scan()
    pipeline this test has no use for."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO scans (id, url, target_type, scanned_at, duration_ms, score, grade, created_at)
            VALUES (?, 'https://github.com/octo/demo', 'repo', '2026-01-01T00:00:00+00:00', 1, 80, 'B', '2026-01-01T00:00:00+00:00')
            """,
            (scan_id,),
        )
        conn.commit()
    finally:
        conn.close()

_RealAsyncClient = httpx.AsyncClient


def _patch_client(monkeypatch, routes: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        entry = routes.get(request.url.path)
        if entry is None:
            return httpx.Response(404, text="")
        status, headers, body = entry
        return httpx.Response(status, headers=headers, text=body)

    def factory(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _contents_response(sha: str, content: str) -> tuple[int, dict, str]:
    body = json.dumps({
        "sha": sha,
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    })
    return (200, {"content-type": "application/json"}, body)


def _report(findings: list[Finding], target_type: str = "repo", url: str = "https://github.com/octo/demo") -> ScanReport:
    return ScanReport(
        id="scan1",
        url=url,
        target_type=target_type,
        scanned_at="2026-01-01T00:00:00+00:00",
        duration_ms=1,
        score=80,
        grade="B",
        findings=findings,
    )


_GITIGNORE_FINDING = Finding(
    id="gitignore-present", title="No .gitignore", category="Configuration",
    severity=Severity.MEDIUM, status=Status.FAIL,
)


async def test_preview_plan_returns_a_plan_for_a_fixable_finding(monkeypatch):
    _patch_client(monkeypatch, {
        "/repos/octo/demo": (200, {"content-type": "application/json"}, json.dumps({"default_branch": "main"})),
        # .gitignore contents lookup 404s -- doesn't exist -- fixer can plan
    })
    report = _report([_GITIGNORE_FINDING])
    plan = await preview_plan(report, "gitignore-present")
    assert plan is not None
    assert plan.patches[0].path == ".gitignore"


async def test_preview_plan_returns_none_for_unfixable_finding(monkeypatch):
    _patch_client(monkeypatch, {
        "/repos/octo/demo": (200, {"content-type": "application/json"}, json.dumps({"default_branch": "main"})),
    })
    finding = Finding(id="spf-record", title="t", category="c", severity=Severity.LOW, status=Status.WARN)
    report = _report([finding])
    plan = await preview_plan(report, "spf-record")
    assert plan is None


async def test_preview_plan_returns_none_for_unknown_finding_key(monkeypatch):
    _patch_client(monkeypatch, {
        "/repos/octo/demo": (200, {"content-type": "application/json"}, json.dumps({"default_branch": "main"})),
    })
    report = _report([_GITIGNORE_FINDING])
    plan = await preview_plan(report, "does-not-exist")
    assert plan is None


async def test_preview_plan_raises_not_a_repo_scan_for_url_scans(temp_db):
    report = _report([_GITIGNORE_FINDING], target_type="url", url="https://example.com")
    with pytest.raises(NotARepoScan):
        await preview_plan(report, "gitignore-present")


# --- PLAN-v5 Stage D: a URL scan linked to a repository ----------------------

async def test_preview_plan_for_a_linked_url_scan_reads_the_linked_repo(monkeypatch, temp_db):
    from storage.scan_links import save_scan_repo_link
    from storage.users import sign_in

    user = sign_in(
        github_id=1, github_login="octo", avatar_url=None,
        token_hash="hash1", expires_at="2099-01-01T00:00:00+00:00",
    )
    _insert_bare_scan("url-scan-1")
    save_scan_repo_link("url-scan-1", user.id, 500, "octo", "demo")

    _patch_client(monkeypatch, {
        "/repos/octo/demo": (200, {"content-type": "application/json"}, json.dumps({"default_branch": "main"})),
        "/repos/octo/demo/contents/vercel.json": _contents_response("v1", "{}"),
    })
    finding = Finding(
        id="missing-hsts", title="t", category="Headers",
        severity=Severity.HIGH, status=Status.FAIL, agent="headers",
    )
    report = _report([finding], target_type="url", url="https://example.com")
    report.id = "url-scan-1"

    plan = await preview_plan(report, "missing-hsts")
    assert plan is not None
    assert plan.patches[0].path == "vercel.json"


async def test_preview_plan_for_an_unlinked_url_scan_still_refuses(monkeypatch, temp_db):
    finding = Finding(
        id="missing-hsts", title="t", category="Headers",
        severity=Severity.HIGH, status=Status.FAIL, agent="headers",
    )
    report = _report([finding], target_type="url", url="https://example.com")
    with pytest.raises(NotARepoScan, match="linked repository"):
        await preview_plan(report, "missing-hsts")


async def test_plan_and_save_persists_valid_plans_and_reports_none_for_the_rest(monkeypatch, temp_db):
    _patch_client(monkeypatch, {
        "/repos/octo/demo": (200, {"content-type": "application/json"}, json.dumps({"default_branch": "main"})),
    })
    unfixable = Finding(id="spf-record", title="t", category="c", severity=Severity.LOW, status=Status.WARN)
    report = _report([_GITIGNORE_FINDING, unfixable])
    _insert_bare_scan(report.id)

    results = await plan_and_save(report, ["gitignore-present", "spf-record", "not-a-real-key"])

    assert results["gitignore-present"] is not None
    assert results["spf-record"] is None
    assert results["not-a-real-key"] is None
    assert get_fix_plan("scan1", "gitignore-present") is not None
    assert get_fix_plan("scan1", "spf-record") is None


def test_build_bundle_zip_returns_none_when_nothing_planned(temp_db):
    assert build_bundle_zip("no-such-scan") is None


async def test_build_bundle_zip_contains_a_diff_per_saved_plan(monkeypatch, temp_db):
    import zipfile
    import io

    _patch_client(monkeypatch, {
        "/repos/octo/demo": (200, {"content-type": "application/json"}, json.dumps({"default_branch": "main"})),
    })
    report = _report([_GITIGNORE_FINDING])
    _insert_bare_scan(report.id)
    await plan_and_save(report, ["gitignore-present"])

    bundle = build_bundle_zip("scan1")
    assert bundle is not None
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert "gitignore-present" in names[0]
