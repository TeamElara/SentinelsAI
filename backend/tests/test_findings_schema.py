"""Tests for the Finding/SubdomainEntry/ScanReport schema and its round trip
through SQLite (PLAN-v4 §V1, formalized in §V9).

V1 shipped `affected_url`/`confidence` verified only by hand ("a hand-built
Finding round-trips through save -> load"), not as a committed test. This
file is that test, plus the schema-default checks that any future field
addition should keep passing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from models import (
    AgentResult,
    Finding,
    ScanReport,
    Severity,
    Status,
    SubdomainEntry,
)
from storage.scans import get_scan, save_scan

# --- Pure schema defaults, no DB involved -------------------------------------


def test_finding_affected_url_and_confidence_default_to_none():
    f = Finding(id="x", title="X", category="Test", severity=Severity.LOW, status=Status.FAIL)
    assert f.affected_url is None
    assert f.confidence is None


def test_finding_accepts_both_new_fields_set():
    f = Finding(
        id="x", title="X", category="Test", severity=Severity.LOW, status=Status.FAIL,
        affected_url="https://api.example.com/v1", confidence=0.6,
    )
    assert f.affected_url == "https://api.example.com/v1"
    assert f.confidence == 0.6


def test_subdomain_entry_optional_fields_default_sensibly():
    e = SubdomainEntry(host="www.example.com", record_type="A", record_value="1.2.3.4", source="common-name")
    assert e.http_status is None
    assert e.scheme is None
    assert e.tls_valid is None
    assert e.server is None
    assert e.redirects_to is None
    assert e.issue_count == 0


def test_scan_report_subdomains_defaults_to_empty_list():
    report = _bare_report()
    assert report.subdomains == []


def test_agent_result_defaults_to_no_error_empty_findings():
    result = AgentResult(agent="headers")
    assert result.error is None
    assert result.findings == []
    assert result.duration_ms == 0


# --- Round trip through SQLite --------------------------------------------------


def _bare_report(**overrides) -> ScanReport:
    defaults = dict(
        id=str(uuid.uuid4()),
        url="https://example.com",
        scanned_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=1234,
        score=87,
        grade="B",
    )
    defaults.update(overrides)
    return ScanReport(**defaults)


def test_affected_url_and_confidence_round_trip(temp_db):
    finding = Finding(
        id="subdomain-dangling-dns",
        title="Potential dangling DNS record",
        category="Subdomain",
        severity=Severity.MEDIUM,
        status=Status.FAIL,
        agent="subdomain",
        affected_url="https://old.example.com",
        confidence=0.6,
    )
    report = _bare_report(agents=[AgentResult(agent="subdomain", findings=[finding])])
    save_scan(report)

    reloaded = get_scan(report.id)
    assert reloaded is not None
    assert len(reloaded.findings) == 1
    saved = reloaded.findings[0]
    assert saved.affected_url == "https://old.example.com"
    assert saved.confidence == 0.6


def test_confidence_zero_is_not_lost_as_falsy(temp_db):
    # 0.0 is falsy in Python -- a `finding.confidence or default` bug anywhere
    # in the write/read path would silently turn a real 0.0 into None. This
    # pins that it doesn't happen.
    finding = Finding(
        id="x", title="X", category="Test", severity=Severity.LOW, status=Status.FAIL,
        agent="headers", confidence=0.0,
    )
    report = _bare_report(agents=[AgentResult(agent="headers", findings=[finding])])
    save_scan(report)

    reloaded = get_scan(report.id)
    assert reloaded.findings[0].confidence == 0.0


def test_finding_with_no_affected_url_or_confidence_round_trips_as_none(temp_db):
    finding = Finding(id="missing-hsts", title="Missing HSTS", category="Headers", severity=Severity.HIGH, status=Status.FAIL, agent="headers")
    report = _bare_report(agents=[AgentResult(agent="headers", findings=[finding])])
    save_scan(report)

    reloaded = get_scan(report.id)
    assert reloaded.findings[0].affected_url is None
    assert reloaded.findings[0].confidence is None


def test_subdomain_inventory_round_trips_including_tls_valid_true_false_none(temp_db):
    entries = [
        SubdomainEntry(host="a.example.com", record_type="A", record_value="1.1.1.1", source="ct-log", tls_valid=True),
        SubdomainEntry(host="b.example.com", record_type="A", record_value="2.2.2.2", source="common-name", tls_valid=False),
        SubdomainEntry(host="c.example.com", record_type="CNAME", record_value="d.example.com", source="certificate", tls_valid=None),
    ]
    report = _bare_report(subdomains=entries)
    save_scan(report)

    reloaded = get_scan(report.id)
    by_host = {e.host: e for e in reloaded.subdomains}
    assert by_host["a.example.com"].tls_valid is True
    assert by_host["b.example.com"].tls_valid is False
    assert by_host["c.example.com"].tls_valid is None


def test_empty_subdomain_inventory_round_trips_as_empty_list(temp_db):
    report = _bare_report(subdomains=[])
    save_scan(report)

    reloaded = get_scan(report.id)
    assert reloaded.subdomains == []


def test_get_scan_counts_exclude_pass_findings_like_a_live_scan_does(temp_db):
    # storage/scans.py:get_scan uses scoring.count_by_severity, the same
    # function a live scan's orchestrator._finalize uses -- this pins that a
    # PASS finding never inflates a stored scan's severity counts (the bug
    # V1 fixed).
    fail_finding = Finding(id="missing-hsts", title="x", category="Headers", severity=Severity.HIGH, status=Status.FAIL, agent="headers")
    pass_finding = Finding(id="tls-ok", title="x", category="TLS", severity=Severity.INFO, status=Status.PASS, agent="tls")
    report = _bare_report(agents=[
        AgentResult(agent="headers", findings=[fail_finding]),
        AgentResult(agent="tls", findings=[pass_finding]),
    ])
    save_scan(report)

    reloaded = get_scan(report.id)
    assert len(reloaded.findings) == 2  # both rows persisted...
    assert reloaded.counts.get("High", 0) == 1  # ...but only the FAIL counts
    assert reloaded.counts.get("Info", 0) == 0
