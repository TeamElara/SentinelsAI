"""The outbound policy: which URLs and addresses a scan may connect to.

A scanner fetches whatever host the user names, and the server it runs on
can reach things the user can't: `localhost`, the private network, the cloud
metadata service at 169.254.169.254. Without a check, "scan this URL" is a
way to make our server fetch those on someone's behalf (SSRF). This module
is the one place that decides what is allowed:

- `check_url` — scheme, port and credentials, before any DNS happens.
- `check_ip` — is one address public? Includes the IPv6 forms that carry a
  private IPv4 address inside them, which `ipaddress.is_global` alone lets
  through.
- `resolve_and_check` — resolve a host's A *and* AAAA records and reject it
  if *any* answer is not public. Returns the vetted addresses so the caller
  can connect to exactly those (that pinning is `net` task B2; without it a
  second DNS lookup at connect time could answer differently).

Everything raises `BlockedTarget` with a reason that is safe to show the
user. Nothing here opens a connection to the target.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Awaitable, Callable
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http": 80, "https": 443}
ALLOWED_PORTS = frozenset(ALLOWED_SCHEMES.values())

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Given a hostname, return every address it resolves to (A and AAAA), as
# strings. Injectable so tests never touch real DNS.
Resolver = Callable[[str], Awaitable[list[str]]]

# IPv6 ranges whose addresses are a wrapper around an IPv4 address in the
# low 32 bits. `is_global` judges the wrapper, not what's inside, so
# `64:ff9b::7f00:1` (NAT64 for 127.0.0.1) and `::127.0.0.1` both pass it.
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL_USE = ipaddress.IPv6Network("64:ff9b:1::/48")
_IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")


class BlockedTarget(Exception):
    """A URL, host or address the outbound policy refuses to connect to.

    `reason` is written for the person who asked for the scan — it names
    what was refused, never anything about our own network.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_url(url: str) -> tuple[str, str, int]:
    """Validate a URL's shape and return `(scheme, host, port)`.

    Only `http`/`https` on ports 80/443, and no `user:pass@` part — a URL
    with credentials is either a mistake or an attempt to confuse whoever
    reads the host out of it. The host comes back lowercased, without a
    trailing dot, and with a numeric IPv4 form (`2130706433`, `0177.0.0.1`)
    rewritten as dotted decimal. No DNS happens here.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise BlockedTarget("That URL could not be parsed.") from None

    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise BlockedTarget("Only http:// and https:// URLs can be scanned.")
    if parts.username is not None or parts.password is not None:
        raise BlockedTarget("URLs containing a username or password can't be scanned.")
    if not parts.hostname:
        raise BlockedTarget("That URL has no host.")

    if port is None:
        port = ALLOWED_SCHEMES[scheme]
    if port not in ALLOWED_PORTS:
        raise BlockedTarget("Only ports 80 and 443 can be scanned.")

    return scheme, normalize_host(parts.hostname), port


def normalize_host(host: str) -> str:
    """Lowercase a host, drop a trailing dot, and canonicalize IP literals.

    The C resolver accepts IPv4 written as one decimal number
    (`2130706433`), in octal (`0177.0.0.1`), in hex (`0x7f.0.0.1`) or with
    fewer than four parts (`127.1`) — all of those are 127.0.0.1. Rewriting
    them here means the rest of the policy only ever sees one spelling.
    """
    host = host.strip().lower().rstrip(".")
    ip = parse_ip_literal(host)
    return str(ip) if ip is not None else host


def parse_ip_literal(host: str) -> IPAddress | None:
    """Return the address `host` spells, or None if it's a hostname."""
    candidate = host.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    # `inet_aton` is the same parser `getaddrinfo` uses for numeric hosts,
    # so anything it accepts is something a connect would treat as an IP.
    # A real hostname can't parse here: its last label is never all-numeric.
    try:
        return ipaddress.IPv4Address(socket.inet_aton(candidate))
    except (OSError, ValueError):
        return None


def embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """Every IPv4 address carried inside an IPv6 transition address.

    Traffic to these addresses ends up at the IPv4 address inside (through
    the host's own stack, a NAT64 gateway, or a 6to4/Teredo relay), so the
    inner address has to pass the policy as well.
    """
    found: list[ipaddress.IPv4Address] = []
    low32 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)

    if ip.ipv4_mapped is not None:  # ::ffff:a.b.c.d
        found.append(ip.ipv4_mapped)
    if ip in _IPV4_COMPATIBLE:  # ::a.b.c.d (deprecated, still parsed)
        found.append(low32)
    if ip in _NAT64_WELL_KNOWN:  # 64:ff9b::a.b.c.d
        found.append(low32)
    if ip in _NAT64_LOCAL_USE:
        # RFC 6052 puts the IPv4 address of a /48 prefix around a reserved
        # byte (bits 48-63 and 72-87); operators also commonly use a /96
        # inside the range, which puts it in the low 32 bits. Check both.
        packed = ip.packed
        found.append(ipaddress.IPv4Address(packed[6:8] + packed[9:11]))
        found.append(low32)
    if ip.sixtofour is not None:  # 2002:AABB:CCDD::/48
        found.append(ip.sixtofour)
    if ip.teredo is not None:  # 2001:0::/32 — (server, client)
        found.extend(ip.teredo)
    return found


def check_ip(ip: IPAddress | str) -> IPAddress:
    """Return `ip` if a scan may connect to it; raise `BlockedTarget` if not.

    "May connect" means globally routable unicast: not loopback, private,
    link-local (which covers cloud metadata), carrier-grade NAT, reserved,
    unspecified or multicast — and, for IPv6 transition addresses, the same
    for the IPv4 address inside.
    """
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip.split("%", 1)[0])
        except ValueError:
            raise BlockedTarget("That address could not be parsed.") from None

    if not ip.is_global or ip.is_multicast:
        raise BlockedTarget(_NOT_PUBLIC)
    if isinstance(ip, ipaddress.IPv6Address):
        for inner in embedded_ipv4(ip):
            if not inner.is_global or inner.is_multicast:
                raise BlockedTarget(_NOT_PUBLIC)
    return ip


_NOT_PUBLIC = (
    "That host points at a private, local or reserved address, "
    "which Sentinels is not allowed to scan."
)


async def system_resolver(host: str) -> list[str]:
    """Resolve `host` to all of its A and AAAA addresses via the OS resolver."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    # Order-preserving dedupe: getaddrinfo can repeat an address per socket type.
    return list(dict.fromkeys(info[4][0] for info in infos))


async def resolve_and_check(host: str, *, resolver: Resolver = system_resolver) -> list[str]:
    """Resolve `host` and return its addresses, all of them vetted.

    The host is rejected if *any* answer is not public — not just the first,
    and not "at least one is fine". A name that answers with both a public
    and a private address is exactly what a DNS-rebinding setup looks like,
    and which answer a connect would pick is up to the OS, not us.
    """
    host = normalize_host(host)
    if not host:
        raise BlockedTarget("That URL has no host.")

    literal = parse_ip_literal(host)
    if literal is not None:
        return [str(check_ip(literal))]

    # Refused by name so the answer doesn't depend on how this machine's
    # resolver happens to treat them.
    if host == "localhost" or host.endswith(".localhost"):
        raise BlockedTarget(_NOT_PUBLIC)

    addresses = await resolver(host)
    if not addresses:
        raise BlockedTarget("That host could not be resolved.")
    return [str(check_ip(address)) for address in addresses]


async def check_target(url: str, *, resolver: Resolver = system_resolver) -> tuple[str, str, int, list[str]]:
    """Full check for one URL: shape, then every address the host resolves to.

    Returns `(scheme, host, port, vetted_addresses)`.
    """
    scheme, host, port = check_url(url)
    addresses = await resolve_and_check(host, resolver=resolver)
    return scheme, host, port, addresses
