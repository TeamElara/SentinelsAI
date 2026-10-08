"""Tests that every scan connection goes through the outbound policy
(launch plan task B3): the orchestrator, fix verification, the raw TLS
handshake and the subdomain agent.

conftest's `no_real_dns` makes every name resolve to one public address;
tests here replace the resolver when a name has to point somewhere private.
"""
from __future__ import annotations

import ssl
from pathlib import Path

import httpx
import pytest

import agents.subdomain as subdomain
import agents.tls as tls
import net.policy
import orchestrator
from agents.base import ScanContext
from agents.subdomain import SubdomainAgent
from agents.tls import TLSAgent, fetch_certificate
from models import ScanReport
from net.policy import BlockedTarget
from orchestrator import run_scan, run_scan_stream
from remediation import verify as verify_module
from remediation.verify import VerifyError
from tests.conftest import FAKE_PUBLIC_IP
from tests.test_net_client import ok, self_signed, start_server

BACKEND = Path(__file__).resolve().parent.parent

INTERNAL_URLS = [
    "http://localhost:8000",
    "http://localhost",
    "localhost",
    "http://127.0.0.1",
    "https://127.0.0.1:443",
    "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1",
    "http://192.168.1.1/admin",
    "http://2130706433/",
    "http://[::ffff:127.0.0.1]/",
    "https://example.com:8443",
    "https://user:pass@example.com",
]


@pytest.fixture
def sent(monkeypatch):
    """Every request the scan client sends, with the real client swapped for
    one that records instead of connecting."""
    requests: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="hi")

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return requests


# --- orchestrator ------------------------------------------------------------

@pytest.mark.parametrize("url", INTERNAL_URLS)
async def test_scanning_an_internal_target_is_refused_before_anything_is_sent(url, sent):
    with pytest.raises(ValueError, match="Not allowed"):
        await run_scan(url)
    assert sent == []


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1", "http://10.0.0.1"])
async def test_streaming_scan_refuses_an_internal_target_too(url, sent):
    with pytest.raises(ValueError, match="Not allowed"):
        async for _ in run_scan_stream(url):
            pass
    assert sent == []


async def test_a_name_that_resolves_privately_is_refused(sent, monkeypatch):
    async def resolve(host: str) -> list[str]:
        return ["192.168.0.20"]

    monkeypatch.setattr(net.policy, "system_resolver", resolve)

    with pytest.raises(ValueError, match="Not allowed"):
        await run_scan("https://intranet.example.com")
    assert sent == []


async def test_scan_goes_through_the_policy_client(monkeypatch, temp_db):
    """`run_scan` must build its client with `make_scan_client`, not a plain
    `httpx.AsyncClient` — proven by handing it a marked client and seeing
    the agents receive that one."""
    seen: list[httpx.AsyncClient] = []
    marked = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="hi")))

    class Probe(subdomain.BaseAgent):
        name = "probe"

        async def scan(self, context: ScanContext):
            seen.append(context.client)
            return []

    monkeypatch.setattr(orchestrator, "make_scan_client", lambda **kwargs: marked)
    monkeypatch.setattr(orchestrator, "AGENTS", [Probe])

    await run_scan("https://example.com")

    assert seen == [marked]


def test_no_plain_http_client_in_code_that_reaches_user_supplied_hosts():
    """The orchestrator and the agents may only get a client from
    `make_scan_client()`. Agents share the orchestrator's; none builds one."""
    files = [BACKEND / "orchestrator.py", *sorted((BACKEND / "agents").glob("*.py"))]
    offenders = [
        f"{path.name}:{number}"
        for path in files
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if "AsyncClient(" in line or "httpx.Client(" in line
    ]
    assert offenders == []


def test_scan_route_answers_400_not_allowed_for_localhost(temp_db, monkeypatch, sent):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", "test-signing-secret")
    from fastapi.testclient import TestClient

    import main
    from auth import session
    from storage.users import sign_in

    token = session.new_token()
    sign_in(
        github_id=7, github_login="octo", avatar_url=None,
        token_hash=session.hash_token(token), expires_at=session.session_expiry(),
    )
    client = TestClient(main.app)
    client.cookies.set("sentinels_session", session.cookie_value(token, "test-signing-secret"))

    response = client.post("/scan", json={"url": "http://localhost:8000"})

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Not allowed:")
    assert sent == []


# --- fix verification --------------------------------------------------------

@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:8000/", "http://10.0.0.1/"])
async def test_verification_refuses_a_stored_scan_of_an_internal_target(url, sent):
    report = ScanReport(
        id="scan-1", url=url, scanned_at="2026-10-08T00:00:00+00:00",
        duration_ms=1, score=50, grade="F", findings=[], checklist=[],
    )

    with pytest.raises(VerifyError, match="Not allowed") as excinfo:
        await verify_module._rerun_url_agent(report, verify_module.url_agent_for("headers"))

    assert excinfo.value.status == 400
    assert sent == []


# --- raw TLS handshake -------------------------------------------------------

@pytest.fixture
def no_sockets(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"socket opened: {args}")

    monkeypatch.setattr(tls.socket, "create_connection", refuse)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5", "::1", "::ffff:127.0.0.1"])
def test_fetch_certificate_refuses_internal_hosts_without_opening_a_socket(host, no_sockets):
    with pytest.raises(BlockedTarget):
        fetch_certificate(host, 443, 1.0)


def test_fetch_certificate_refuses_a_name_that_resolves_privately(no_sockets, monkeypatch):
    monkeypatch.setattr(net.policy, "system_resolver_sync", lambda host: [FAKE_PUBLIC_IP, "10.0.0.5"])
    with pytest.raises(BlockedTarget):
        fetch_certificate("intranet.example.com", 443, 1.0)


