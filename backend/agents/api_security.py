"""The API Security agent — passively discovers a publicly reachable API and
checks what it gives away.

Discovery is HEAD-first against a fixed list of common paths (never a
wordlist): only a 200 on HEAD earns a follow-up GET, and even then a 200 body
must actually *look like* the thing it claims to be (an OpenAPI doc, a
GraphiQL page, ...) before it becomes a finding — the same "don't trust a
soft-404" discipline `exposure.py` uses. Nothing here sends a mutating
request; OPTIONS is used to *read* the Allow header, never to invoke PUT/
DELETE/PATCH, and GraphQL is only ever GET, never an introspection POST.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import httpx

from agents.base import BaseAgent, ScanContext
from agents.probe import Budget, RobotsGate, safe_get, safe_head, safe_options
from models import EvidenceKind, Finding, Severity, Status

OWASP_MISCONFIG = "A05:2021 - Security Misconfiguration"
OWASP_ACCESS_CONTROL = "A01:2021 - Broken Access Control"

MAX_REQUESTS = 16
DEADLINE_SECONDS = 12.0

# Plain discovery pings — a 200 + parseable JSON body here is the auth-posture
# signal (check D), not a doc/spec.
API_BASE_PATHS = ["/api", "/api/v1", "/api/v2"]
# Doc/spec candidates — the ones a follow-up GET is allowed to content-sniff.
DOC_PATHS = ["/api/docs", "/swagger", "/swagger-ui", "/swagger.json", "/openapi.json", "/openapi.yaml"]
GRAPHQL_PATHS = ["/graphql", "/graphiql"]
DISCOVERY_PATHS = API_BASE_PATHS + DOC_PATHS + GRAPHQL_PATHS  # 11 total, per PLAN-v4 §V4

_RISKY_METHODS = {"PUT", "DELETE", "PATCH", "TRACE"}

# Internal-only hostnames/IPs a public response should never mention.
_INTERNAL_HOST_RE = re.compile(
    r"\b[\w-]+\.internal\b|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b",
    re.IGNORECASE,
)
_STACK_TRACE_RE = re.compile(r"Traceback \(most recent call last\)|Exception in thread")
_DEBUG_MARKER_RE = re.compile(r"Werkzeug|X-Debug-Token|SQLSTATE\[|mysqli", re.IGNORECASE)
# Shape-only match ("looks like a credential"), per exposure.py's no-echo rule
# — the count and type are reported, never the matched value itself.
_CREDENTIAL_RE = re.compile(
    r"(?:api[_-]?key|secret|token|password)\"?\s*[:=]\s*\"?[A-Za-z0-9_\-]{8,}", re.IGNORECASE
)

_OPENAPI_JSON_KEYS = ("openapi", "swagger")
_OPENAPI_YAML_RE = re.compile(r"^(openapi|swagger)\s*:", re.MULTILINE)
_SWAGGER_UI_MARKERS = ("swagger-ui", "swaggeruibundle")
_GRAPHIQL_MARKERS = ("graphiql", "graphql-playground")

# A deliberately foreign-looking Origin — if a server reflects this verbatim
# (or replies with a bare "*"), that's the permissive-CORS signal, regardless
# of what real third-party origins exist.
_CORS_PROBE_ORIGIN = "https://sentinels-cors-probe.invalid"


def _parse_json_safe(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _looks_like_openapi_json(text: str) -> bool:
    data = _parse_json_safe(text)
    return isinstance(data, dict) and any(key in data for key in _OPENAPI_JSON_KEYS)


def _looks_like_openapi_yaml(text: str) -> bool:
    return bool(_OPENAPI_YAML_RE.search(text))


def _looks_like_swagger_ui(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _SWAGGER_UI_MARKERS)


def _looks_like_graphiql(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _GRAPHIQL_MARKERS)


def _looks_like_graphql_response(text: str) -> bool:
    data = _parse_json_safe(text)
    return isinstance(data, dict) and ("errors" in data or "data" in data)


class ApiSecurityAgent(BaseAgent):
    name = "api-security"
    display_name = "API Security"
    purpose = "Passively discovers publicly reachable API endpoints and checks CORS, auth posture, and response leaks."
    checks = [
        "API docs — checks whether Swagger/OpenAPI/GraphiQL documentation is publicly reachable and what it reveals",
        "CORS — checks for a permissive Access-Control-Allow-Origin, especially combined with credentials",
        "Response leaks — scans API/doc responses for internal hostnames, stack traces, and credential-shaped strings",
        "Auth posture — checks whether a discovered endpoint returns data to an anonymous request",
        "HTTP methods — checks whether risky methods (PUT/DELETE/PATCH/TRACE) are advertised on the API base",
    ]
    category = "API"

    async def scan(self, context: ScanContext) -> list[Finding]:
        robots = RobotsGate()
        await robots.load(context)
        budget = Budget(MAX_REQUESTS, DEADLINE_SECONDS)

        discovered = await self._discover(context, robots, budget)

        findings: list[Finding] = []
        findings.extend(self._check_docs(discovered))
        findings.extend(self._check_graphql(discovered))
        findings.extend(self._check_leaks(discovered))
        cors_finding = await self._check_cors(context, discovered, budget)
        if cors_finding is not None:
            findings.append(cors_finding)
        findings.extend(self._check_content_type(discovered))
        findings.extend(self._check_auth_posture(discovered))
        methods_finding = await self._check_methods(context, discovered, budget)
        if methods_finding is not None:
            findings.append(methods_finding)

        if not discovered:
            findings.append(self._clean_finding())

        skipped = robots.skipped_paths
        if skipped:
            findings.append(self._robots_skipped_finding(skipped))
        if budget.partial:
            findings.append(self._partial_finding(budget))

        return findings

    async def _discover(
        self, context: ScanContext, robots: RobotsGate, budget: Budget
    ) -> list[dict]:
        """HEAD every candidate path; GET only the ones that answer 200.

        A path robots.txt disallows is skipped entirely (never fetched, not
        even HEAD) — recorded via `robots.skipped_paths`, not silently
        dropped.
        """
        discovered: list[dict] = []
        for path in DISCOVERY_PATHS:
            if not robots.allowed(path):
                continue
            if not budget.allow():
                break
            url = urljoin(context.url, path)
            head_response = await safe_head(context, url)
            if head_response is None or head_response.status_code != 200:
                continue
            get_response = await safe_get(context, url) if budget.allow() else None
            discovered.append({"path": path, "url": url, "get": get_response})
        return discovered

    # --- Check A: API docs -------------------------------------------------

    def _check_docs(self, discovered: list[dict]) -> list[Finding]:
        findings = []
        for entry in discovered:
            path, url, get_response = entry["path"], entry["url"], entry["get"]
            if path not in DOC_PATHS or get_response is None:
                continue

            text = get_response.text
            if path in ("/openapi.json", "/swagger.json"):
                is_doc = _looks_like_openapi_json(text)
            elif path == "/openapi.yaml":
                is_doc = _looks_like_openapi_yaml(text)
            else:  # /api/docs, /swagger, /swagger-ui — HTML doc UIs
                is_doc = _looks_like_swagger_ui(text)
            if not is_doc:
                continue

            severity, status = Severity.INFO, Status.PASS
            title = "API documentation is publicly reachable"
            description = (
                "Published API documentation makes the API easy for a legitimate "
                "integrator to use correctly — but it's equally readable by "
                "anyone else, so it should never describe a host or endpoint "
                "that isn't meant to be public."
            )
            if _INTERNAL_HOST_RE.search(text):
                severity, status = Severity.LOW, Status.WARN
                title = "API documentation reveals an internal hostname"
                description += (
                    " This document references what looks like an internal "
                    "hostname or private IP address."
                )

            evidence_text = f"GET {url} -> 200, recognized as an API documentation/spec response."
            findings.append(
                Finding(
                    id="api-docs-public",
                    title=title,
                    category="API",
                    severity=severity,
                    status=status,
                    owasp=OWASP_MISCONFIG,
                    affected_url=url,
                    evidence=evidence_text,
                    description=description,
                    remediation=(
                        "If this API isn't meant to be publicly documented, remove "
                        "or authenticate the docs endpoint; otherwise confirm no "
                        "internal-only host or endpoint is listed in it."
                    ),
                    evidence_items=[
                        self.evidence(EvidenceKind.HTML_SNIPPET, "API docs response", evidence_text)
                    ],
                )
            )
        return findings

    # --- Check A2: GraphQL ---------------------------------------------------

    def _check_graphql(self, discovered: list[dict]) -> list[Finding]:
        findings = []
        for entry in discovered:
            path, url, get_response = entry["path"], entry["url"], entry["get"]
            if path not in GRAPHQL_PATHS or get_response is None:
                continue

            text = get_response.text
            is_ide = _looks_like_graphiql(text)
            if not is_ide and not _looks_like_graphql_response(text):
                continue

            if is_ide:
                evidence_text = f"GET {url} -> 200, response is a GraphiQL interface page."
                findings.append(
                    Finding(
                        id="api-graphql-exposed",
                        title="GraphQL IDE (GraphiQL) is publicly reachable",
                        category="API",
                        severity=Severity.MEDIUM,
                        status=Status.WARN,
                        owasp=OWASP_MISCONFIG,
                        affected_url=url,
                        evidence=evidence_text,
                        description=(
                            "A publicly reachable GraphiQL IDE lets anyone browse "
                            "the schema interactively — a strong signal that "
                            "introspection is enabled for anonymous requests."
                        ),
                        remediation="Disable the GraphiQL IDE (and introspection) in production, or put it behind authentication.",
                        evidence_items=[
                            self.evidence(EvidenceKind.HTML_SNIPPET, "GraphiQL response", evidence_text)
                        ],
                    )
                )
            else:
                evidence_text = f"GET {url} -> 200, response looks like a GraphQL endpoint."
                findings.append(
                    Finding(
                        id="api-graphql-exposed",
                        title="GraphQL endpoint is publicly reachable",
                        category="API",
                        severity=Severity.INFO,
                        status=Status.PASS,
                        owasp=OWASP_MISCONFIG,
                        affected_url=url,
                        evidence=evidence_text,
                    )
                )
        return findings

    # --- Check B: response leaks ---------------------------------------------

    def _check_leaks(self, discovered: list[dict]) -> list[Finding]:
        findings = []
        for entry in discovered:
            url, get_response = entry["url"], entry["get"]
            if get_response is None:
                continue
            text = get_response.text

            internal_hits = len(_INTERNAL_HOST_RE.findall(text))
            has_debug = bool(_STACK_TRACE_RE.search(text)) or bool(_DEBUG_MARKER_RE.search(text))
            credential_hits = len(_CREDENTIAL_RE.findall(text))
            if not internal_hits and not has_debug and not credential_hits:
                continue

            parts = []
            if internal_hits:
                parts.append(f"{internal_hits} internal-hostname/private-IP reference(s)")
            if has_debug:
                parts.append("a stack trace or debug-framework marker")
            if credential_hits:
                parts.append(f"{credential_hits} credential-shaped string(s) (type/count only, value withheld)")

            severity = Severity.HIGH if credential_hits else Severity.MEDIUM
            evidence_text = f"GET {url} -> response contains " + "; ".join(parts) + "."
            findings.append(
                Finding(
                    id="api-response-leak",
                    title="API/documentation response leaks internal details",
                    category="API",
                    severity=severity,
                    status=Status.FAIL,
                    owasp=OWASP_MISCONFIG,
                    affected_url=url,
                    evidence=evidence_text,
                    description=(
                        "A response meant for the public internet contains information — "
                        "internal hostnames, stack traces, or credential-shaped strings — "
                        "that should never leave the server."
                    ),
                    remediation="Disable verbose/debug error output in production, and scrub internal hostnames from any publicly served response.",
                    evidence_items=[
                        self.evidence(EvidenceKind.HTML_SNIPPET, "Response leak scan", evidence_text)
                    ],
                )
            )
        return findings

    # --- Check C: CORS ---------------------------------------------------------

    async def _check_cors(
        self, context: ScanContext, discovered: list[dict], budget: Budget
    ) -> Finding | None:
        if not discovered or not budget.allow():
            return None
        target = discovered[0]["url"]
        # A direct request, deliberately outside the shared cache: the whole
        # point is a custom Origin header, and the cache keys only on
        # (method, url, follow_redirects) — reusing it here could return an
        # earlier, header-less response instead of actually probing CORS.
        try:
            response = await context.client.get(
                target, headers={"Origin": _CORS_PROBE_ORIGIN}, follow_redirects=True, timeout=5.0
            )
        except httpx.HTTPError:
            return None

        acao = response.headers.get("access-control-allow-origin")
        if acao is None or acao not in ("*", _CORS_PROBE_ORIGIN):
            return None
        acac = response.headers.get("access-control-allow-credentials", "").lower() == "true"

        severity = Severity.HIGH if acac else Severity.MEDIUM
        evidence_text = f"GET {target} with Origin: {_CORS_PROBE_ORIGIN} -> Access-Control-Allow-Origin: {acao}"
        if acac:
            evidence_text += ", Access-Control-Allow-Credentials: true"

        return Finding(
            id="api-cors-permissive",
            title="API allows cross-origin requests from any origin"
            + (" with credentials" if acac else ""),
            category="API",
            severity=severity,
            status=Status.FAIL,
            owasp=OWASP_ACCESS_CONTROL,
            affected_url=target,
            evidence=evidence_text,
            description=(
                "A permissive CORS policy lets any website read this API's "
                "responses from a logged-in user's browser."
                + (
                    " Combined with Allow-Credentials: true, a malicious page can "
                    "make an authenticated request on the victim's behalf and read "
                    "the response — the actually dangerous combination."
                    if acac
                    else ""
                )
            ),
            remediation="Restrict Access-Control-Allow-Origin to a known allow-list of trusted origins, and never combine a wildcard/reflected origin with Allow-Credentials: true.",
            evidence_items=[
                self.evidence(EvidenceKind.RESPONSE_HEADERS, "CORS probe response", evidence_text)
            ],
        )

    # --- Check C2: content-type / cacheable -------------------------------------

    def _check_content_type(self, discovered: list[dict]) -> list[Finding]:
        findings = []
        for entry in discovered:
            if entry["path"] not in API_BASE_PATHS and entry["path"] not in GRAPHQL_PATHS:
                continue
            get_response = entry["get"]
            if get_response is None:
                continue

            text = get_response.text.strip()
            if not (text.startswith("{") or text.startswith("[")):
                continue  # not JSON-shaped, nothing to check here

            content_type = get_response.headers.get("content-type", "").lower()
            url = entry["url"]
            if "json" not in content_type:
                findings.append(
                    Finding(
                        id="api-content-type",
                        title="API response served without a JSON content type",
                        category="API",
                        severity=Severity.LOW,
                        status=Status.WARN,
                        owasp=OWASP_MISCONFIG,
                        affected_url=url,
                        evidence=f"GET {url} -> Content-Type: {content_type or '(none)'}, body looks like JSON.",
                        description="Serving JSON without a matching Content-Type risks a browser misinterpreting the response.",
                        remediation="Set Content-Type: application/json on JSON API responses.",
                    )
                )
                continue  # a mislabeled response isn't meaningfully "cacheable" per JSON rules below

            cache_control = get_response.headers.get("cache-control", "").lower()
            not_protected = "no-store" not in cache_control and "private" not in cache_control
            looks_cacheable = not cache_control or "public" in cache_control or "max-age" in cache_control
            if not_protected and looks_cacheable:
                findings.append(
                    Finding(
                        id="api-cacheable-response",
                        title="API response is cacheable",
                        category="API",
                        severity=Severity.LOW,
                        status=Status.WARN,
                        owasp=OWASP_MISCONFIG,
                        affected_url=url,
                        evidence=f"GET {url} -> Cache-Control: {cache_control or '(none)'}.",
                        description="A JSON API response with no explicit no-store/private directive can end up stored in a shared cache or browser history.",
                        remediation="Add Cache-Control: no-store (or private) to API responses that return per-user or sensitive data.",
                    )
                )
        return findings

    # --- Check D: auth posture ---------------------------------------------------

    def _check_auth_posture(self, discovered: list[dict]) -> list[Finding]:
        findings = []
        for entry in discovered:
            if entry["path"] not in API_BASE_PATHS:
                continue
            get_response = entry["get"]
            if get_response is None or get_response.status_code != 200:
                continue
            data = _parse_json_safe(get_response.text)
            if data is None or (isinstance(data, (dict, list)) and len(data) == 0):
                continue

            url = entry["url"]
            findings.append(
                Finding(
                    id="api-unauthenticated-endpoint",
                    title="API endpoint appears to be publicly readable",
                    category="API",
                    severity=Severity.MEDIUM,
                    status=Status.WARN,
                    owasp=OWASP_ACCESS_CONTROL,
                    affected_url=url,
                    confidence=0.5,
                    evidence=f"GET {url} -> 200 with a JSON body, no authentication supplied.",
                    description=(
                        "An anonymous request to this endpoint returned data. This "
                        "may be intentional — manual verification is needed to "
                        "confirm whether this data is meant to be public."
                    ),
                    remediation="If this endpoint should require authentication, add an auth check; if it's intentionally public, no action is needed.",
                )
            )
        return findings

    # --- Check E: risky HTTP methods --------------------------------------------

    async def _check_methods(
        self, context: ScanContext, discovered: list[dict], budget: Budget
    ) -> Finding | None:
        api_entries = [e for e in discovered if e["path"] in API_BASE_PATHS] or discovered
        if not api_entries or not budget.allow():
            return None
        target = api_entries[0]["url"]
        response = await safe_options(context, target)
        if response is None:
            return None

        allow_header = response.headers.get("allow", "")
        methods = {m.strip().upper() for m in allow_header.split(",") if m.strip()}
        risky = methods & _RISKY_METHODS
        if not risky:
            return None

        severity = Severity.MEDIUM if {"PUT", "DELETE"} <= risky else Severity.LOW
        evidence_text = f"OPTIONS {target} -> Allow: {allow_header}"
        return Finding(
            id="api-risky-methods",
            title="API advertises risky HTTP methods",
            category="API",
            severity=severity,
            status=Status.WARN,
            owasp=OWASP_MISCONFIG,
            affected_url=target,
            evidence=evidence_text,
            description=(
                f"The Allow header lists {', '.join(sorted(risky))}, which can enable "
                "direct writes or deletes if the corresponding handlers aren't "
                "independently authenticated. These methods were never invoked."
            ),
            remediation="Confirm each advertised method is genuinely authenticated and intended; remove ones that aren't, and disable TRACE entirely.",
            evidence_items=[self.evidence(EvidenceKind.RESPONSE_HEADERS, "OPTIONS response", evidence_text)],
        )

    # --- Fallbacks ---------------------------------------------------------------

    def _clean_finding(self) -> Finding:
        evidence_text = (
            f"Checked {len(DISCOVERY_PATHS)} common API/documentation paths; "
            "none responded with a recognizable match."
        )
        return Finding(
            id="api-surface-clean",
            title="No publicly reachable API endpoints found",
            category="API",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_MISCONFIG,
            evidence=evidence_text,
            evidence_items=[self.evidence(EvidenceKind.REQUEST, "API discovery", evidence_text)],
        )

    def _robots_skipped_finding(self, skipped: list[str]) -> Finding:
        evidence_text = f"Skipped per robots.txt: {', '.join(skipped)}"
        return Finding(
            id="api-scan-robots-skipped",
            title="Some API discovery paths were skipped (robots.txt)",
            category="API",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_MISCONFIG,
            evidence=evidence_text,
        )

    def _partial_finding(self, budget: Budget) -> Finding:
        evidence_text = (
            f"Stopped after {budget.max_requests} requests / {budget.deadline_seconds}s "
            "— results may be incomplete."
        )
        return Finding(
            id="api-scan-partial",
            title="API scan stopped early (budget exhausted)",
            category="API",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_MISCONFIG,
            evidence=evidence_text,
        )
