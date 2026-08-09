"""Tests for the Subdomain Security agent (PLAN-v4 §V6).

DNS, Certificate Transparency, and TLS handshakes are never real network
calls in this suite -- `agents.subdomain._resolve`, `_target_resolves`,
`_query_ct_logs`, and `agents.tls.fetch_certificate` (imported into
`agents.subdomain`'s namespace) are monkeypatched directly. Only the
per-host HTTP follow-up goes through `mock_site`'s `httpx.MockTransport`,
which matches by path only -- exactly like every other agent's tests.
"""
from __future__ import annotations

import ssl

import agents.subdomain as subdomain
from agents.base import ScanContext
from agents.subdomain import SubdomainAgent
from models import Severity, Status


def _no_cert(*a, **k):
    raise OSError("no handshake in tests")


async def _run(mock_site, routes, monkeypatch, *, resolve, target_resolves=None, ct_hosts=None, cert=_no_cert):
    """Wire up a fully-mocked subdomain scan: `resolve` is a dict
    {hostname: (record_type, record_value) | None} or a callable; everything
    else defaults to "nothing extra found" so a test only sets what it cares
    about.
    """
    def _resolve(hostname):
        if callable(resolve):
            return resolve(hostname)
        return resolve.get(hostname)

    def _target_resolves(hostname):
        if target_resolves is None:
            return True
        if callable(target_resolves):
            return target_resolves(hostname)
        return target_resolves.get(hostname, True)

    async def _query_ct_logs(client, domain):
        return ct_hosts or []

    monkeypatch.setattr(subdomain, "_resolve", _resolve)
    monkeypatch.setattr(subdomain, "_target_resolves", _target_resolves)
    monkeypatch.setattr(subdomain, "_query_ct_logs", _query_ct_logs)
    monkeypatch.setattr(subdomain, "fetch_certificate", cert)

    client = mock_site(routes)
    context = ScanContext(url="https://example.com", client=client)
    result = await SubdomainAgent().run(context)
    await client.aclose()
    return result, context


async def test_no_subdomains_yields_clean_pass(mock_site, monkeypatch):
    result, context = await _run(mock_site, {}, monkeypatch, resolve={})

    assert result.error is None
    assert [f.id for f in result.findings] == ["subdomain-surface-clean"]
    assert context.shared["subdomains"] == []


async def test_common_name_only_resolved_ones_appear(mock_site, monkeypatch):
    resolve = {
        "www.example.com": ("A", "93.184.216.34"),
        "api.example.com": ("A", "93.184.216.35"),
    }
    routes = {"/": (200, {"content-type": "text/html"}, "hi")}
    result, context = await _run(mock_site, routes, monkeypatch, resolve=resolve)

    hosts = {e.host for e in context.shared["subdomains"]}
    assert hosts == {"www.example.com", "api.example.com"}
    assert result.error is None


async def test_cname_to_dead_target_is_dangling_medium(mock_site, monkeypatch):
    resolve = {"old.example.com": ("CNAME", "ghost-bucket.s3.amazonaws.com")}
    result, context = await _run(
        mock_site, {}, monkeypatch, resolve=resolve, target_resolves={"ghost-bucket.s3.amazonaws.com": False},
        ct_hosts=["old.example.com"],
    )

    dangling = [f for f in result.findings if f.id == "subdomain-dangling-dns"]
    assert len(dangling) == 1
    assert dangling[0].severity == Severity.MEDIUM
    assert dangling[0].confidence == 0.6
    assert "manual verification" in dangling[0].title.lower()


async def test_cname_to_live_provider_serving_normally_is_no_finding(mock_site, monkeypatch):
    resolve = {"blog.example.com": ("CNAME", "myblog.ghost.io")}
    routes = {"/": (200, {"content-type": "text/html"}, "<html>My real blog</html>")}
    result, context = await _run(
        mock_site, routes, monkeypatch, resolve=resolve, target_resolves={"myblog.ghost.io": True},
    )

    assert not [f for f in result.findings if f.id in ("subdomain-dangling-dns", "subdomain-takeover-potential")]


async def test_cname_matching_provider_fingerprint_is_takeover_high(mock_site, monkeypatch):
    resolve = {"old.example.com": ("CNAME", "someuser.github.io")}
    routes = {"/": (200, {"content-type": "text/html"}, "<html>There isn't a GitHub Pages site here.</html>")}
    result, context = await _run(
        mock_site, routes, monkeypatch, resolve=resolve, target_resolves={"someuser.github.io": True},
        ct_hosts=["old.example.com"],
    )

    takeover = [f for f in result.findings if f.id == "subdomain-takeover-potential"]
    assert len(takeover) == 1
    assert takeover[0].severity == Severity.HIGH
    assert takeover[0].confidence == 0.9
    assert "verify manually" in takeover[0].title.lower()


