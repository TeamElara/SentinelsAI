"""Optional AI must not erase or invalidate deterministic scan results."""
import json
from unittest.mock import AsyncMock

import httpx
import pytest

import ai.client as client_module
import ai.fixes as fixes_module
import orchestrator
from agents.base import BaseAgent
from models import Finding, Severity, Status, ScanReport
from storage.scans import get_scan, save_scan

REAL_CLIENT = httpx.AsyncClient


def fixture_finding():
    return Finding(id="missing-csp", title="CSP missing", category="Headers",
                   severity=Severity.HIGH, status=Status.FAIL, agent="headers")


class HeadersFixture(BaseAgent):
    name = "headers"
    async def scan(self, context):
        return [fixture_finding()]


@pytest.mark.parametrize("status", [401, 429])
async def test_failed_groq_keeps_report_durable_and_reload_does_not_call_it(status, monkeypatch, temp_db):
    calls = []
    def handler(request):
        if request.url.host == "api.groq.com":
            calls.append(request)
            return httpx.Response(status, json={"error": {"message": "unavailable"}})
        return httpx.Response(200, text="fixture site")
    monkeypatch.setenv("GROQ_API_KEY", "fixture-invalid-key")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: REAL_CLIENT(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(orchestrator, "AGENTS", [HeadersFixture])
    report = await orchestrator.run_scan("https://example.com")
    assert report.summary == ""
    assert report.score == 85
    for _ in range(3):
        stored = get_scan(report.id)
        assert stored.score == 85
        assert stored.findings[0].id == "missing-csp"
        assert stored.summary == ""
    assert len(calls) == 1


async def test_summary_survives_reload_without_regeneration(monkeypatch, temp_db):
    monkeypatch.setattr(orchestrator, "AGENTS", [HeadersFixture])
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: REAL_CLIENT(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    summarize = AsyncMock(return_value="Observed CSP is missing.")
    monkeypatch.setattr(orchestrator, "summarize", summarize)
    report = await orchestrator.run_scan("https://example.com")
    assert get_scan(report.id).summary == "Observed CSP is missing."
    assert get_scan(report.id).summary == report.summary
    summarize.assert_awaited_once()


def saved_report():
    report = ScanReport(id="ai-fixture", url="https://example.com", scanned_at="2026-10-08",
                        duration_ms=1, score=85, grade="B", findings=[fixture_finding()])
    from models import AgentResult
    report.agents = [AgentResult(agent="headers", findings=report.findings)]
    save_scan(report)
    return report


async def test_cached_fix_available_without_provider_key(monkeypatch, temp_db):
    report = saved_report()
    monkeypatch.setenv("GROQ_API_KEY", "fixture-key")
    raw = json.dumps(dict(why_it_exists="Missing configuration", security_impact="Exposure",
        exploitation="Conceptual abuse", recommended_fix="Set CSP", best_practices=["Review policy"], framework_examples={}))
    provider = AsyncMock(return_value=raw)
    monkeypatch.setattr(fixes_module, "call_groq", provider)
    first = await fixes_module.get_or_generate_fix(report.id, "missing-csp", report.findings[0])
    assert first is not None
    monkeypatch.delenv("GROQ_API_KEY")
    cached = await fixes_module.get_or_generate_fix(report.id, "missing-csp", report.findings[0])
    assert cached == first
    provider.assert_awaited_once()


@pytest.mark.parametrize("raw", ['[]', 'null', '{}', '{"best_practices": 12}', 'not json'])
async def test_malformed_fix_degrades_cleanly(raw, monkeypatch, temp_db):
    report = saved_report()
    monkeypatch.setenv("GROQ_API_KEY", "fixture-key")
    monkeypatch.setattr(fixes_module, "call_groq", AsyncMock(return_value=raw))
    assert await fixes_module.get_or_generate_fix(report.id, "missing-csp", report.findings[0]) is None
    assert get_scan(report.id).score == 85
