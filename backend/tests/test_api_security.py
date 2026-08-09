"""Tests for the API Security agent (PLAN-v4 §V4).

All against `mock_site` — nothing here ever touches a real network.
"""
from __future__ import annotations

import json

from agents.api_security import ApiSecurityAgent
from agents.base import ScanContext
from models import Severity, Status

SOFT_404_HTML = "<html><body><h1>Not Found</h1><p>Nothing here.</p></body></html>"


async def _run(mock_site, routes):
    client = mock_site(routes)
    context = ScanContext(url="https://example.com", client=client)
    result = await ApiSecurityAgent().run(context)
    await client.aclose()
    return result


async def test_valid_openapi_json_yields_one_docs_finding(mock_site):
    spec = json.dumps({"openapi": "3.0.0", "info": {"title": "Test API"}, "paths": {}})
    result = await _run(
        mock_site,
        {"/openapi.json": (200, {"content-type": "application/json"}, spec)},
    )

    assert result.error is None
    docs = [f for f in result.findings if f.id == "api-docs-public"]
    assert len(docs) == 1
    assert docs[0].affected_url == "https://example.com/openapi.json"
    assert docs[0].status == Status.PASS
    assert docs[0].severity == Severity.INFO


async def test_soft_404_html_everywhere_yields_zero_findings(mock_site):
    # A server that answers 200 + the same HTML page for literally any path
    # must never be mistaken for a real API surface.
    routes = {path: (200, {"content-type": "text/html"}, SOFT_404_HTML) for path in [
        "/api", "/api/v1", "/api/v2", "/api/docs", "/swagger", "/swagger-ui",
        "/swagger.json", "/openapi.json", "/openapi.yaml", "/graphql", "/graphiql",
    ]}
    result = await _run(mock_site, routes)

    assert result.error is None
    # Every real check must stay silent — the only findings allowed here are
    # the agent's own informational markers (e.g. budget exhaustion, since 11
    # HEAD + up to 11 GET follow-ups can exceed the 16-request budget when
    # every path happens to 200).
    security_ids = {f.id for f in result.findings} - {"api-scan-partial", "api-scan-robots-skipped"}
    assert security_ids == set()


async def test_permissive_cors_with_credentials_is_high(mock_site):
    routes = {
        "/api": (
            200,
            {
                "content-type": "application/json",
                "access-control-allow-origin": "*",
                "access-control-allow-credentials": "true",
            },
            json.dumps({"status": "ok"}),
        ),
    }
    result = await _run(mock_site, routes)

    cors = [f for f in result.findings if f.id == "api-cors-permissive"]
    assert len(cors) == 1
    assert cors[0].severity == Severity.HIGH
    assert cors[0].status == Status.FAIL


async def test_unreachable_site_completes_cleanly(mock_site):
    def handler(request):
        import httpx as _httpx
        raise _httpx.ConnectError("connection refused", request=request)

    import httpx

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.com")
    context = ScanContext(url="https://example.com", client=client)
    result = await ApiSecurityAgent().run(context)
    await client.aclose()

    assert result.error is None  # every probe is crash-proofed, never bubbles up
    assert len(result.findings) == 1
    assert result.findings[0].id == "api-surface-clean"


async def test_unauthenticated_endpoint_is_medium_with_confidence(mock_site):
    routes = {
        "/api": (200, {"content-type": "application/json"}, json.dumps({"users": [{"id": 1}]})),
    }
    result = await _run(mock_site, routes)

    auth = [f for f in result.findings if f.id == "api-unauthenticated-endpoint"]
    assert len(auth) == 1
    assert auth[0].severity == Severity.MEDIUM
    assert auth[0].confidence == 0.5


async def test_graphiql_ide_is_medium_plain_graphql_is_info(mock_site):
    routes = {
        "/graphiql": (200, {"content-type": "text/html"}, "<html>GraphiQL</html>"),
        "/graphql": (
            200,
            {"content-type": "application/json"},
            json.dumps({"errors": [{"message": "Must provide query string."}]}),
        ),
    }
    result = await _run(mock_site, routes)

    graphql_findings = {f.affected_url: f for f in result.findings if f.id == "api-graphql-exposed"}
    assert graphql_findings["https://example.com/graphiql"].severity == Severity.MEDIUM
    assert graphql_findings["https://example.com/graphql"].severity == Severity.INFO


async def test_risky_methods_never_invoked_only_options_read(mock_site):
    routes = {
        "/api": (200, {"content-type": "application/json", "allow": "GET, POST, PUT, DELETE"}, json.dumps({})),
    }
    result = await _run(mock_site, routes)

    methods = [f for f in result.findings if f.id == "api-risky-methods"]
    assert len(methods) == 1
    assert methods[0].severity == Severity.MEDIUM  # PUT and DELETE both present


async def test_credentials_never_echoed_in_leak_evidence(mock_site):
    body = json.dumps({"debug": True, "api_key": "sk_live_abcdef1234567890"})
    routes = {"/api": (200, {"content-type": "application/json"}, body)}
    result = await _run(mock_site, routes)

    leaks = [f for f in result.findings if f.id == "api-response-leak"]
    assert len(leaks) == 1
    assert leaks[0].severity == Severity.HIGH
    assert "sk_live_abcdef1234567890" not in leaks[0].evidence


# --- V9: additional failure-case coverage -----------------------------------

async def test_malformed_json_in_openapi_path_is_not_treated_as_a_spec(mock_site):
    # A 200 at /openapi.json whose body doesn't even parse as JSON must never
    # be mistaken for a real spec -- this is a stricter case than the
    # soft-404-HTML test above (a truncated/corrupt JSON body, not HTML).
    routes = {"/openapi.json": (200, {"content-type": "application/json"}, '{"openapi": "3.0.0", "info": ')}
    result = await _run(mock_site, routes)

    assert result.error is None
    assert [f for f in result.findings if f.id == "api-docs-public"] == []


async def test_403_everywhere_yields_clean_pass_not_a_crash(mock_site):
    routes = {path: (403, {"content-type": "text/plain"}, "Forbidden") for path in [
        "/api", "/api/v1", "/api/v2", "/api/docs", "/swagger", "/swagger-ui",
        "/swagger.json", "/openapi.json", "/openapi.yaml", "/graphql", "/graphiql",
    ]}
    result = await _run(mock_site, routes)

    assert result.error is None
    assert [f.id for f in result.findings] == ["api-surface-clean"]


async def test_404_everywhere_yields_clean_pass(mock_site):
    result = await _run(mock_site, {})  # mock_site's default for any unrouted path is 404
    assert result.error is None
    assert [f.id for f in result.findings] == ["api-surface-clean"]


async def test_rate_limited_429_does_not_crash_the_agent(mock_site):
    routes = {"/api": (429, {"content-type": "application/json", "retry-after": "60"}, '{"error": "rate limited"}')}
    result = await _run(mock_site, routes)

    assert result.error is None
    # A 429 isn't a 200, so discovery never follows up with a GET -- no
    # findings about it, but critically: no exception either.
    assert [f.id for f in result.findings] == ["api-surface-clean"]


async def test_redirect_is_followed_without_crashing(mock_site):
    routes = {
        "/api": (302, {"location": "https://example.com/api/v2"}, ""),
        "/api/v2": (200, {"content-type": "application/json"}, json.dumps({"status": "ok"})),
    }
    result = await _run(mock_site, routes)

    assert result.error is None
    # The redirect target is itself a discovery path and gets probed on its
    # own merits -- nothing here should raise regardless of what it finds.
