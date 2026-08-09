"""Declarative checklist rules — one per deployment readiness check.

Each rule maps to one row in the checklist_items table. Rules are organised
into three tiers (see PLAN-v2.md §0.1):

  auto         — Sentinels observed this directly (from an agent finding)
  inferred     — Weak passive signal, labelled "not conclusive"
  self_attested — We never test; we ask the developer to answer

This module is data only. The evaluation logic lives in evaluator.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from models import Finding, Status


@dataclass
class ChecklistRule:
    key: str
    title: str
    tier: str                 # "auto" | "inferred" | "self_attested"
    agent: Optional[str]      # slug, or None for self_attested
    # Pure function: findings -> (state, explanation, suggested_fix)
    evaluate: Callable[[list[Finding]], tuple[str, str, str]]
    # blocking=True: any "fail" on this item forces deployment_status="blocked"
    blocking: bool = False


def _find(findings: list[Finding], id_: str) -> Finding | None:
    return next((f for f in findings if f.id == id_), None)


def _from_finding(
    findings: list[Finding],
    id_: str,
    *,
    absent_state: str = "pass",
    absent_explanation: str = "Check passed.",
) -> tuple[str, str, str]:
    """Convert one finding's status into a checklist (state, explanation, fix) triple.

    absent_state controls what to return when the finding doesn't exist in the
    results at all. For headers this is always "pass" (every header is always
    emitted). For TLS findings like tls-cert-invalid it's "pass" too (the
    absence of an invalid-cert finding means the cert is fine).
    """
    f = _find(findings, id_)
    if f is None:
        return absent_state, absent_explanation, ""
    if f.status == Status.FAIL:
        return "fail", f.description or f.title, f.remediation or ""
    if f.status == Status.WARN:
        return "warn", f.description or f.title, f.remediation or ""
    return "pass", f.title, ""


# ── named helpers for rules that need slightly special logic ──────────────────

def _eval_https(findings: list[Finding]) -> tuple[str, str, str]:
    # tls-not-used only appears when the scheme is HTTP — its absence means HTTPS
    return _from_finding(
        findings, "tls-not-used",
        absent_state="pass",
        absent_explanation="Site is served over HTTPS.",
    )


def _eval_cert_valid(findings: list[Finding]) -> tuple[str, str, str]:
    # tls-cert-invalid only appears on a failed handshake — its absence means valid
    return _from_finding(
        findings, "tls-cert-invalid",
        absent_state="pass",
        absent_explanation="TLS certificate is valid and trusted.",
    )


def _eval_cert_expiry(findings: list[Finding]) -> tuple[str, str, str]:
    # tls-cert-expiry is always emitted for HTTPS sites; absent for HTTP ones
    return _from_finding(
        findings, "tls-cert-expiry",
        absent_state="unknown",
        absent_explanation="Cannot check — site does not use HTTPS.",
    )


RULES: list[ChecklistRule] = [
    # ── Auto-verified ─────────────────────────────────────────────────────────
    ChecklistRule(
        key="https_enforced",
        title="HTTPS enforced",
        tier="auto",
        agent="tls",
        blocking=True,
        evaluate=_eval_https,
    ),
    ChecklistRule(
        key="cert_valid",
        title="TLS certificate valid",
        tier="auto",
        agent="tls",
        blocking=True,
        evaluate=_eval_cert_valid,
    ),
    ChecklistRule(
        key="cert_not_expiring",
        title="Certificate not expiring soon",
        tier="auto",
        agent="tls",
        evaluate=_eval_cert_expiry,
    ),
    ChecklistRule(
        key="hsts_enabled",
        title="HSTS header set",
        tier="auto",
        agent="headers",
        evaluate=lambda f: _from_finding(f, "missing-hsts"),
    ),
    ChecklistRule(
        key="csp_enabled",
        title="Content-Security-Policy header set",
        tier="auto",
        agent="headers",
        evaluate=lambda f: _from_finding(f, "missing-csp"),
    ),
    ChecklistRule(
        key="clickjacking_protection",
        title="Clickjacking protection set",
        tier="auto",
        agent="headers",
        evaluate=lambda f: _from_finding(f, "missing-x-frame-options"),
    ),
    ChecklistRule(
        key="no_env_exposure",
        title=".env file not publicly accessible",
        tier="auto",
        agent="exposure",
        blocking=True,
        evaluate=lambda f: _from_finding(f, "env-file-exposed"),
    ),
    ChecklistRule(
        key="no_git_exposure",
        title=".git directory not publicly accessible",
        tier="auto",
        agent="exposure",
        blocking=True,
        evaluate=lambda f: _from_finding(f, "git-directory-exposed"),
    ),
    ChecklistRule(
        key="spf_configured",
        title="SPF record configured",
        tier="auto",
        agent="dns",
        evaluate=lambda f: _from_finding(f, "spf-record"),
    ),
    ChecklistRule(
        key="dmarc_configured",
        title="DMARC record configured",
        tier="auto",
        agent="dns",
        evaluate=lambda f: _from_finding(f, "dmarc-record"),
    ),
    ChecklistRule(
        key="no_directory_listing",
        title="Directory listing disabled",
        tier="auto",
        agent="misconfig",
        evaluate=lambda f: _from_finding(f, "dir-listing"),
    ),
    ChecklistRule(
        key="no_debug_output",
        title="No debug output or stack traces exposed",
        tier="auto",
        agent="misconfig",
        blocking=True,
        evaluate=lambda f: _from_finding(f, "debug-output-exposed"),
    ),
    ChecklistRule(
        key="no_dangling_dns",
        title="No dangling DNS records",
        tier="auto",
        agent="subdomain",
        evaluate=lambda f: _from_finding(f, "subdomain-dangling-dns"),
    ),
    # ── Passively inferred ────────────────────────────────────────────────────
    ChecklistRule(
        key="no_version_disclosure",
        title="CMS/generator version not disclosed",
        tier="inferred",
        agent="recon",
        evaluate=lambda f: _from_finding(f, "generator-meta-exposed"),
    ),
    ChecklistRule(
        key="robots_no_sensitive_paths",
        title="robots.txt doesn't expose sensitive paths",
        tier="inferred",
        agent="recon",
        evaluate=lambda f: _from_finding(f, "robots-txt-sensitive-paths"),
    ),
    # ── Self-attested ─────────────────────────────────────────────────────────
    ChecklistRule(
        key="input_validation",
        title="Input validation in place",
        tier="self_attested",
        agent=None,
        evaluate=lambda _: (
            "unknown",
            "Sentinels cannot test this passively. Confirm that all user inputs "
            "are validated and sanitised on the server side.",
            "Validate and sanitise all user inputs server-side. Reject unexpected "
            "formats at the boundary — before any processing occurs.",
        ),
    ),
    ChecklistRule(
        key="rate_limiting",
        title="Rate limiting configured",
        tier="self_attested",
        agent=None,
        evaluate=lambda _: (
            "unknown",
            "Sentinels cannot test this passively. Confirm that rate limits are "
            "in place on login, registration, and high-value API endpoints.",
            "Apply rate limiting to authentication and sensitive endpoints to "
            "prevent brute-force and denial-of-service attacks.",
        ),
    ),
    ChecklistRule(
        key="auth_secured",
        title="Authentication secured",
        tier="self_attested",
        agent=None,
        evaluate=lambda _: (
            "unknown",
            "Sentinels cannot test this passively. Confirm that authentication "
            "uses secure session management, strong password policies, and MFA "
            "where appropriate.",
            "Use secure, short-lived session tokens. Enforce password complexity "
            "and enable MFA on privileged accounts.",
        ),
    ),
    ChecklistRule(
        key="dependencies_audited",
        title="Dependencies audited for known CVEs",
        tier="self_attested",
        agent=None,
        evaluate=lambda _: (
            "unknown",
            "Sentinels cannot test this passively. Confirm that production "
            "dependencies have been checked against known vulnerability databases.",
            "Run a dependency audit (e.g. npm audit, pip-audit, snyk) and address "
            "any high-severity findings before deploying.",
        ),
    ),
]
