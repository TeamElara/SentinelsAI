"""The HTTP client every scan connection uses: check and connect in one step.

Checking a host and then handing its *name* to an HTTP client leaves a gap:
the client resolves the name again when it connects, and DNS is allowed to
answer differently the second time (that is how DNS rebinding works — public
address for the check, 127.0.0.1 for the connect). `make_scan_client()`
closes the gap by doing the check where the connection is made:

- `PolicyBackend` replaces the piece of httpcore that opens TCP sockets. It
  resolves the host, runs every answer through `net.policy`, and connects to
  one of those vetted IP addresses — never to the hostname. httpx still uses
  the URL's hostname for SNI and certificate checks, so TLS is verified
  against the name the user asked for, not the IP.
- `PolicyTransport` checks every request's URL (scheme, port, credentials)
  before it is sent. httpx sends each redirect hop through the transport as
  a new request, so this covers every URL in a redirect chain, not only the
  first.
- The client is built with `trust_env=False`. Otherwise httpx reads
  `HTTP_PROXY`/`HTTPS_PROXY` and connects to the proxy instead, and the
  proxy — not this code — would decide where the request ends up.
"""
from __future__ import annotations

import ssl
from typing import Any, Iterable

import httpcore
import httpx

from net.policy import (
    ALLOWED_PORTS,
    BlockedTarget,
    Resolver,
    check_ip,
    check_url,
    parse_ip_literal,
    resolve_and_check,
)


class BlockedRequest(httpx.ConnectError):
    """A request the outbound policy refused to send.

    A subclass of `httpx.ConnectError` on purpose: to an agent, a blocked
    host is one more host it couldn't connect to, and the existing probe
    helpers already turn that into a missing data point instead of a crash.
    Code that wants to tell the user *why* can catch this class and read
    `reason`.
    """

    def __init__(self, reason: str, *, request: httpx.Request | None = None) -> None:
        super().__init__(reason, request=request)
        self.reason = reason


class _BlockedConnect(httpcore.ConnectError):
    """`BlockedTarget`, in a type httpcore and httpx let pass through.

    httpx translates httpcore's exceptions into its own and would let a
    plain `BlockedTarget` escape untranslated, past every `except
    httpx.HTTPError` in the agents. `PolicyTransport` turns this back into
    a `BlockedRequest`.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PolicyBackend(httpcore.AsyncNetworkBackend):
    """Opens TCP connections only to addresses the outbound policy vetted."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._resolver = resolver
        # What actually opens the socket once an address is vetted. Only
        # tests replace it, to stand in for the public internet.
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if port not in ALLOWED_PORTS:
            raise _BlockedConnect("Only ports 80 and 443 can be scanned.")
        try:
            addresses = await resolve_and_check(host, resolver=self._resolver)
        except BlockedTarget as exc:
            raise _BlockedConnect(exc.reason) from None

        # Connect to the vetted IPs themselves. Passing `host` down instead
        # would make the OS resolve it a second time, unchecked.
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None  # resolve_and_check never returns []
        raise last_error

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> httpcore.AsyncNetworkStream:
        raise _BlockedConnect("Unix sockets can't be scanned.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PolicyTransport(httpx.AsyncBaseTransport):
    """Checks each request's URL, then sends it through `inner`."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            _, host, _ = check_url(str(request.url))
            # A literal address needs no DNS, so it can be refused here
            # with nothing sent. Hostnames are checked at connect time.
            literal = parse_ip_literal(host)
            if literal is not None:
                check_ip(literal)
        except BlockedTarget as exc:
            raise BlockedRequest(exc.reason, request=request) from None

        try:
            return await self._inner.handle_async_request(request)
        except httpx.ConnectError as exc:
            if isinstance(exc.__cause__, _BlockedConnect):
                raise BlockedRequest(exc.__cause__.reason, request=request) from None
            raise

    async def aclose(self) -> None:
        await self._inner.aclose()


def make_scan_transport(
    *,
    verify: bool | ssl.SSLContext = True,
    resolver: Resolver | None = None,
    backend_inner: httpcore.AsyncNetworkBackend | None = None,
) -> PolicyTransport:
    """An httpx transport whose connections all go through the outbound policy."""
    inner = httpx.AsyncHTTPTransport(verify=verify, trust_env=False)
    # httpx has no public way to pass a network backend to the transport it
    # builds, so the pool is swapped for one that has it. The settings
    # mirror what AsyncHTTPTransport itself passes (httpx 0.28).
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    inner._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(verify=verify, trust_env=False),
        max_connections=limits.max_connections,
        max_keepalive_connections=limits.max_keepalive_connections,
        keepalive_expiry=limits.keepalive_expiry,
        network_backend=PolicyBackend(resolver=resolver, inner=backend_inner),
    )
    return PolicyTransport(inner)


def make_scan_client(
    *,
    verify: bool | ssl.SSLContext = True,
    resolver: Resolver | None = None,
    backend_inner: httpcore.AsyncNetworkBackend | None = None,
    **client_kwargs: Any,
) -> httpx.AsyncClient:
    """Build the `httpx.AsyncClient` for connections to user-supplied hosts.

    Takes the usual client options (`timeout`, `headers`, `follow_redirects`,
    ...). `transport`, `mounts`, `proxy` and `trust_env` are refused, since
    each of them is a way around the policy.

    `resolver` and `backend_inner` exist for tests: the first fakes DNS, the
    second fakes the socket *underneath* the policy, so the checks still run.
    """
    for forbidden in ("transport", "mounts", "proxy", "trust_env"):
        if forbidden in client_kwargs:
            raise TypeError(f"make_scan_client() does not accept {forbidden!r}")
    return httpx.AsyncClient(
        transport=make_scan_transport(verify=verify, resolver=resolver, backend_inner=backend_inner),
        trust_env=False,
        **client_kwargs,
    )