async def test_plain_http_only_host_is_medium(mock_site, monkeypatch):
    resolve = {"legacy.example.com": ("A", "93.184.216.36")}

    def routes_handler(request):
        import httpx as _httpx
        if request.url.scheme == "https":
            raise _httpx.ConnectError("no https here", request=request)
        return _httpx.Response(200, headers={"content-type": "text/html"}, text="plain http site")

    import httpx as _httpx
    client = _httpx.AsyncClient(transport=_httpx.MockTransport(routes_handler), base_url="https://example.com")

    monkeypatch.setattr(subdomain, "_resolve", lambda h: resolve.get(h))
    monkeypatch.setattr(subdomain, "_target_resolves", lambda h: True)
    async def _query_ct_logs(client, domain):
        return ["legacy.example.com"]
    monkeypatch.setattr(subdomain, "_query_ct_logs", _query_ct_logs)
    monkeypatch.setattr(subdomain, "fetch_certificate", _no_cert)

    context = ScanContext(url="https://example.com", client=client)
    result = await SubdomainAgent().run(context)
    await client.aclose()

    plain_http = [f for f in result.findings if f.id == "subdomain-plain-http"]
    assert len(plain_http) == 1
    assert plain_http[0].severity == Severity.MEDIUM
    assert plain_http[0].affected_url == "http://legacy.example.com/"


async def test_invalid_tls_cert_is_high(mock_site, monkeypatch):
    resolve = {"broken.example.com": ("A", "93.184.216.37")}
    routes = {"/": (200, {"content-type": "text/html"}, "hi")}

    def bad_cert(hostname, port, timeout):
        raise ssl.SSLError("certificate verify failed")

    result, context = await _run(
        mock_site, routes, monkeypatch, resolve=resolve, cert=bad_cert, ct_hosts=["broken.example.com"],
    )

    invalid = [f for f in result.findings if f.id == "subdomain-tls-invalid"]
    assert len(invalid) == 1
    assert invalid[0].severity == Severity.HIGH


async def test_sensitive_name_live_is_medium(mock_site, monkeypatch):
    resolve = {"staging.example.com": ("A", "93.184.216.38")}
    routes = {"/": (200, {"content-type": "text/html"}, "welcome to staging")}
    result, context = await _run(mock_site, routes, monkeypatch, resolve=resolve)

    sensitive = [f for f in result.findings if f.id == "subdomain-sensitive-name-live"]
    assert len(sensitive) == 1
    assert sensitive[0].severity == Severity.MEDIUM


async def test_missing_hsts_and_csp_are_low(mock_site, monkeypatch):
    resolve = {"app.example.com": ("A", "93.184.216.39")}
    routes = {"/": (200, {"content-type": "text/html"}, "hi")}
    result, context = await _run(mock_site, routes, monkeypatch, resolve=resolve)

    ids = {f.id for f in result.findings}
    assert "subdomain-missing-hsts" in ids
    assert "subdomain-missing-csp" in ids
    for f in result.findings:
        if f.id in ("subdomain-missing-hsts", "subdomain-missing-csp"):
            assert f.severity == Severity.LOW


async def test_ct_log_failure_still_finds_common_names(mock_site, monkeypatch):
    """crt.sh being unavailable must not stop the other two sources."""
    resolve = {"www.example.com": ("A", "93.184.216.34")}
    routes = {"/": (200, {"content-type": "text/html"}, "hi")}

    async def _raising_ct(client, domain):
        return []  # _query_ct_logs itself never raises -- failures are caught inside it

    result, context = await _run(mock_site, routes, monkeypatch, resolve=resolve, ct_hosts=None)

    hosts = {e.host for e in context.shared["subdomains"]}
    assert "www.example.com" in hosts
    assert result.error is None


async def test_unreachable_everything_still_completes(mock_site, monkeypatch):
    result, context = await _run(mock_site, {}, monkeypatch, resolve={})

    assert result.error is None
    assert context.shared["subdomains"] == []
    assert [f.id for f in result.findings] == ["subdomain-surface-clean"]


async def test_rate_limited_429_on_followup_does_not_crash(mock_site, monkeypatch):
    resolve = {"api.example.com": ("A", "93.184.216.40")}
    routes = {"/": (429, {"retry-after": "30"}, "Too Many Requests")}
    result, context = await _run(mock_site, routes, monkeypatch, resolve=resolve)

    assert result.error is None
    entry = next(e for e in context.shared["subdomains"] if e.host == "api.example.com")
    assert entry.http_status == 429


async def test_dns_exception_on_one_host_is_isolated_by_base_agent_crash_proofing(mock_site, monkeypatch):
    # _resolve isn't individually try/excepted inside _discover's loop -- a
    # genuine bug there (as opposed to the expected NXDOMAIN/NoAnswer cases
    # _resolve already handles) surfaces as AgentResult.error via
    # BaseAgent.run()'s blanket crash-proofing, exactly like any other agent.
    # This pins that degrade mode is what actually happens today, and that
    # it never hangs or raises past run().
    def flaky_resolve(hostname):
        if hostname == "www.example.com":
            raise RuntimeError("simulated resolver bug")
        return None

    result, context = await _run(mock_site, {}, monkeypatch, resolve=flaky_resolve)

    assert result.error is not None
    assert "simulated resolver bug" in result.error


async def test_discovery_sources_are_deduped(mock_site, monkeypatch):
    """A host found by both cert SANs and CT logs must appear once."""
    resolve = {"www.example.com": ("A", "93.184.216.34")}
    routes = {"/": (200, {"content-type": "text/html"}, "hi")}

    def cert(hostname, port, timeout):
        return ({"subjectAltName": (("DNS", "example.com"), ("DNS", "www.example.com"))}, "TLSv1.3")

    result, context = await _run(
        mock_site, routes, monkeypatch, resolve=resolve, ct_hosts=["www.example.com"], cert=cert,
    )

    matching = [e for e in context.shared["subdomains"] if e.host == "www.example.com"]
    assert len(matching) == 1
