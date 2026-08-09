"""Tests for orchestrator.py's crash-isolation and URL-validation contracts
(PLAN-v4 §V9's cross-cutting matrix item: "one agent raising -> other seven
still report and the scan still finishes").

`run_scan`/`run_scan_stream` build their own `httpx.AsyncClient` internally
(no injection point), so these tests monkeypatch `httpx.AsyncClient` itself
for the duration of one test -- capturing the real class first so the
replacement factory doesn't recurse into itself.
"""
from __future__ import annotations

import httpx
import pytest

import orchestrator
from agents.base import BaseAgent, ScanContext
from models import Finding, Severity, Status
from orchestrator import normalize_url, run_scan, run_scan_stream

_RealAsyncClient = httpx.AsyncClient


def _patch_client(monkeypatch, handler):
    """Replace httpx.AsyncClient for this test with one wired to `handler`,
    ignoring whatever constructor kwargs orchestrator.py passes (timeout=...)."""

    def factory(*args, **kwargs):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.com")

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _raising_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="hi")


# --- normalize_url ---------------------------------------------------------


def test_empty_url_is_rejected():
    with pytest.raises(ValueError):
        normalize_url("")


def test_unsupported_scheme_is_rejected():
    with pytest.raises(ValueError):
        normalize_url("javascript://alert(1)")


def test_bare_host_gets_https_prepended():
    assert normalize_url("example.com") == "https://example.com/"


def test_scheme_and_case_are_normalized():
    assert normalize_url("HTTP://Example.COM") == "http://example.com/"


# --- unreachable host --------------------------------------------------------


async def test_unreachable_host_raises_friendly_value_error(monkeypatch):
    _patch_client(monkeypatch, _raising_handler)
    with pytest.raises(ValueError, match="Couldn't reach"):
        await run_scan("https://example.com")


# --- crash isolation: one agent raising must not take down the scan --------


class _CleanAgent(BaseAgent):
    display_name = "Clean"
    category = "Test"

    async def scan(self, context: ScanContext) -> list[Finding]:
        return [Finding(id="ok", title="ok", category="Test", severity=Severity.INFO, status=Status.PASS)]


class _BrokenAgent(BaseAgent):
    name = "broken"
    display_name = "Broken"
    category = "Test"

    async def scan(self, context: ScanContext) -> list[Finding]:
        raise RuntimeError("simulated agent crash")


def _make_clean_agent(slug: str) -> type[BaseAgent]:
    return type(f"Clean_{slug}", (_CleanAgent,), {"name": slug})


def _eight_agents_one_broken() -> list[type[BaseAgent]]:
    clean = [_make_clean_agent(f"clean-{i}") for i in range(7)]
    return [*clean, _BrokenAgent]


async def test_one_broken_agent_leaves_the_other_seven_intact(monkeypatch, temp_db):
    _patch_client(monkeypatch, _ok_handler)
    monkeypatch.setattr(orchestrator, "AGENTS", _eight_agents_one_broken())

    report = await run_scan("https://example.com")

    assert len(report.agents) == 8
    broken = [a for a in report.agents if a.agent == "broken"]
    clean = [a for a in report.agents if a.agent != "broken"]
    assert len(broken) == 1
    assert broken[0].error is not None
    assert "simulated agent crash" in broken[0].error
    assert len(clean) == 7
    assert all(a.error is None for a in clean)
    # the scan still produces a complete, scored, saved report
    assert isinstance(report.score, int)
    assert report.grade in "ABCDF"
    assert report.id


async def test_stream_also_isolates_the_broken_agent(monkeypatch, temp_db):
    _patch_client(monkeypatch, _ok_handler)
    monkeypatch.setattr(orchestrator, "AGENTS", _eight_agents_one_broken())

    events = [event async for event in run_scan_stream("https://example.com")]

    agent_events = [payload for kind, payload in events if kind == "agent"]
    done_events = [payload for kind, payload in events if kind == "done"]
    assert len(agent_events) == 8
    assert len(done_events) == 1

    broken = [a for a in agent_events if a.agent == "broken"]
    assert len(broken) == 1
    assert broken[0].error is not None
    assert done_events[0].agents == agent_events
