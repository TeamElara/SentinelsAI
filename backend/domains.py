"""Proving that a person controls a website before Sentinels scans it.

Launch Plan gate 6: the permission checkbox records a claim; this is the proof
that has to exist before sign-ups open to people nobody on the team knows.

A person adds a host and gets a random token. They prove control in one of
two ways:

* **DNS**: a TXT record at `_sentinels.<host>` reading
  `sentinels-verification=<token>`. Only whoever runs the domain's DNS can
  publish it, so it covers the host and every name under it.
* **File**: `https://<host>/.well-known/sentinels-verification.txt` containing
  the token, served without a redirect. That proves control of that one
  host's web server, so it covers that host only.

`SENTINELS_REQUIRE_DOMAIN_VERIFICATION` decides whether a website scan needs
a verified host. It is off unless set, because the invite-only beta relies on
the invite list and the checkbox; turn it on before inviting strangers.

The file check reaches a host the person typed, so it goes through the same
outbound policy as a scan (`make_scan_client`): public addresses only, ports
80/443, every connection pinned to a checked IP. The DNS check asks public
resolvers about a name and connects to nothing the person chose.
"""
from __future__ import annotations

import os
import re
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx

from net.client import BlockedRequest, make_scan_client
from net.policy import BlockedTarget, _dns_resolver, check_url, parse_ip_literal

DNS_LABEL = "_sentinels"
TOKEN_PREFIX = "sentinels-verification="
FILE_PATH = "/.well-known/sentinels-verification.txt"

MAX_CLAIMS_PER_USER = 20
CHECK_COOLDOWN_SECONDS = 10
FILE_TIMEOUT_SECONDS = 10.0
FILE_READ_LIMIT = 4096

_TRUE = {"1", "true", "yes", "on"}
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

# Given a DNS name, return its TXT records as strings. Injectable so tests
# never touch real DNS.
TxtLookup = Callable[[str], Awaitable[list[str]]]
ClientFactory = Callable[..., httpx.AsyncClient]


def required() -> bool:
    """Whether a website scan needs a verified host on this deployment."""
    return os.environ.get("SENTINELS_REQUIRE_DOMAIN_VERIFICATION", "").strip().lower() in _TRUE


def new_token() -> str:
    return secrets.token_urlsafe(24)


def host_of(raw: str) -> str:
    """The host in what someone typed: `example.com`, `https://Example.com/x`.

    Lowercased and without a trailing dot, the way the outbound policy writes
    it, so a claim and a later scan of the same site compare equal. Raises
    ValueError with a message for the person on anything that isn't one.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Enter a domain name, such as example.com.")
    if not _SCHEME_RE.match(raw):
        raw = f"https://{raw}"
    try:
        _, host, _ = check_url(raw)
    except BlockedTarget as exc:
        raise ValueError(exc.reason) from None
    return host


def claimable_host(raw: str) -> str:
    """`host_of`, refusing what can't be verified: IP addresses (no DNS name
    to put a record under) and single names such as `localhost`."""
    host = host_of(raw)
    if parse_ip_literal(host) is not None:
        raise ValueError("Verify a domain name. An IP address can't be verified.")
    if "." not in host:
        raise ValueError("Enter a full domain name, such as example.com.")
    if urlsplit(f"https://{host}").hostname != host:
        raise ValueError("That isn't a valid domain name.")
    return host


def covers(claim_host: str, method: str | None, host: str) -> bool:
    """Whether a verified claim on `claim_host` lets its owner scan `host`."""
    if method is None:
        return False
    if host == claim_host:
        return True
    return method == "dns" and host.endswith("." + claim_host)


def dns_name(host: str) -> str:
    return f"{DNS_LABEL}.{host}"


def dns_value(token: str) -> str:
    return f"{TOKEN_PREFIX}{token}"


def file_url(host: str) -> str:
    return f"https://{host}{FILE_PATH}"


async def system_txt_lookup(name: str) -> list[str]:
    """Every TXT record at `name`, each one's strings joined, as text."""
    resolver = _dns_resolver(dns.asyncresolver.Resolver)
    answer = await resolver.resolve(name, "TXT")
    return [b"".join(record.strings).decode("utf-8", "replace") for record in answer]


async def check_dns(host: str, token: str, *, lookup: TxtLookup | None = None) -> str | None:
    """None if the TXT record is there, otherwise what was found instead."""
    name = dns_name(host)
    try:
        records = await (lookup or system_txt_lookup)(name)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return f"No TXT record at {name}."
    except (dns.exception.DNSException, OSError):
        return f"The DNS lookup for {name} failed. Try again in a minute."
    if any(record.strip() == dns_value(token) for record in records):
        return None
    return f"There is a TXT record at {name}, but it doesn't contain this token."


async def check_file(
    host: str, token: str, *, client_factory: ClientFactory = make_scan_client
) -> str | None:
    """None if the file is there with the token in it, otherwise what was found."""
    url = file_url(host)
    try:
        async with client_factory(
            timeout=FILE_TIMEOUT_SECONDS, follow_redirects=False
        ) as client, client.stream("GET", url) as response:
            if response.is_redirect:
                return f"{url} redirects elsewhere. Serve the file at that address itself."
            if response.status_code != 200:
                return f"{url} answered {response.status_code}."
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) >= FILE_READ_LIMIT:
                    break
    except BlockedRequest as exc:
        return f"{url} can't be checked: {exc.reason}"
    except httpx.HTTPError:
        return f"Couldn't reach {url}."
    if token in body[:FILE_READ_LIMIT].decode("utf-8", "replace").split():
        return None
    return f"{url} doesn't contain this token."


async def prove(
    host: str,
    token: str,
    *,
    lookup: TxtLookup | None = None,
    client_factory: ClientFactory = make_scan_client,
) -> tuple[str | None, list[str]]:
    """Try DNS, then the file. Returns `(method, problems)`: the method that
    proved control, or None and what each way found."""
    dns_problem = await check_dns(host, token, lookup=lookup)
    if dns_problem is None:
        return "dns", []
    file_problem = await check_file(host, token, client_factory=client_factory)
    if file_problem is None:
        return "file", []
    return None, [dns_problem, file_problem]
