"""Tests for the outbound policy (launch plan task B1).

No real DNS: every test that resolves a name passes its own fake resolver.
"""
from __future__ import annotations

import pytest

from net.policy import (
    BlockedTarget,
    check_ip,
    check_target,
    check_url,
    normalize_host,
    resolve_and_check,
)


def fake_resolver(answers: dict[str, list[str]]):
    """A resolver that answers from a dict and records what it was asked."""
    asked: list[str] = []

    async def resolve(host: str) -> list[str]:
        asked.append(host)
        return answers.get(host, [])

    resolve.asked = asked
    return resolve


async def no_dns(host: str) -> list[str]:
    raise AssertionError(f"DNS lookup attempted for {host!r}")


# --- check_url ---------------------------------------------------------------

@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://example.com", ("https", "example.com", 443)),
        ("http://example.com/path?q=1", ("http", "example.com", 80)),
        ("https://Example.COM.:443/", ("https", "example.com", 443)),
        ("http://example.com:443", ("http", "example.com", 443)),
        ("https://example.com:80", ("https", "example.com", 80)),
    ],
)
def test_check_url_accepts_plain_web_urls(url, expected):
    assert check_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "gopher://example.com",
        "javascript:alert(1)",
        "example.com",
        "//example.com",
        "https://",
        "https://example.com:8000",
        "http://example.com:22",
        "https://example.com:99999",
        "https://user@example.com",
        "https://user:pass@example.com",
        "https://:pass@example.com",
        "https://@example.com",
    ],
)
def test_check_url_rejects_other_schemes_ports_and_credentials(url):
    with pytest.raises(BlockedTarget):
        check_url(url)


@pytest.mark.parametrize(
    "host, expected",
    [
        ("2130706433", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("0x7f.0.0.1", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("[::1]", "::1"),
        ("EXAMPLE.com.", "example.com"),
    ],
)
def test_normalize_host_canonicalizes_numeric_forms(host, expected):
    assert normalize_host(host) == expected


# --- check_ip ----------------------------------------------------------------

@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.255.255.254",
        "0.0.0.0",
        "10.0.0.1",
        "10.255.255.255",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "198.18.0.1",  # benchmarking
        "224.0.0.1",  # multicast
        "224.0.1.1",  # multicast that is_global calls global
        "240.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "fe80::1",
        "fe80::1%eth0",
        "fc00::1",
        "fd12:3456::1",
        "ff02::1",
        "ff0e::1",  # global-scope multicast
        "2001:db8::1",  # documentation
    ],
)
def test_check_ip_rejects_non_public_addresses(address):
    with pytest.raises(BlockedTarget):
        check_ip(address)


@pytest.mark.parametrize(
    "address",
    [
        "::ffff:127.0.0.1",  # IPv4-mapped
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "::127.0.0.1",  # IPv4-compatible
        "::10.0.0.1",
        "64:ff9b::7f00:1",  # NAT64 well-known prefix -> 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 -> 169.254.169.254
        "64:ff9b:1::7f00:1",  # NAT64 local-use, IPv4 in the low 32 bits
        "64:ff9b:1:7f00:0:100::",  # NAT64 local-use, RFC 6052 /48 layout
        "2002:7f00:1::1",  # 6to4 -> 127.0.0.1
        "2002:c0a8:101::1",  # 6to4 -> 192.168.1.1
        "2001:0:7f00:1::1",  # Teredo, server 127.0.0.1
        "2001:0:808:808:0:0:80ff:fffe",  # Teredo, client 127.0.0.1 (stored inverted)
    ],
)
def test_check_ip_rejects_ipv6_wrapping_a_private_ipv4(address):
    with pytest.raises(BlockedTarget):
        check_ip(address)


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",
        "2606:4700:4700::1111",
        "2a00:1450:4001:81b::200e",
        "64:ff9b::808:808",  # NAT64 for 8.8.8.8
    ],
)
def test_check_ip_allows_public_addresses(address):
    assert str(check_ip(address)) == address


def test_check_ip_rejects_garbage():
    with pytest.raises(BlockedTarget):
        check_ip("not-an-ip")


# --- resolve_and_check -------------------------------------------------------

@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.5",
        "192.168.0.10",
        "[::1]",
        "::ffff:127.0.0.1",
        "2130706433",
        "0177.0.0.1",
        "0.0.0.0",
        "64:ff9b::7f00:1",
        "::127.0.0.1",
        "2002:7f00:1::1",
        "localhost",
        "LOCALHOST.",
        "app.localhost",
    ],
)
async def test_blocked_hosts_are_rejected_without_dns(host):
    with pytest.raises(BlockedTarget):
        await resolve_and_check(host, resolver=no_dns)


async def test_public_ip_literal_is_returned_without_dns():
    assert await resolve_and_check("8.8.8.8", resolver=no_dns) == ["8.8.8.8"]


async def test_public_host_returns_every_address():
    resolver = fake_resolver({"example.com": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]})

    addresses = await resolve_and_check("Example.com.", resolver=resolver)

    assert addresses == ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]
    assert resolver.asked == ["example.com"]


@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["::1"],
        ["93.184.216.34", "127.0.0.1"],  # public first, private second
        ["192.168.1.1", "93.184.216.34"],  # private first
        ["93.184.216.34", "::1"],  # public A, loopback AAAA
        ["93.184.216.34", "fe80::1"],
        ["93.184.216.34", "64:ff9b::7f00:1"],
        ["93.184.216.34", "::ffff:10.0.0.1"],
    ],
)
async def test_host_is_rejected_if_any_answer_is_not_public(answers):
    resolver = fake_resolver({"rebind.example": answers})
    with pytest.raises(BlockedTarget):
        await resolve_and_check("rebind.example", resolver=resolver)


async def test_unresolvable_host_is_rejected():
    with pytest.raises(BlockedTarget) as excinfo:
        await resolve_and_check("nope.example", resolver=fake_resolver({}))
    assert "resolved" in excinfo.value.reason


# --- check_target ------------------------------------------------------------

async def test_check_target_returns_the_vetted_addresses():
    resolver = fake_resolver({"example.com": ["93.184.216.34"]})
    assert await check_target("https://example.com/x", resolver=resolver) == (
        "https",
        "example.com",
        443,
        ["93.184.216.34"],
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",  # port, and the name
        "http://localhost",
        "http://127.0.0.1/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://0.0.0.0/",
        "http://[64:ff9b::7f00:1]/",
        "http://[::127.0.0.1]/",
        "http://[2002:7f00:1::1]/",
    ],
)
async def test_check_target_rejects_internal_urls_without_dns(url):
    with pytest.raises(BlockedTarget):
        await check_target(url, resolver=no_dns)


async def test_blocked_reason_does_not_echo_the_address():
    with pytest.raises(BlockedTarget) as excinfo:
        await check_target("http://10.1.2.3/", resolver=no_dns)
    assert "10.1.2.3" not in excinfo.value.reason
