"""Tests for "work that actually stops" (launch plan task B4): the scan
deadline, cleanup when a stream's reader goes away, per-call timeouts, the
response-size cap and the concurrent-scan limits.
"""
from __future__ import annotations

import asyncio
import gzip
import zlib

import dns.exception
import httpx
import pytest

import net.policy
import orchestrator
import repo_orchestrator
import scan_limits
from agents.base import BaseAgent, ScanContext
from models import AgentResult
from net.client import MAX_BODY_BYTES, make_scan_client
from net.policy import DNS_LIFETIME_SECONDS, system_resolver, system_resolver_sync
from orchestrator import run_scan, run_scan_stream
from scan_limits import ScanBusy, ScanSlots, run_with_deadline
from tests.test_net_client import Loopback, ok, start_server

PUBLIC = "93.184.216.34"


class Agents:
    """Fake agents: `fast` returns at once, `stalled` waits forever and
    records whether it was actually cancelled (not merely abandoned)."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.cancelled: list[str] = []

    async def fast(self, name: str) -> AgentResult:
        self.started.append(name)
        return AgentResult(agent=name, findings=[], duration_ms=1)

    async def stalled(self, name: str) -> AgentResult:
        self.started.append(name)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise
        raise AssertionError("unreachable")


def _deadline(seconds: float) -> float:
    return asyncio.get_running_loop().time() + seconds


def _other_tasks() -> set[asyncio.Task]:
    return {t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()}


# --- run_with_deadline -------------------------------------------------------

async def test_stalled_agent_is_cancelled_at_the_deadline_and_reported_failed():
    agents = Agents()
    runs = [("slow-a", agents.stalled("slow-a")), ("quick", agents.fast("quick")), ("slow-b", agents.stalled("slow-b"))]

    results = [result async for result in run_with_deadline(runs, _deadline(0.1))]

    assert [r.agent for r in results] == ["quick", "slow-a", "slow-b"]
    assert results[0].error is None
    for timed_out in results[1:]:
        assert timed_out.findings == []
        assert "TimeoutError" in timed_out.error and "deadline" in timed_out.error
    assert sorted(agents.cancelled) == ["slow-a", "slow-b"]
    assert _other_tasks() == set()


async def test_results_arrive_as_each_agent_finishes():
    async def after(name: str, delay: float) -> AgentResult:
        await asyncio.sleep(delay)
        return AgentResult(agent=name, findings=[], duration_ms=1)

    runs = [("third", after("third", 0.09)), ("first", after("first", 0.01)), ("second", after("second", 0.05))]
    results = [result.agent async for result in run_with_deadline(runs, _deadline(5))]
    assert results == ["first", "second", "third"]


async def test_closing_the_generator_early_leaves_no_running_tasks():
    agents = Agents()
    runs = [("quick", agents.fast("quick")), ("slow-a", agents.stalled("slow-a")), ("slow-b", agents.stalled("slow-b"))]

    generator = run_with_deadline(runs, _deadline(60))
    first = await generator.__anext__()
    await generator.aclose()

    assert first.agent == "quick"
    assert sorted(agents.cancelled) == ["slow-a", "slow-b"]
    assert _other_tasks() == set()


async def test_cancelling_the_consumer_cancels_the_agents():
    """What happens when the server drops a request: the task consuming the
    generator is cancelled while it waits."""
    agents = Agents()

    async def consume():
        runs = [("slow-a", agents.stalled("slow-a")), ("slow-b", agents.stalled("slow-b"))]
        async for _ in run_with_deadline(runs, _deadline(60)):
            pass

    consumer = asyncio.ensure_future(consume())
    while len(agents.started) < 2:
        await asyncio.sleep(0)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert sorted(agents.cancelled) == ["slow-a", "slow-b"]
    assert _other_tasks() == set()


# --- the orchestrator --------------------------------------------------------

class _Quick(BaseAgent):
    name = "quick"

    async def scan(self, context: ScanContext):
        return []


def _stalling_agent(cancelled: list[str]):
    class _Stalled(BaseAgent):
        name = "stalled"

        async def scan(self, context: ScanContext):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(self.name)
                raise

    return _Stalled


@pytest.fixture
def scan_client(monkeypatch):
    """Give the orchestrator a client that answers 200 and can be inspected."""
    made: list[httpx.AsyncClient] = []

    def make(**kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="hi")))
        made.append(client)
        return client

    monkeypatch.setattr(orchestrator, "make_scan_client", make)
    return made


@pytest.fixture
def fresh_slots(monkeypatch):
    slots = ScanSlots()
    monkeypatch.setattr(orchestrator, "scan_slots", slots)
    monkeypatch.setattr(repo_orchestrator, "scan_slots", slots)
    return slots


async def test_scan_past_its_deadline_finishes_with_the_stalled_agent_failed(
    monkeypatch, temp_db, scan_client, fresh_slots
):
    cancelled: list[str] = []
    monkeypatch.setattr(orchestrator, "AGENTS", [_stalling_agent(cancelled), _Quick])
    monkeypatch.setattr(orchestrator, "SCAN_DEADLINE_SECONDS", 0.1)

    report = await run_scan("https://example.com", user_id=None)

    by_agent = {result.agent: result for result in report.agents}
    assert [result.agent for result in report.agents] == ["stalled", "quick"]  # registry order
    assert by_agent["quick"].error is None
    assert "TimeoutError" in by_agent["stalled"].error
    assert cancelled == ["stalled"]
    assert scan_client[0].is_closed
    assert fresh_slots.running == 0
    assert _other_tasks() == set()


async def test_disconnected_stream_leaves_no_tasks_and_closes_the_client(
    monkeypatch, temp_db, scan_client, fresh_slots
):
    cancelled: list[str] = []
    monkeypatch.setattr(orchestrator, "AGENTS", [_Quick, _stalling_agent(cancelled)])

    stream = run_scan_stream("https://example.com", user_id=7)
    event, first = await stream.__anext__()
    assert (event, first.agent) == ("agent", "quick")
    assert fresh_slots.running == 1

    await stream.aclose()  # the browser went away

    assert cancelled == ["stalled"]
    assert scan_client[0].is_closed
    assert fresh_slots.running == 0
    assert _other_tasks() == set()


async def test_disconnected_stream_closes_its_sockets(monkeypatch, temp_db, fresh_slots):
    """Same disconnect, with a real client and a server that accepts the
    request and never answers: the server must see the connection close."""
    closed = asyncio.Event()
    got_request = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        if b"GET /hang" not in head:
            writer.write(ok())
            await writer.drain()
            writer.close()
            return
        got_request.set()
        await reader.read()  # returns only when the client closes its end
        closed.set()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    class _Hangs(BaseAgent):
        name = "hangs"

        async def scan(self, context: ScanContext):
            await context.client.get("http://example.com/hang", timeout=30.0)
            return []

    monkeypatch.setattr(
        orchestrator, "make_scan_client", lambda **kwargs: make_scan_client(backend_inner=Loopback(port), **kwargs)
    )
    monkeypatch.setattr(orchestrator, "AGENTS", [_Hangs])

    try:
        stream = run_scan_stream("http://example.com", user_id=None)
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.wait_for(got_request.wait(), 5)

        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()

        await asyncio.wait_for(closed.wait(), 5)
    finally:
        server.close()
        await server.wait_closed()

    assert fresh_slots.running == 0
    assert _other_tasks() == set()


async def test_stalled_host_times_out():
    """A host that accepts the connection and then says nothing."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with make_scan_client(backend_inner=Loopback(port), timeout=0.2) as client:
            started = asyncio.get_running_loop().time()
            with pytest.raises(httpx.ReadTimeout):
                await client.get("http://example.com/")
            assert asyncio.get_running_loop().time() - started < 2
    finally:
        server.close()
        await server.wait_closed()


