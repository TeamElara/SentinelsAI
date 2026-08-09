"""The Subdomain Security agent — passively maps what a domain exposes
besides its main site, and flags the honestly-provable subset that's
risky.

Discovery merges three passive sources (never a brute-force wordlist beyond
one short, named list):

1. **Certificate SANs** — the apex's own TLS certificate lists every
   hostname it covers. Free: `tls.fetch_certificate` is a handshake this
   scan already performs once for the TLS agent; this just reads the same
   kind of response for a second, unrelated host.
2. **Certificate Transparency** (`crt.sh`) — a public log of every
   certificate ever issued for `*.<domain>`. A pure read of a third party's
   public data; no traffic to the target at all. Any failure (timeout, rate
   limit, malformed response) is silently skipped — the other two sources
   still run.
3. **A 12-name common list** — `www`, `api`, `dev`, ... — each one is only
   ever reported if DNS genuinely resolves it. "Common, therefore probably
   exists" is never good enough on its own.

Every candidate from all three sources is resolved for real before it can
appear anywhere in the output — the inventory is a list of hosts DNS
confirmed exist, not a list of guesses.

Takeover findings are the one place this agent must be conspicuously
honest: a CNAME pointing at a known hosting provider is completely normal
and, on its own, proves nothing (see `takeover_signatures.py`). Three
outcomes, three different words, matching PLAN-v4 §V6's decision table
exactly — "potential takeover" is used only when the page actually served
back matches that provider's specific unclaimed-resource fingerprint.
"""
from __future__ import annotations

import asyncio
import re
import ssl
from urllib.parse import urlsplit

import dns.resolver
import httpx

from agents.base import BaseAgent, ScanContext
from agents.probe import Budget, safe_get
from agents.takeover_signatures import match_provider
from agents.tls import fetch_certificate
from models import EvidenceKind, Finding, Severity, Status, SubdomainEntry

OWASP_MISCONFIG = "A05:2021 - Security Misconfiguration"
OWASP_CRYPTO_FAILURE = "A02:2021 - Cryptographic Failures"

MAX_DNS_LOOKUPS = 40
MAX_HTTP_REQUESTS = 25
DEADLINE_SECONDS = 15.0
MAX_DISCOVERED = 25
MAX_FOLLOWUP = 10
CT_TIMEOUT = 5.0
FOLLOWUP_TIMEOUT = 5.0

COMMON_NAMES = [
    "www", "api", "dev", "staging", "test", "admin",
    "app", "dashboard", "mail", "blog", "docs", "cdn",
]
_SENSITIVE_LABELS = {"staging", "dev", "test", "admin"}

_VERSION_RE = re.compile(r"\d+\.\d+")

_DNS_RESOLVERS = ["8.8.8.8", "1.1.1.1"]


# --- Blocking DNS/network helpers ---------------------------------------
#
# Each is a plain module-level function (not a method) specifically so a
# test can monkeypatch it directly (`monkeypatch.setattr(subdomain, "_resolve", fake)`)
# without needing a real DNS server or a real crt.sh — the same reason
# `tls.py` isolates its handshake in one function.

def _make_resolver() -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = _DNS_RESOLVERS
    return resolver


def _resolve(hostname: str) -> tuple[str, str] | None:
    """Blocking: CNAME first, then A, then AAAA. Returns the first record
    type that exists, or None if the name doesn't resolve at all."""
    resolver = _make_resolver()
    try:
        answer = resolver.resolve(hostname, "CNAME")
        return ("CNAME", str(answer[0].target).rstrip("."))
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.exception.Timeout):
        pass
    try:
        answer = resolver.resolve(hostname, "A")
        return ("A", answer[0].address)
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.exception.Timeout):
        pass
    try:
        answer = resolver.resolve(hostname, "AAAA")
        return ("AAAA", answer[0].address)
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers, dns.exception.Timeout):
        pass
    return None


def _target_resolves(hostname: str) -> bool:
    """True if `hostname` resolves to anything at all — used to tell a
    healthy CNAME target from a dangling (NXDOMAIN) one."""
    return _resolve(hostname) is not None


