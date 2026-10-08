"""Tests for the pinned scan client (launch plan task B2).

No real DNS and nothing leaves the machine: names resolve through a fake
resolver, and `Loopback` stands in for the public internet by sending every
connection the policy approved to a small server on 127.0.0.1 — while
recording which address the client *asked* to connect to.
"""
from __future__ import annotations

import asyncio
import datetime
import ssl

import httpcore
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agents.base import ScanContext
from agents.probe import safe_get
from net.client import BlockedRequest, make_scan_client
from net.policy import check_target

PUBLIC = "93.184.216.34"
PUBLIC_2 = "93.184.216.35"


def resolver_for(answers: dict[str, list[str]]):
    async def resolve(host: str) -> list[str]:
        return answers.get(host, [])

    return resolve


class Loopback(httpcore.AsyncNetworkBackend):
    """Sends approved connections to a local test server instead of the internet."""

    def __init__(self, port: int, *, refuse: tuple[str, ...] = ()) -> None:
        self.port = port
        self.refuse = refuse
        self.calls: list[tuple[str, int]] = []
        self._real = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append((host, port))
        if host in self.refuse:
            raise httpcore.ConnectError("refused")
        return await self._real.connect_tcp("127.0.0.1", self.port, timeout=timeout)

    async def sleep(self, seconds: float) -> None:
        await self._real.sleep(seconds)


async def start_server(routes: dict[str, bytes], ssl_context: ssl.SSLContext | None = None):
    """Serve fixed raw HTTP responses by path; anything else is a 404."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            path = head.split(b" ", 2)[1].decode()
            writer.write(routes.get(path, b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"))
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ssl_context)
    return server, server.sockets[0].getsockname()[1]


def ok(body: bytes = b"ok") -> bytes:
    return b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body)


def redirect(location: str) -> bytes:
    return (
        b"HTTP/1.1 302 Found\r\nLocation: " + location.encode() + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )


def self_signed(hostname: str, directory) -> str:
    """Write a self-signed cert + key for `hostname`; return the PEM path."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = directory / f"{hostname}.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        + cert.public_bytes(serialization.Encoding.PEM)
    )
    return str(path)


@pytest.fixture
async def site():
    """A plain-HTTP test server plus a client wired to reach it as site.example."""
    servers = []

    async def build(routes, answers=None, **kwargs):
        server, port = await start_server(routes)
        servers.append(server)
        backend = Loopback(port, refuse=kwargs.pop("refuse", ()))
        answers = {"site.example": [PUBLIC], **(answers or {})}
        client = make_scan_client(resolver=resolver_for(answers), backend_inner=backend, timeout=5.0, **kwargs)
        return client, backend

    yield build
    for server in servers:
        server.close()
        await server.wait_closed()


# --- pinning -----------------------------------------------------------------

async def test_connects_to_the_vetted_ip_not_the_hostname(site):
    client, backend = await site({"/": ok()})
    async with client:
        response = await client.get("http://site.example/")

    assert response.status_code == 200
    assert backend.calls == [(PUBLIC, 80)]


async def test_public_then_private_answer_is_blocked_at_connect(site):
    """DNS rebinding: the up-front check sees a public address, the connect
    sees a private one. The connect must do its own check and refuse."""
    answers = iter([[PUBLIC], ["127.0.0.1"]])

    async def rebinding(host: str) -> list[str]:
        return next(answers)

    server, port = await start_server({"/": ok()})
    backend = Loopback(port)
    try:
        await check_target("http://rebind.example/", resolver=rebinding)  # passes
        async with make_scan_client(resolver=rebinding, backend_inner=backend) as client:
            with pytest.raises(BlockedRequest):
                await client.get("http://rebind.example/")
    finally:
        server.close()
        await server.wait_closed()

    assert backend.calls == []


async def test_mixed_public_and_private_answers_are_blocked(site):
    client, backend = await site({"/": ok()}, answers={"mixed.example": [PUBLIC, "10.0.0.1"]})
    async with client:
        with pytest.raises(BlockedRequest):
            await client.get("http://mixed.example/")
    assert backend.calls == []


