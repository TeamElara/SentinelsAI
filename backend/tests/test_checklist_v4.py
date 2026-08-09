"""Tests for the three v4 checklist rules (PLAN-v4 §V7).

Pure -- no HTTP, no fixtures beyond plain `Finding` construction, same style
as test_scoring.py.
"""
from __future__ import annotations

from checklist.evaluator import evaluate
from checklist.rules import RULES
from models import Finding, Severity, Status


def _fail(id_: str, *, agent: str) -> Finding:
    return Finding(
        id=id_, title=id_, category="Test", severity=Severity.HIGH,
        status=Status.FAIL, agent=agent,
    )


def _pass(id_: str, *, agent: str) -> Finding:
    return Finding(
        id=id_, title=id_, category="Test", severity=Severity.INFO,
        status=Status.PASS, agent=agent,
    )


def _item(findings: list[Finding], key: str):
    items = evaluate(findings, RULES)
    return next(i for i in items if i.item_key == key)


# --- no_directory_listing ---------------------------------------------------

def test_directory_listing_absent_passes():
    item = _item([], "no_directory_listing")
    assert item.state == "pass"
    assert item.agent == "misconfig"


def test_directory_listing_present_fails():
    findings = [_fail("dir-listing", agent="misconfig")]
    item = _item(findings, "no_directory_listing")
    assert item.state == "fail"


def test_directory_listing_not_blocking():
    rule = next(r for r in RULES if r.key == "no_directory_listing")
    assert rule.blocking is False


# --- no_debug_output ---------------------------------------------------------

def test_debug_output_absent_passes():
    item = _item([], "no_debug_output")
    assert item.state == "pass"


def test_debug_output_present_fails_and_blocks_deployment():
    findings = [_fail("debug-output-exposed", agent="misconfig")]
    items = evaluate(findings, RULES)
    item = next(i for i in items if i.item_key == "no_debug_output")
    assert item.state == "fail"

    rule = next(r for r in RULES if r.key == "no_debug_output")
    assert rule.blocking is True


# --- no_dangling_dns ----------------------------------------------------------

def test_dangling_dns_absent_passes():
    item = _item([], "no_dangling_dns")
    assert item.state == "pass"
    assert item.agent == "subdomain"


def test_dangling_dns_present_fails():
    findings = [_fail("subdomain-dangling-dns", agent="subdomain")]
    item = _item(findings, "no_dangling_dns")
    assert item.state == "fail"


def test_dangling_dns_not_blocking():
    rule = next(r for r in RULES if r.key == "no_dangling_dns")
    assert rule.blocking is False


# --- an agent that never ran still reads sensibly, not as a false failure ---

def test_missing_agent_result_reads_as_pass_not_failure():
    # A scan where misconfig/subdomain crashed entirely produces no findings
    # with those ids at all -- absent_state="pass" (the _from_finding
    # default) means the checklist doesn't lie and claim a failure.
    other_agent_findings = [_pass("missing-hsts", agent="headers")]
    items = evaluate(other_agent_findings, RULES)
    for key in ("no_directory_listing", "no_debug_output", "no_dangling_dns"):
        item = next(i for i in items if i.item_key == key)
        assert item.state == "pass"