async def _query_ct_logs(client: httpx.AsyncClient, domain: str) -> list[str]:
    """crt.sh, a public Certificate Transparency log search. Any failure —
    timeout, non-200, malformed JSON — returns an empty list rather than
    raising; discovery just falls back to the other two sources."""
    url = f"https://crt.sh/?q=%25.{domain}&output=json"
    try:
        response = await client.get(url, timeout=CT_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    names: set[str] = set()
    for entry in data if isinstance(data, list) else []:
        raw = entry.get("name_value", "") if isinstance(entry, dict) else ""
        for name in raw.split("\n"):
            name = name.strip().lower().lstrip("*.")
            if name and name.endswith(f".{domain}") and name != domain:
                names.add(name)
    return sorted(names)


class SubdomainAgent(BaseAgent):
    name = "subdomain"
    display_name = "Subdomain Security"
    purpose = "Passively maps what else this domain exposes besides its main site, and flags risky or dangling hosts."
    checks = [
        "Discovery — certificate SANs, Certificate Transparency logs, and a 12-name common list, each DNS-verified",
        "Security headers per subdomain — HSTS/CSP presence on each host actually reachable over HTTPS",
        "TLS validity per subdomain — a real handshake against each reachable host",
        "Plain-HTTP hosts — a subdomain that resolves and serves but never over HTTPS",
        "Sensitive environment names live — a staging/dev/test/admin host reachable without an auth challenge",
        "Dangling DNS and potential subdomain takeover — honestly graded by confidence, never asserted as fact",
    ]
    category = "Subdomain"

    async def scan(self, context: ScanContext) -> list[Finding]:
        apex = urlsplit(context.url).hostname
        dns_budget = Budget(MAX_DNS_LOOKUPS, DEADLINE_SECONDS)
        http_budget = Budget(MAX_HTTP_REQUESTS, DEADLINE_SECONDS)

        entries = await self._discover(context, apex, dns_budget)
        entries.sort(key=lambda e: self._sort_key(e, apex))
        entries = entries[:MAX_DISCOVERED]

        responses: dict[str, httpx.Response] = {}
        https_failed: set[str] = set()
        for entry in entries[:MAX_FOLLOWUP]:
            await self._follow_up(context, entry, http_budget, responses, https_failed)

        findings: list[Finding] = []
        for entry in entries[:MAX_FOLLOWUP]:
            response = responses.get(entry.host)
            entry_findings = self._findings_for_entry(entry, response, entry.host in https_failed)
            findings.extend(entry_findings)

        for entry in entries:
            cname_finding = await self._check_cname(entry, responses.get(entry.host), dns_budget)
            if cname_finding is not None:
                findings.append(cname_finding)

        issue_counts: dict[str, int] = {}
        for finding in findings:
            if finding.affected_url:
                host = urlsplit(finding.affected_url).hostname
                issue_counts[host] = issue_counts.get(host, 0) + 1
        for entry in entries:
            entry.issue_count = issue_counts.get(entry.host, 0)

        context.shared["subdomains"] = entries

        if not entries:
            findings.append(self._clean_finding())
        if dns_budget.partial or http_budget.partial:
            findings.append(self._partial_finding())

        return findings

    # --- Discovery -----------------------------------------------------------

    def _sort_key(self, entry: SubdomainEntry, apex: str) -> tuple:
        apex_labels = apex.count(".")
        is_adjacent = entry.host.count(".") <= apex_labels + 1
        return (0 if is_adjacent else 1, 0 if entry.record_type == "CNAME" else 1, entry.host)

    async def _discover(
        self, context: ScanContext, apex: str, dns_budget: Budget
    ) -> list[SubdomainEntry]:
        cert_hosts = await self._discover_from_cert(apex)
        ct_hosts = await _query_ct_logs(context.client, apex)

        candidates: dict[str, str] = {}
        for host in cert_hosts:
            candidates.setdefault(host, "certificate")
        for host in ct_hosts:
            candidates.setdefault(host, "ct-log")
        for name in COMMON_NAMES:
            candidates.setdefault(f"{name}.{apex}", "common-name")

        entries: list[SubdomainEntry] = []
        for host, source in candidates.items():
            if not dns_budget.allow():
                break
            resolved = await asyncio.to_thread(_resolve, host)
            if resolved is None:
                continue
            record_type, record_value = resolved
            entries.append(SubdomainEntry(
                host=host, record_type=record_type, record_value=record_value, source=source,
            ))
        return entries

    async def _discover_from_cert(self, apex: str) -> list[str]:
        try:
            cert, _ = await asyncio.to_thread(fetch_certificate, apex, 443, CT_TIMEOUT)
        except Exception:  # noqa: BLE001 - discovery-only, a dead handshake just means "no SANs"
            return []
        names: set[str] = set()
        for kind, value in cert.get("subjectAltName", ()):
            if kind != "DNS":
                continue
            value = value.strip().lower().lstrip("*.")
            if value.endswith(f".{apex}") and value != apex:
                names.add(value)
        return sorted(names)

    # --- Follow-up: HTTPS/HTTP + TLS per discovered host ----------------------

    async def _follow_up(
        self,
        context: ScanContext,
        entry: SubdomainEntry,
        http_budget: Budget,
        responses: dict[str, httpx.Response],
        https_failed: set[str],
    ) -> None:
        response = None
        if http_budget.allow():
            response = await safe_get(context, f"https://{entry.host}/", timeout=FOLLOWUP_TIMEOUT)
        if response is not None:
            entry.scheme = "https"
        else:
            https_failed.add(entry.host)
            if http_budget.allow():
                response = await safe_get(context, f"http://{entry.host}/", timeout=FOLLOWUP_TIMEOUT)
                if response is not None:
                    entry.scheme = "http"

        if response is not None:
            responses[entry.host] = response
            entry.http_status = response.status_code
            entry.server = response.headers.get("server")
            final_host = urlsplit(str(response.url)).hostname
            if final_host and final_host != entry.host:
                entry.redirects_to = final_host

        try:
            await asyncio.to_thread(fetch_certificate, entry.host, 443, FOLLOWUP_TIMEOUT)
            entry.tls_valid = True
        except ssl.SSLError:
            entry.tls_valid = False
        except Exception:  # noqa: BLE001 - connection refused/timeout/DNS: "couldn't determine", not "invalid"
            entry.tls_valid = None

    # --- Per-subdomain findings ------------------------------------------------

    def _findings_for_entry(
        self, entry: SubdomainEntry, response: httpx.Response | None, https_failed: bool
    ) -> list[Finding]:
        findings: list[Finding] = []
        url = f"{entry.scheme}://{entry.host}/" if entry.scheme else f"https://{entry.host}/"

        if response is not None and entry.scheme == "https":
            if "strict-transport-security" not in response.headers:
                findings.append(self._subdomain_finding(
                    "subdomain-missing-hsts", "Subdomain missing HSTS", Severity.LOW, Status.WARN, url,
                    f"GET {url} -> 200, no Strict-Transport-Security header.",
                    "Without HSTS a visitor's first request to this host can still be sent in plain HTTP and intercepted before any redirect happens.",
                    "Add Strict-Transport-Security to this host's responses.",
                ))
            if "content-security-policy" not in response.headers:
                findings.append(self._subdomain_finding(
                    "subdomain-missing-csp", "Subdomain missing CSP", Severity.LOW, Status.WARN, url,
                    f"GET {url} -> 200, no Content-Security-Policy header.",
                    "Without a CSP, a script-injection bug on this host has no second line of defense.",
                    "Add a Content-Security-Policy to this host's responses.",
                ))

        if response is not None:
            server = response.headers.get("server", "")
            if server and _VERSION_RE.search(server):
                findings.append(self._subdomain_finding(
                    "subdomain-server-disclosed", "Subdomain reveals an exact software version", Severity.LOW, Status.WARN, url,
                    f"GET {url} -> Server: {server}",
                    "An exact version number tells an attacker precisely which known vulnerabilities to try.",
                    "Configure this host's server to omit the version from the Server header.",
                ))

            first_label = entry.host.split(".")[0].lower()
            if first_label in _SENSITIVE_LABELS and response.status_code == 200:
                findings.append(self._subdomain_finding(
                    "subdomain-sensitive-name-live", "Non-production host is live and unauthenticated", Severity.MEDIUM, Status.WARN, url,
                    f"GET {url} -> 200, no authentication challenge, host name suggests a non-production environment.",
                    "Staging/dev/test/admin environments are rarely hardened to the same standard as production, and this one is reachable by anyone.",
                    "Put this host behind authentication or a VPN, or take it down if it's no longer needed.",
                ))

        if entry.scheme == "http" and https_failed:
            findings.append(self._subdomain_finding(
                "subdomain-plain-http", "Subdomain resolves but has no HTTPS", Severity.MEDIUM, Status.FAIL, url,
                f"https://{entry.host}/ failed; http://{entry.host}/ served the response.",
                "Everything sent to or from this host — including any cookie set on the main site if the browser shares one — travels in plain text.",
                "Serve this host over HTTPS with a valid certificate, or take it down if it's unused.",
            ))

        if entry.tls_valid is False:
            findings.append(self._subdomain_finding(
                "subdomain-tls-invalid", "Subdomain TLS certificate is invalid", Severity.HIGH, Status.FAIL, url,
                f"TLS handshake to {entry.host}:443 failed certificate verification.",
                "A browser connecting to this host would show a full-page certificate warning.",
                "Install a valid certificate covering this exact hostname, or remove the host if it's unused.",
                owasp=OWASP_CRYPTO_FAILURE,
            ))

        return findings

    def _subdomain_finding(
        self, finding_id: str, title: str, severity: Severity, status: Status, url: str,
        evidence: str, description: str, remediation: str, owasp: str = OWASP_MISCONFIG,
    ) -> Finding:
        return Finding(
            id=finding_id,
            title=title,
            category="Subdomain",
            severity=severity,
            status=status,
            owasp=owasp,
            affected_url=url,
            evidence=evidence,
            description=description,
            remediation=remediation,
            evidence_items=[self.evidence(EvidenceKind.RESPONSE_HEADERS, title, evidence)],
        )

    # --- Dangling DNS / takeover -------------------------------------------------

    async def _check_cname(
        self, entry: SubdomainEntry, response: httpx.Response | None, dns_budget: Budget
    ) -> Finding | None:
        if entry.record_type != "CNAME" or not dns_budget.allow():
            return None
        target = entry.record_value
        target_resolves = await asyncio.to_thread(_target_resolves, target)
        url = f"https://{entry.host}/"

        if not target_resolves:
            return Finding(
                id="subdomain-dangling-dns",
                title="Potential dangling DNS record — manual verification recommended",
                category="Subdomain",
                severity=Severity.MEDIUM,
                status=Status.WARN,
                owasp=OWASP_MISCONFIG,
                affected_url=url,
                confidence=0.6,
                evidence=f"{entry.host} CNAME -> {target}, which does not resolve (NXDOMAIN).",
                description=(
                    "This host's CNAME points at a name that no longer exists. That's not "
                    "proof of anything by itself, but a CNAME to a dead target is exactly "
                    "the shape a dangling-DNS takeover starts from — worth a manual look."
                ),
                remediation="Remove this DNS record if the target is genuinely gone, or repoint it at a live resource.",
                evidence_items=[self.evidence(EvidenceKind.DNS_RECORD, "CNAME target lookup", f"{entry.host} CNAME -> {target} (NXDOMAIN)")],
            )

        provider = match_provider(target)
        if provider is None or response is None:
            return None
        if provider["fingerprint"].lower() not in response.text.lower():
            return None

        return Finding(
            id="subdomain-takeover-potential",
            title="Potential subdomain takeover — verify manually",
            category="Subdomain",
            severity=Severity.HIGH,
            status=Status.FAIL,
            owasp=OWASP_MISCONFIG,
            affected_url=url,
            confidence=0.9,
            evidence=f"{entry.host} CNAME -> {target} ({provider['provider']}); response matches that provider's unclaimed-resource page.",
            description=(
                f"This host points at {provider['provider']} but the page served back is "
                f"{provider['provider']}'s own \"nothing is registered here\" page — meaning "
                "someone else could potentially claim this resource and serve their own "
                "content under this domain's name. Confirming this requires attempting to "
                "claim the resource, which is active exploitation and out of scope here."
            ),
            remediation="Remove the dangling DNS record, or claim the resource yourself on the provider before anyone else can.",
            evidence_items=[self.evidence(EvidenceKind.DNS_RECORD, "CNAME + response fingerprint", f"{entry.host} -> {target} ({provider['provider']})")],
        )

    # --- Fallbacks ---------------------------------------------------------------

    def _clean_finding(self) -> Finding:
        evidence_text = "Certificate SANs, Certificate Transparency logs, and 12 common names — no subdomain resolved."
        return Finding(
            id="subdomain-surface-clean",
            title="No additional subdomains discovered",
            category="Subdomain",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_MISCONFIG,
            evidence=evidence_text,
            evidence_items=[self.evidence(EvidenceKind.DNS_RECORD, "Subdomain discovery", evidence_text)],
        )

    def _partial_finding(self) -> Finding:
        evidence_text = (
            f"Stopped after the DNS ({MAX_DNS_LOOKUPS} lookups) or HTTP ({MAX_HTTP_REQUESTS} requests) "
            f"budget, or {DEADLINE_SECONDS}s — results may be incomplete."
        )
        return Finding(
            id="subdomain-scan-partial",
            title="Subdomain scan stopped early (budget exhausted)",
            category="Subdomain",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_MISCONFIG,
            evidence=evidence_text,
        )