def test_fetch_certificate_refuses_other_ports(no_sockets):
    with pytest.raises(BlockedTarget):
        fetch_certificate("example.com", 8443, 1.0)


async def _tls_server(tmp_path, monkeypatch, cert_hostname: str):
    """A local TLS server that `fetch_certificate` reaches in place of the
    public internet; returns the list of addresses it asked to connect to."""
    pem = self_signed(cert_hostname, tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(pem)
    server, port = await start_server({"/": ok()}, ssl_context=server_context)

    asked: list[tuple[str, int]] = []
    real_connect = tls.socket.create_connection

    def connect(address, timeout=None):
        asked.append(address)
        return real_connect(("127.0.0.1", port), timeout=timeout)

    monkeypatch.setattr(tls.socket, "create_connection", connect)
    real_context = ssl.create_default_context
    monkeypatch.setattr(tls.ssl, "create_default_context", lambda: real_context(cafile=pem))
    return server, asked


async def test_fetch_certificate_connects_to_the_vetted_ip_and_verifies_the_name(tmp_path, monkeypatch):
    import asyncio

    server, asked = await _tls_server(tmp_path, monkeypatch, "site.example")
    try:
        cert, version = await asyncio.to_thread(fetch_certificate, "site.example", 443, 5.0)
    finally:
        server.close()
        await server.wait_closed()

    assert asked == [(FAKE_PUBLIC_IP, 443)]  # the IP, never the hostname
    assert ("DNS", "site.example") in cert["subjectAltName"]
    assert version.startswith("TLS")


async def test_fetch_certificate_still_rejects_a_certificate_for_another_name(tmp_path, monkeypatch):
    import asyncio

    server, asked = await _tls_server(tmp_path, monkeypatch, "other.example")
    try:
        with pytest.raises(ssl.SSLError):
            await asyncio.to_thread(fetch_certificate, "site.example", 443, 5.0)
    finally:
        server.close()
        await server.wait_closed()

    assert asked == [(FAKE_PUBLIC_IP, 443)]


async def test_tls_agent_records_blocked_host_as_skipped(no_sockets, mock_site):
    client = mock_site({})
    result = await TLSAgent().run(ScanContext(url="https://127.0.0.1", client=client))
    await client.aclose()

    assert result.findings == []
    assert result.error is None
    assert result.coverage_status == "skipped"
    assert len(result.coverage) == len(TLSAgent.checks)
    assert all(c.status == "skipped" and c.reason for c in result.coverage)


# --- subdomain agent ---------------------------------------------------------

async def test_internally_resolving_subdomain_is_skipped_not_contacted(monkeypatch):
    """`intranet.example.com` points at 10.0.0.5. It must get no HTTP request
    and no TLS handshake, and the report must say it was skipped — while the
    public host next to it is still checked."""
    records = {
        "intranet.example.com": ("A", "10.0.0.5"),
        "www.example.com": ("A", "93.184.216.40"),
    }
    contacted: list[str] = []
    handshakes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        contacted.append(request.url.host)
        return httpx.Response(200, headers={"strict-transport-security": "max-age=1", "content-security-policy": "x"})

    def fake_handshake(hostname, port, timeout):
        handshakes.append(hostname)
        raise OSError("no handshake in tests")

    async def ct(client, domain):
        return ["intranet.example.com"]

    async def resolve(host: str) -> list[str]:
        return ["10.0.0.5"] if host == "intranet.example.com" else [FAKE_PUBLIC_IP]

    monkeypatch.setattr(subdomain, "_resolve", lambda host: records.get(host))
    monkeypatch.setattr(subdomain, "_target_resolves", lambda host: True)
    monkeypatch.setattr(subdomain, "_query_ct_logs", ct)
    monkeypatch.setattr(subdomain, "fetch_certificate", fake_handshake)
    monkeypatch.setattr(net.policy, "system_resolver", resolve)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    context = ScanContext(url="https://example.com", client=client)
    result = await SubdomainAgent().run(context)
    await client.aclose()

    assert result.error is None
    assert "intranet.example.com" not in contacted
    assert "intranet.example.com" not in handshakes
    assert "www.example.com" in contacted

    skipped = [c for c in result.coverage if c.status == "skipped"]
    assert len(skipped) == 1
    assert "intranet.example.com" in skipped[0].check
    assert "www.example.com" not in skipped[0].check
    assert not [f for f in result.findings if f.id == "subdomain-internal-skipped"]
    # Skipped is "not checked", so nothing is claimed about the host itself.
    assert not [f for f in result.findings if f.affected_url and "intranet" in f.affected_url]
    # It still appears in the inventory.
    assert "intranet.example.com" in [entry.host for entry in context.shared["subdomains"]]


async def test_no_skipped_finding_when_every_subdomain_is_public(monkeypatch, mock_site):
    async def ct(client, domain):
        return []

    monkeypatch.setattr(subdomain, "_resolve", lambda host: ("A", "93.184.216.40") if host == "www.example.com" else None)
    monkeypatch.setattr(subdomain, "_target_resolves", lambda host: True)
    monkeypatch.setattr(subdomain, "_query_ct_logs", ct)
    monkeypatch.setattr(subdomain, "fetch_certificate", lambda *a, **k: (_ for _ in ()).throw(OSError("none")))

    client = mock_site({"/": (200, {}, "hi")})
    result = await SubdomainAgent().run(ScanContext(url="https://example.com", client=client))
    await client.aclose()

    assert not [f for f in result.findings if f.id == "subdomain-internal-skipped"]
