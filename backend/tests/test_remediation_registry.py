"""Tests for remediation/registry.py -- fixer dispatch."""
from __future__ import annotations

from models import Finding, Severity, Status
from remediation.dockerfile import DockerLatestTagFixer, DockerRootUserFixer
from remediation.gitignore import GitignoreFixer
from remediation.registry import fixable_findings, fixer_for
from remediation.scaffolding import CiScaffoldFixer, EnvExampleFixer, LicenseFixer, ReadmeFixer
from remediation.secrets import SecretEnvCommittedFixer
from remediation.workflows import PullRequestTargetFixer, WorkflowPinFixer


def _finding(finding_id: str, status: Status = Status.WARN, agent: str = "") -> Finding:
    return Finding(
        id=finding_id, title="t", category="c", severity=Severity.LOW, status=status, agent=agent,
    )


def test_fixer_for_dispatches_each_known_id_to_the_right_fixer():
    assert isinstance(fixer_for(_finding("ci-unpinned-action-x-L1")), WorkflowPinFixer)
    assert isinstance(fixer_for(_finding("gitignore-present")), GitignoreFixer)
    assert isinstance(fixer_for(_finding("repo-readme-present")), ReadmeFixer)
    assert isinstance(fixer_for(_finding("repo-env-example-present")), EnvExampleFixer)
    assert isinstance(fixer_for(_finding("docker-root-user-Dockerfile")), DockerRootUserFixer)
    assert isinstance(fixer_for(_finding("docker-latest-tag-Dockerfile-L1")), DockerLatestTagFixer)
    assert isinstance(fixer_for(_finding("secret-env-committed-dot-env")), SecretEnvCommittedFixer)
    assert isinstance(fixer_for(_finding("ci-pull-request-target-ci-yml")), PullRequestTargetFixer)
    assert isinstance(fixer_for(_finding("repo-license-present")), LicenseFixer)
    assert isinstance(fixer_for(_finding("repo-ci-configured")), CiScaffoldFixer)


def test_fixer_for_returns_none_for_unrecognized_finding():
    assert fixer_for(_finding("spf-record")) is None
    assert fixer_for(_finding("totally-unknown-id")) is None


# --- fixable_findings --------------------------------------------------------

def test_fixable_findings_keeps_only_non_passing_findings_with_a_fixer():
    findings = [
        _finding("gitignore-present", status=Status.FAIL, agent="repo-config"),
        _finding("gitignore-present", status=Status.PASS, agent="repo-config"),  # passing -- excluded
        _finding("spf-record", status=Status.FAIL, agent="dns"),                  # no fixer -- excluded
        _finding("docker-root-user-Dockerfile", status=Status.WARN, agent="repo-config"),
    ]
    kept = fixable_findings(findings)
    assert [f.id for f in kept] == ["gitignore-present", "docker-root-user-Dockerfile"]


def test_fixable_findings_preserves_input_order():
    findings = [
        _finding("docker-root-user-Dockerfile", status=Status.WARN),
        _finding("gitignore-present", status=Status.FAIL),
        _finding("repo-readme-present", status=Status.FAIL),
    ]
    assert [f.id for f in fixable_findings(findings)] == [f.id for f in findings]


def test_fixable_findings_is_empty_for_a_clean_scan():
    findings = [_finding("gitignore-present", status=Status.PASS)]
    assert fixable_findings(findings) == []


def test_fixable_findings_is_empty_when_nothing_has_a_fixer():
    findings = [_finding("spf-record", status=Status.FAIL), _finding("totally-unknown-id", status=Status.WARN)]
    assert fixable_findings(findings) == []


def test_fixable_findings_includes_header_findings_since_stage_d():
    """PLAN-v5 Stage D registers `SecurityHeaderFixer` for the four
    `missing-*` header findings -- unlike every other URL-scan finding,
    these now do collide with a Fixer's id, which is the whole point of the
    stage (see conflict #12)."""
    findings = [
        _finding("missing-hsts", status=Status.FAIL, agent="headers"),
        _finding("missing-csp", status=Status.WARN, agent="headers"),
    ]
    assert [f.id for f in fixable_findings(findings)] == ["missing-hsts", "missing-csp"]
