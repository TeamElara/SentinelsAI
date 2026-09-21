"""Tests for remediation/tiers.py -- the ID-prefix -> fixability tier lookup."""
from __future__ import annotations

import pytest

from models import Finding, Severity, Status
from remediation.tiers import tier_for


def _finding(finding_id: str, confidence: float | None = None) -> Finding:
    return Finding(
        id=finding_id,
        title="t",
        category="c",
        severity=Severity.LOW,
        status=Status.WARN,
        confidence=confidence,
    )


@pytest.mark.parametrize(
    "finding_id,expected_tier",
    [
        ("gitignore-present", 1),
        ("repo-readme-present", 1),
        ("repo-env-example-present", 1),
        ("repo-license-present", 1),
        ("repo-ci-configured", 1),
        ("ci-unpinned-action-workflow-yml-L12", 1),
        ("docker-root-user-Dockerfile", 2),
        ("dependency-lodash-cve-1234", 2),
        ("docker-latest-tag-Dockerfile-L1", 2),
        ("secret-env-committed-dot-env", 2),
        ("ci-pull-request-target-workflow-yml", 2),
        ("api-cors-permissive", 2),
        ("sensitive-response-cacheable", 2),
        ("server-version-disclosed", 2),
        ("risky-http-methods", 2),
        ("spf-record", 3),
        ("dmarc-record", 3),
        ("tls-weak-cipher", 3),
        ("dir-listing", 3),
        ("env-file-exposed", 3),
        ("git-directory-exposed", 3),
        ("backup-file-exposed", 3),
        ("setup-page-exposed", 3),
        ("pattern-sql-injection-hint", 4),
        ("subdomain-takeover-potential", 4),
        ("subdomain-dangling-dns", 4),
        ("something-totally-unrecognized", 4),
    ],
)
def test_tier_for_known_and_unknown_ids(finding_id, expected_tier):
    assert tier_for(_finding(finding_id)) == expected_tier


def test_confidence_set_always_forces_tier_4_even_for_a_tier1_prefix():
    finding = _finding("gitignore-present", confidence=0.5)
    assert tier_for(finding) == 4


def test_scan_partial_suffix_always_forces_tier_4():
    finding = _finding("headers-scan-partial")
    assert tier_for(finding) == 4
