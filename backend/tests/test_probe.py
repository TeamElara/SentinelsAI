"""Tests for the shared probe layer (PLAN-v4 §V2, formalized in §V9).

V2 shipped this module verified only by an ad hoc script, not a committed
test -- pytest infra didn't exist yet at the time. This file is that test,
written properly, plus the failure-injection cases the original script
didn't cover.
"""
from __future__ import annotations

import asyncio
import ssl

import httpx
import pytest

from agents.base import ScanContext
from agents.probe import Budget, ResponseCache, RobotsGate, safe_get, safe_head, safe_options


# --- ResponseCache -----------------------------------------------------------

async def test_concurrent_identical_fetches_hit_the_transport_once():
    hits = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hits
        hits += 1
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.com")
    cache = ResponseCache()

    results = await asyncio.gather(*(cache.get(client, "https://example.com/") for _ in range(5)))

    await client.aclose()
    assert hits == 1
    assert all(r.status_code == 200 for r in results)


async def test_different_methods_are_not_shared():
    seen_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_methods.append(request.method)
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.com")
    cache = ResponseCache()

    await cache.get(client, "https://example.com/", method="GET")
    await cache.get(client, "https://example.com/", method="HEAD")

    await client.aclose()
    assert seen_methods == ["GET", "HEAD"]


async def test_different_urls_are_not_shared():
    hits = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hits
        hits += 1
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.com")
    cache = ResponseCache()

    await cache.get(client, "https://example.com/a")
    await cache.get(client, "https://example.com/b")

    await client.aclose()
    assert hits == 2


# --- RobotsGate ----------------------------------------------------------------

async def test_disallowed_path_is_blocked_allowed_path_is_not():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /admin\n")
        ),
        base_url="https://example.com",
    )
    context = ScanContext(url="https://example.com", client=client)
    gate = RobotsGate()
    await gate.load(context)

    await client.aclose()
    assert gate.allowed("/admin") is False
    assert gate.allowed("/pricing") is True
    assert gate.skipped_paths == ["/admin"]


async def test_missing_robots_txt_allows_everything():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, text="")),
        base_url="https://example.com",
    )
    context = ScanContext(url="https://example.com", client=client)
    gate = RobotsGate()
    await gate.load(context)

    await client.aclose()
    assert gate.allowed("/anything") is True
    assert gate.skipped_paths == []


def test_unloaded_gate_fails_open():
    # A gate nobody called load() on yet must not block every probe.
    gate = RobotsGate()
    assert gate.allowed("/anything") is True


async def test_load_is_idempotent():
    fetches = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal fetches
        fetches += 1
        return httpx.Response(200, text="User-agent: *\nDisallow: /x\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.com")
    context = ScanContext(url="https://example.com", client=client)
    gate = RobotsGate()
    await gate.load(context)
    await gate.load(context)  # second call should be a no-op

    await client.aclose()
    assert fetches == 1


# --- Budget --------------------------------------------------------------------

def test_budget_stops_after_max_requests():
    budget = Budget(max_requests=3, deadline_seconds=60)
    allowed = [budget.allow() for _ in range(5)]
    assert allowed == [True, True, True, False, False]
    assert budget.partial is True


def test_budget_under_the_cap_never_flips_partial():
    budget = Budget(max_requests=5, deadline_seconds=60)
    for _ in range(5):
        assert budget.allow() is True
    assert budget.partial is False


def test_budget_stops_after_deadline(monkeypatch):
    import agents.probe as probe

    fake_time = [1000.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: fake_time[0])

    budget = Budget(max_requests=100, deadline_seconds=5)
    assert budget.allow() is True

    fake_time[0] += 6  # past the 5s deadline
    assert budget.allow() is False
    assert budget.partial is True


# --- safe_get / safe_head / safe_options ----------------------------------------

async def _context_that_raises(exc: Exception) -> ScanContext:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.com")
    return ScanContext(url="https://example.com", client=client)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("timed out"),
        ssl.SSLError("certificate verify failed"),
    ],
)
async def test_safe_get_returns_none_instead_of_raising(exc):
    context = await _context_that_raises(exc)
    result = await safe_get(context, "https://example.com/")
    await context.client.aclose()
    assert result is None


async def test_safe_head_and_safe_options_also_swallow_errors():
    context = await _context_that_raises(httpx.ConnectError("refused"))
    head_result = await safe_head(context, "https://example.com/")
    options_result = await safe_options(context, "https://example.com/")
    await context.client.aclose()
    assert head_result is None
    assert options_result is None


async def test_safe_get_returns_response_on_success():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="hi")),
        base_url="https://example.com",
    )
    context = ScanContext(url="https://example.com", client=client)
    result = await safe_get(context, "https://example.com/")
    await client.aclose()
    assert result is not None
    assert result.status_code == 200


async def test_a_dead_probe_never_stops_the_rest_of_a_loop():
    # The whole point of safe_* -- one bad path in a list of candidates must
    # not abort the loop checking the others.
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if request.url.path == "/dead":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.com")
    context = ScanContext(url="https://example.com", client=client)

    results = []
    for path in ["/a", "/dead", "/b"]:
        results.append(await safe_get(context, f"https://example.com{path}"))

    await client.aclose()
    assert [r is None for r in results] == [False, True, False]
    assert calls["count"] == 3
