import asyncio
import json
import time

import httpx
import pytest

import orchestrator
import repo_orchestrator
from agents.base import BaseAgent, ScanContext
from agents.probe import Budget, RobotsGate, safe_get
from agents.repo.base import BaseRepoAgent, RepoContext, RepoFile
from agents.repo.dependencies import DependenciesAgent
from checklist.evaluator import evaluate, compute_readiness
from models import AgentResult, CheckCoverage, ScanReport
from report.html_doc import render_html
from report.markdown import MarkdownExporter
from report.json_export import JsonExporter
from scan_coverage import record
from storage.scans import get_scan


class CompletedAgent(BaseAgent):
    name = "headers"
    checks = ["Header policy"]
    async def scan(self, context):
        await asyncio.sleep(0)
        return []


class FailedProbeAgent(BaseAgent):
    name = "misconfig"
    checks = ["Public config"]
    async def scan(self, context):
        await safe_get(context, context.url)
        Budget(0, 1).allow()
        return []


async def test_unavailable_probe_and_budget_are_isolated_from_other_agents():
    def handler(request):
        raise httpx.ConnectError("fixture", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        context = ScanContext(url="https://example.com", client=client)
        completed, partial = await asyncio.gather(CompletedAgent().run(context), FailedProbeAgent().run(context))
    assert completed.coverage_status == "completed"
    assert partial.coverage_status != "completed"
    assert {c.status for c in partial.coverage} >= {"partial", "unavailable"}
    assert all(c.reason for c in partial.coverage)


async def test_robots_skip_is_recorded(mock_site):
    class RobotsAgent(CompletedAgent):
        async def scan(self, context):
            gate = RobotsGate()
            await gate.load(context)
            assert not gate.allowed("/admin")
            return []
    async with mock_site({"/robots.txt": (200, {}, "User-agent: *\nDisallow: /admin")}) as client:
        result = await RobotsAgent().run(ScanContext(url="https://example.com", client=client))
    assert any(c.status == "skipped" and "/admin" in c.check for c in result.coverage)
    assert result.coverage_status != "completed"


@pytest.mark.parametrize("target", ["url", "repo"])
async def test_incomplete_final_report_storage_and_exports(target, monkeypatch, temp_db):
    monkeypatch.setattr(orchestrator, "summarize", _empty_summary)
    monkeypatch.setattr(repo_orchestrator, "summarize", _empty_summary)
    result = AgentResult(agent="headers" if target == "url" else "repo-config", error="fixture crash",
                         coverage=[CheckCoverage(check="Configuration", status="failed", reason="fixture crash")])
    finalize = orchestrator._finalize if target == "url" else repo_orchestrator._finalize
    report = await finalize("https://example.com", time.perf_counter(), [result])
    assert report.score == 100
    assert report.provisional
    assert report.deployment_status != "ready"
    assert "provisional" in report.summary
    assert not any(c.state == "pass" for c in report.checklist if c.agent == result.agent)
    stored = get_scan(report.id)
    assert stored.provisional and stored.agents[0].coverage == result.coverage
    md = (await MarkdownExporter().render(stored)).decode()
    html = render_html(stored)
    data = json.loads(await JsonExporter().render(stored))
    assert "provisional" in md and "fixture crash" in md
    assert "provisional" in html and "fixture crash" in html
    assert "Every check passed" not in html + md
    assert data["provisional"] and data["agents"][0]["coverage"][0]["status"] == "failed"


async def _empty_summary(*args, **kwargs):
    return ""


async def test_osv_incomplete_response_is_not_a_clean_dependency_scan(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("requests==2.0.0\nflask==1.0.0")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": [{}]}))) as client:
        context = RepoContext(repo_url="https://github.com/octo/test", owner="octo", repo="test",
            ref="main", root=tmp_path, files=[RepoFile("requirements.txt", path, path.stat().st_size)], client=client)
        result = await DependenciesAgent().run(context)
    assert result.coverage_status != "completed"
    assert any(c.check == "OSV lookup" and c.status == "unavailable" for c in result.coverage)


def test_legacy_report_and_unknown_checklist_never_claim_ready():
    report = ScanReport(url="https://example.com", scanned_at="old", duration_ms=1, score=100,
                        grade="A", deployment_status="ready", agents=[AgentResult(agent="headers")])
    assert report.provisional and report.deployment_status == "incomplete"
    items = evaluate([], agent_results=[])
    assert compute_readiness(items)[1] == "incomplete"