# --- concurrent-scan limits --------------------------------------------------

def test_one_scan_per_user():
    slots = ScanSlots(total=3, per_user=1)
    with slots.hold(1):
        with pytest.raises(ScanBusy, match="already have a scan running"):
            with slots.hold(1):
                pass
        with slots.hold(2):
            assert slots.running == 2
    assert slots.running == 0
    with slots.hold(1):  # free again once the first finished
        pass


def test_three_scans_overall():
    slots = ScanSlots(total=3, per_user=1)
    with slots.hold(1), slots.hold(2), slots.hold(None):
        with pytest.raises(ScanBusy, match="as many scans as it can"):
            with slots.hold(4):
                pass
    assert slots.running == 0


def test_slot_is_released_when_the_scan_fails():
    slots = ScanSlots()
    with pytest.raises(RuntimeError):
        with slots.hold(1):
            raise RuntimeError("scan blew up")
    assert slots.running == 0
    with slots.hold(1):
        pass


async def test_second_scan_by_the_same_user_is_refused(monkeypatch, temp_db, scan_client, fresh_slots):
    monkeypatch.setattr(orchestrator, "AGENTS", [_stalling_agent([])])

    first = run_scan_stream("https://example.com", user_id=7)
    waiting = asyncio.ensure_future(first.__anext__())
    while fresh_slots.running == 0:
        await asyncio.sleep(0)

    with pytest.raises(ValueError, match="already have a scan running"):
        await run_scan("https://example.com", user_id=7)

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await first.aclose()
    assert fresh_slots.running == 0


async def test_refused_target_does_not_take_a_slot(scan_client, fresh_slots):
    with pytest.raises(ValueError, match="Not allowed"):
        await run_scan("http://localhost:8000", user_id=7)
    assert fresh_slots.running == 0


# --- response size cap -------------------------------------------------------

