"""The TLS agent — inspects the certificate presented on a real TLS handshake.

This is the first agent that doesn't use HTTP at all: it opens a raw TCP
socket and performs the TLS handshake itself via the stdlib `ssl` module,
the same protocol layer HTTPS is built on top of.

`socket`/`ssl` are blocking, synchronous APIs — there is no async version in
the standard library — so the actual connection runs on a background thread
via `asyncio.to_thread`, keeping the event loop free for the other agents
while this one waits on the network.
"""
from __future__ import annotations

import asyncio
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit

from agents.base import BaseAgent, ScanContext
from models import EvidenceKind, Finding, Severity, Status

OWASP_CRYPTO_FAILURE = "A02:2021 - Cryptographic Failures"

_EXPIRY_WARNING_DAYS = 30
_DEPRECATED_PROTOCOLS = {"SSLv2", "SSLv3", "TLSv1", "TLSv1.1"}


def fetch_certificate(hostname: str, port: int, timeout: float) -> tuple[dict, str]:
    """Blocking: open a TCP socket, do a real verified TLS handshake, and
    return (certificate dict, negotiated protocol version).

    Public (not `_`-prefixed) because `agents/subdomain.py` (PLAN-v4 §V6)
    reuses this exact helper for certificate SANs and per-subdomain TLS
    checks — one handshake implementation, not two.

    `ssl.create_default_context()` verifies the certificate chain against the
    system's trusted CAs and checks the hostname — exactly what a browser
    does. If verification fails (expired, untrusted, wrong hostname), this
    raises `ssl.SSLError` before returning anything; that's caught one level
    up, in `TLSAgent.scan()`.
    """
    context = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as ssock:
            return ssock.getpeercert(), ssock.version()


class TLSAgent(BaseAgent):
    name = "tls"
    display_name = "TLS / Certificate"
    purpose = "Performs a real TLS handshake and checks the certificate and protocol version."
    checks = [
        "HTTPS — whether the site is served over TLS at all",
        "Certificate validity — expired, untrusted chain, or hostname mismatch",
        "Certificate expiry — warns if fewer than 30 days remain",
        "Protocol version — flags deprecated TLS 1.0/1.1 and SSLv2/v3",
    ]
    category = "TLS"

    async def scan(self, context: ScanContext) -> list[Finding]:
        parsed = urlsplit(context.url)

        if parsed.scheme != "https":
            evidence_text = f"Scanned URL uses the '{parsed.scheme}' scheme, not https."
            return [Finding(
                id="tls-not-used",
                title="Site is not served over HTTPS",
                category="TLS",
                severity=Severity.CRITICAL,
                status=Status.FAIL,
                owasp=OWASP_CRYPTO_FAILURE,
                evidence=evidence_text,
                description=(
                    "Without TLS, everything sent between a browser and this "
                    "server — including anything typed into a login form — "
                    "travels in plain text and can be read or altered by "
                    "anyone on the network path."
                ),
                remediation=(
                    "Serve the site over HTTPS with a valid certificate, and "
                    "redirect all HTTP traffic to HTTPS."
                ),
                evidence_items=[
                    self.evidence(EvidenceKind.REQUEST, "Scanned URL scheme", evidence_text)
                ],
            )]

        hostname = parsed.hostname
        port = parsed.port or 443

        try:
            cert, protocol_version = await asyncio.to_thread(
                fetch_certificate, hostname, port, 10.0
            )
        except ssl.SSLError as exc:
            # Covers an expired cert, an untrusted/self-signed chain, and a
            # hostname mismatch — all three raise SSLError, with the real
            # OpenSSL reason text already in `exc`. A DNS failure or refused
            # connection is a DIFFERENT exception type (socket.gaierror /
            # ConnectionRefusedError), deliberately NOT caught here — those
            # aren't a fact about this site's TLS setup, so they're left to
            # propagate up to A3's run() wrapper as an agent-level error.
            return [Finding(
                id="tls-cert-invalid",
                title="TLS certificate could not be verified",
                category="TLS",
                severity=Severity.CRITICAL,
                status=Status.FAIL,
                owasp=OWASP_CRYPTO_FAILURE,
                evidence=str(exc),
                description=(
                    "A browser connecting to this host would refuse this "
                    "certificate outright — a full-page security warning, "
                    "not a minor notice. Common causes: the certificate has "
                    "expired, doesn't cover this hostname, or was issued by "
                    "a certificate authority nothing trusts."
                ),
                remediation=(
                    "Install a valid, currently-dated certificate from a "
                    "trusted CA that covers this exact hostname."
                ),
                evidence_items=[
                    self.evidence(EvidenceKind.CERTIFICATE, "TLS handshake error", str(exc))
                ],
            )]

        return [
            self._check_expiry(cert),
            self._check_protocol_version(protocol_version),
        ]

    def _check_expiry(self, cert: dict) -> Finding:
        not_after_raw = cert["notAfter"]
        not_after = datetime.strptime(
            not_after_raw, "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=timezone.utc)
        days_left = (not_after - datetime.now(timezone.utc)).days
        evidence = f"Certificate expires {not_after_raw} ({days_left} day(s) from now)."
        cert_evidence = [self.evidence(EvidenceKind.CERTIFICATE, "Certificate expiry", evidence)]

        if days_left <= _EXPIRY_WARNING_DAYS:
            return Finding(
                id="tls-cert-expiry",
                title="TLS certificate expires soon",
                category="TLS",
                severity=Severity.MEDIUM,
                status=Status.WARN,
                owasp=OWASP_CRYPTO_FAILURE,
                evidence=evidence,
                description=(
                    "A certificate that lapses without being renewed takes "
                    "the whole site down for every visitor with a hard "
                    "browser error, not a soft warning."
                ),
                remediation=(
                    "Renew the certificate before it expires; consider "
                    "automated renewal (e.g. Let's Encrypt + certbot) so "
                    "this can't be missed."
                ),
                evidence_items=cert_evidence,
            )
        return Finding(
            id="tls-cert-expiry",
            title="TLS certificate valid",
            category="TLS",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_CRYPTO_FAILURE,
            evidence=evidence,
            evidence_items=cert_evidence,
        )

    def _check_protocol_version(self, version: str) -> Finding:
        evidence_text = f"Negotiated protocol: {version}"
        evidence_items = [self.evidence(EvidenceKind.LOG, "Negotiated TLS protocol", evidence_text)]

        if version in _DEPRECATED_PROTOCOLS:
            return Finding(
                id="tls-protocol-version",
                title="Deprecated TLS protocol version negotiated",
                category="TLS",
                severity=Severity.HIGH,
                status=Status.FAIL,
                owasp=OWASP_CRYPTO_FAILURE,
                evidence=evidence_text,
                description=(
                    f"{version} has known weaknesses and is disabled by "
                    "modern browsers; a server still offering it is one "
                    "downgrade away from being forced onto it."
                ),
                remediation=(
                    "Disable TLS 1.0/1.1 (and any SSLv2/v3) in the server's "
                    "TLS configuration, allowing only TLS 1.2 and 1.3."
                ),
                evidence_items=evidence_items,
            )
        return Finding(
            id="tls-protocol-version",
            title="Modern TLS protocol negotiated",
            category="TLS",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_CRYPTO_FAILURE,
            evidence=evidence_text,
            evidence_items=evidence_items,
        )