async def test_falls_through_to_the_next_vetted_address(site):
    client, backend = await site({"/": ok()}, answers={"site.example": [PUBLIC, PUBLIC_2]}, refuse=(PUBLIC,))
    async with client:
        response = await client.get("http://site.example/")

    assert response.status_code == 200
    assert backend.calls == [(PUBLIC, 80), (PUBLIC_2, 80)]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://2130706433/",
        "http://[::ffff:127.0.0.1]/",
        "http://site.example:8000/",
        "https://user:pass@site.example/",
    ],
)
async def test_blocked_urls_never_reach_the_network(site, url):
    client, backend = await site({"/": ok()})
    async with client:
        with pytest.raises(BlockedRequest) as excinfo:
            await client.get(url)

    assert backend.calls == []
    assert excinfo.value.reason


# --- redirects ---------------------------------------------------------------

@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://site.example/file",
        "http://internal.example/",  # resolves to a private address
        "http://site.example:8080/",
        "https://user:pass@site.example/",
    ],
)
async def test_redirect_chain_ending_somewhere_blocked_is_refused(site, location):
    routes = {"/": redirect("/hop"), "/hop": redirect(location)}
    client, backend = await site(routes, answers={"internal.example": ["192.168.1.10"]}, follow_redirects=True)
    async with client:
        with pytest.raises(BlockedRequest):
            await client.get("http://site.example/")

    # Both hops on the public site happened; the blocked one never connected.
    assert backend.calls == [(PUBLIC, 80), (PUBLIC, 80)]


async def test_redirect_to_another_public_host_is_followed(site):
    routes = {"/": redirect("http://other.example/landed"), "/landed": ok(b"landed")}
    client, backend = await site(routes, answers={"other.example": [PUBLIC_2]}, follow_redirects=True)
    async with client:
        response = await client.get("http://site.example/")

    assert response.text == "landed"
    assert backend.calls == [(PUBLIC, 80), (PUBLIC_2, 80)]


async def test_unfollowed_redirect_is_returned_untouched(site):
    client, _ = await site({"/": redirect("http://127.0.0.1/")})
    async with client:
        response = await client.get("http://site.example/")
    assert response.status_code == 302


# --- proxies -----------------------------------------------------------------

async def test_proxy_environment_variables_are_ignored(site, monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://proxy.invalid:3128")

    client, backend = await site({"/": ok()})
    async with client:
        with pytest.raises(BlockedRequest):
            await client.get("http://127.0.0.1/")
        response = await client.get("http://site.example/")

    assert response.status_code == 200
    # Straight to the vetted address; the proxy was never contacted.
    assert backend.calls == [(PUBLIC, 80)]
    assert client._mounts == {}


@pytest.mark.parametrize("option", ["transport", "mounts", "proxy", "trust_env"])
def test_make_scan_client_refuses_options_that_bypass_the_policy(option):
    with pytest.raises(TypeError):
        make_scan_client(**{option: None})


# --- TLS ---------------------------------------------------------------------

async def _tls_site(tmp_path, cert_hostname: str):
    pem = self_signed(cert_hostname, tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(pem)
    server, port = await start_server({"/": ok(b"secure")}, ssl_context=server_context)
    backend = Loopback(port)
    client = make_scan_client(
        verify=ssl.create_default_context(cafile=pem),
        resolver=resolver_for({"site.example": [PUBLIC]}),
        backend_inner=backend,
        timeout=5.0,
    )
    return server, backend, client


async def test_certificate_is_verified_against_the_hostname(tmp_path):
    """The socket goes to an IP, but SNI and verification still use the name."""
    server, backend, client = await _tls_site(tmp_path, "site.example")
    try:
        async with client:
            response = await client.get("https://site.example/")
    finally:
        server.close()
        await server.wait_closed()

    assert response.text == "secure"
    assert backend.calls == [(PUBLIC, 443)]


async def test_certificate_for_another_hostname_still_fails(tmp_path):
    server, backend, client = await _tls_site(tmp_path, "other.example")
    try:
        async with client:
            with pytest.raises(httpx.ConnectError) as excinfo:
                await client.get("https://site.example/")
    finally:
        server.close()
        await server.wait_closed()

    # The TCP connect to the vetted IP happened; it was TLS that said no.
    assert backend.calls == [(PUBLIC, 443)]
    assert not isinstance(excinfo.value, BlockedRequest)
    assert "CERTIFICATE_VERIFY_FAILED" in str(excinfo.value)


# --- how agents see a block --------------------------------------------------

async def test_probe_helpers_treat_a_blocked_request_as_a_missing_data_point(site):
    client, _ = await site({"/": ok()})
    async with client:
        context = ScanContext(url="http://site.example", client=client)
        assert await safe_get(context, "http://127.0.0.1/") is None
