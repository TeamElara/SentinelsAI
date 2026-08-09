"""Tests for scoring.py's dedup/alias/decay/cap rules (PLAN-v4 §V3).

All pure — no HTTP, no fixtures beyond plain `Finding` construction.
"""
from __future__ import annotations

from models import Finding, Severity, Status
from scoring import calculate_score

APEX = "https://example.com"


def _fail(id_: str, severity: Severity, *, agent: str, affected_url: str | None = None) -> Finding:
    """A minimal non-passing Finding — the fields scoring.py actually reads."""
    return Finding(
        id=id_,
        title=id_,
        category="Test",
        severity=severity,
        status=Status.FAIL,
        agent=agent,
        affected_url=affected_url,
    )


def _pass(id_: str, *, agent: str) -> Finding:
    return Finding(id=id_, title=id_, category="Test", severity=Severity.INFO, status=Status.PASS, agent=agent)


# --- Backward compatibility ------------------------------------------------
# A realistic 5-agent scan: one finding per id, all on the apex, all with
# affected_url=None. This must score exactly as calculate_score always did,
# because dedup/alias/decay/cap all become no-ops when nothing repeats.
FIVE_AGENT_FIXTURE = [
    _fail("missing-csp", Severity.HIGH, agent="headers"),
    _fail("missing-hsts", Severity.HIGH, agent="headers"),
    _fail("missing-x-content-type-options", Severity.MEDIUM, agent="headers"),
    _pass("missing-x-frame-options", agent="headers"),
    _fail("generator-meta-exposed", Severity.LOW, agent="recon"),
    _pass("robots-txt-sensitive-paths", agent="recon"),
    _fail("tls-cert-expiry", Severity.MEDIUM, agent="tls"),
    _pass("tls-protocol-version", agent="tls"),
    _pass("env-file-exposed", agent="exposure"),
    _fail("git-directory-exposed", Severity.HIGH, agent="exposure"),
    _fail("spf-record", Severity.HIGH, agent="dns"),
    _fail("dmarc-record", Severity.HIGH, agent="dns"),
]


def test_five_agent_regression_matches_pre_v4_arithmetic():
    # Same sum a plain "start at 100, subtract every non-passing penalty"
    # would give — proves dedup/alias/decay/cap changed nothing here.
    expected_penalty = 15 + 15 + 8 + 3 + 8 + 15 + 15 + 15  # csp, hsts, xcto, generator, expiry, git, spf, dmarc
    assert calculate_score(FIVE_AGENT_FIXTURE, APEX) == 100 - expected_penalty


def test_pass_findings_never_cost_points():
    assert calculate_score([_pass("missing-hsts", agent="headers")], APEX) == 100


# --- Dedup: same issue key ---------------------------------------------------

def test_duplicate_issue_from_two_agents_costs_once():
    # headers and (hypothetically) another agent both flag missing HSTS on
    # the apex host with no affected_url set -> same issue key -> one hit.
    findings = [
        _fail("missing-hsts", Severity.HIGH, agent="headers"),
        _fail("missing-hsts", Severity.HIGH, agent="headers"),
    ]
    assert calculate_score(findings, APEX) == 100 - 15


def test_duplicate_keeps_highest_severity():
    findings = [
        _fail("missing-hsts", Severity.LOW, agent="headers"),
        _fail("missing-hsts", Severity.HIGH, agent="headers"),
    ]
    assert calculate_score(findings, APEX) == 100 - 15


# --- Alias collapse -----------------------------------------------------------

def test_alias_collapses_subdomain_apex_duplicate_onto_headers():
    # subdomain agent reports the apex's own missing HSTS under its own id;
    # ALIASES maps it back onto "missing-hsts" and the host is the apex for
    # both -> one deduction, not two.
    findings = [
        _fail("missing-hsts", Severity.HIGH, agent="headers"),
        _fail("subdomain-missing-hsts", Severity.HIGH, agent="subdomain", affected_url=APEX),
    ]
    assert calculate_score(findings, APEX) == 100 - 15


def test_alias_does_not_fully_suppress_a_genuinely_different_host():
    # api.example.com missing HSTS is a real, separate problem from the
    # apex's -- it is NOT collapsed away like the same-host case above -- but
    # it's still a second occurrence of the same base issue, so rule 3's
    # decay (not alias collapse) is what discounts it, per host repeats.
    findings = [
        _fail("missing-hsts", Severity.HIGH, agent="headers"),
        _fail(
            "subdomain-missing-hsts",
            Severity.LOW,
            agent="subdomain",
            affected_url="https://api.example.com",
        ),
    ]
    # apex: full 15 (1st occurrence). api.example.com: 2nd occurrence of the
    # same canonical base_id -> half weight -> int(3 * 0.5) = 1.
    assert calculate_score(findings, APEX) == 100 - 15 - 1


# --- Repeat decay + per-agent cap --------------------------------------------

def test_repeat_decay_weights_second_and_third_at_half():
    # agent is deliberately not one of the capped three, so this isolates
    # decay from the per-agent cap tested separately below.
    findings = [
        _fail(
            f"repeat-issue:{host}",
            Severity.HIGH,
            agent="headers",
            affected_url=f"https://{host}",
        )
        for host in ["a.example.com", "b.example.com", "c.example.com"]
    ]
    # 1st full (15) + 2nd half (7) + 3rd half (7) = 29
    assert calculate_score(findings, APEX) == 100 - 29


def test_repeat_decay_zeroes_out_from_fourth_occurrence():
    findings = [
        _fail(
            f"repeat-issue:{host}",
            Severity.HIGH,
            agent="headers",
            affected_url=f"https://{host}",
        )
        for host in ["a.example.com", "b.example.com", "c.example.com", "d.example.com"]
    ]
    # 1st (15) + 2nd (7) + 3rd (7) + 4th (0) = 29, same as the 3-host case
    assert calculate_score(findings, APEX) == 100 - 29


def test_new_agent_penalty_never_exceeds_cap():
    # 30 subdomains, each with its own High-severity finding under a unique
    # base_id (so decay can't help) -> without a cap this would be 30 * 15.
    # With the cap, the subdomain agent can cost at most AGENT_PENALTY_CAP.
    findings = [
        _fail(
            f"subdomain-sensitive-name-live:{i}.example.com",
            Severity.HIGH,
            agent="subdomain",
            affected_url=f"https://{i}.example.com",
        )
        for i in range(30)
    ]
    score = calculate_score(findings, APEX)
    assert score == 100 - 20
    assert 100 - score <= 20


def test_pre_v4_agents_are_never_capped():
    # Five distinct Critical findings from headers alone (not a v4 agent)
    # must all count in full, even though 5 * 25 > AGENT_PENALTY_CAP.
    findings = [_fail(f"issue-{i}", Severity.CRITICAL, agent="headers") for i in range(5)]
    assert calculate_score(findings, APEX) == max(0, 100 - 5 * 25)


def test_cap_applies_independently_per_capped_agent():
    subdomain_findings = [
        _fail(f"subdomain-x:{i}.example.com", Severity.HIGH, agent="subdomain", affected_url=f"https://{i}.example.com")
        for i in range(10)
    ]
    misconfig_findings = [
        _fail(f"misconfig-x-{i}", Severity.HIGH, agent="misconfig") for i in range(10)
    ]
    score = calculate_score(subdomain_findings + misconfig_findings, APEX)
    # Each agent capped at 20 independently -> 40 total, not one shared cap.
    assert score == 100 - 40