def _response(body: bytes, *headers: bytes) -> bytes:
    lines = [b"HTTP/1.1 200 OK", b"Content-Length: %d" % len(body), b"X-Frame-Options: DENY", b"Connection: close", *headers]
    return b"\r\n".join(lines) + b"\r\n\r\n" + body


async def _fetch(path_body: bytes, *headers: bytes) -> tuple[httpx.Response, list[bytes]]:
    heads: list[bytes] = []
    server, port = await start_server({"/": _response(path_body, *headers)}, heads=heads)
    try:
        async with make_scan_client(backend_inner=Loopback(port), timeout=10.0) as client:
            response = await client.get("http://example.com/")
    finally:
        server.close()
        await server.wait_closed()
    return response, heads


async def test_small_body_is_untouched():
    response, heads = await _fetch(b"<html>hello</html>")
    assert response.text == "<html>hello</html>"
    assert response.headers["x-frame-options"] == "DENY"
    assert b"accept-encoding: gzip, deflate" in heads[0].lower()


async def test_large_body_is_cut_off_at_the_cap():
    response, _ = await _fetch(b"a" * (MAX_BODY_BYTES + 500_000))
    assert len(response.content) == MAX_BODY_BYTES
    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"


async def test_gzip_body_is_decoded():
    response, _ = await _fetch(gzip.compress(b"<html>zipped</html>"), b"Content-Encoding: gzip")
    assert response.text == "<html>zipped</html>"
    assert "content-encoding" not in response.headers


async def test_gzip_bomb_expands_only_to_the_cap():
    bomb = gzip.compress(b"\0" * (60 * 1024 * 1024))
    assert len(bomb) < 100_000  # tiny on the wire

    response, _ = await _fetch(bomb, b"Content-Encoding: gzip")

    assert len(response.content) == MAX_BODY_BYTES


@pytest.mark.parametrize(
    "encode",
    [zlib.compress, lambda data: zlib.compress(data)[2:-4]],  # zlib-wrapped, and raw deflate
    ids=["zlib", "raw"],
)
async def test_deflate_body_is_decoded(encode):
    response, _ = await _fetch(encode(b"<html>deflated</html>" * 50), b"Content-Encoding: deflate")
    assert response.content == b"<html>deflated</html>" * 50


async def test_encoding_we_did_not_ask_for_yields_headers_but_no_body():
    response, _ = await _fetch(b"\x1b\x03\x00pretend-brotli", b"Content-Encoding: br")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["x-frame-options"] == "DENY"


async def test_corrupt_gzip_does_not_raise():
    response, _ = await _fetch(b"this is not gzip", b"Content-Encoding: gzip")
    assert response.content == b""


# --- DNS lookups are bounded -------------------------------------------------

class _Record:
    def __init__(self, address: str) -> None:
        self.address = address


def _fake_resolver_class(answers: dict[str, object], seen: list):
    class FakeResolver:
        def __init__(self, *args, **kwargs) -> None:
            seen.append(self)

        def _answer(self, record_type: str):
            answer = answers[record_type]
            if isinstance(answer, Exception):
                raise answer
            return [_Record(address) for address in answer]

    class Async(FakeResolver):
        async def resolve(self, host: str, record_type: str):
            return self._answer(record_type)

    class Sync(FakeResolver):
        def resolve(self, host: str, record_type: str):
            return self._answer(record_type)

    return Async, Sync


async def test_system_resolver_returns_a_and_aaaa_with_a_lifetime(monkeypatch):
    seen: list = []
    Async, Sync = _fake_resolver_class({"A": [PUBLIC, PUBLIC], "AAAA": ["2606:4700::1111"]}, seen)
    monkeypatch.setattr(net.policy.dns.asyncresolver, "Resolver", Async)
    monkeypatch.setattr(net.policy.dns.resolver, "Resolver", Sync)

    assert await system_resolver("example.com") == [PUBLIC, "2606:4700::1111"]
    assert system_resolver_sync("example.com") == [PUBLIC, "2606:4700::1111"]
    assert [resolver.lifetime for resolver in seen] == [DNS_LIFETIME_SECONDS, DNS_LIFETIME_SECONDS]


async def test_system_resolver_treats_a_failed_record_type_as_no_addresses(monkeypatch):
    seen: list = []
    answers = {"A": [PUBLIC], "AAAA": dns.exception.Timeout()}
    Async, Sync = _fake_resolver_class(answers, seen)
    monkeypatch.setattr(net.policy.dns.asyncresolver, "Resolver", Async)
    monkeypatch.setattr(net.policy.dns.resolver, "Resolver", Sync)

    assert await system_resolver("example.com") == [PUBLIC]
    assert system_resolver_sync("example.com") == [PUBLIC]

    answers["A"] = dns.exception.Timeout()
    assert await system_resolver("example.com") == []
    assert system_resolver_sync("example.com") == []


def test_subdomain_dns_lookups_have_a_lifetime():
    import agents.subdomain as subdomain

    assert subdomain._make_resolver().lifetime == DNS_LIFETIME_SECONDS
