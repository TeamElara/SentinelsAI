"""Ordered fixer registry -- the remediation-side sibling of
`agents/registry.py`. Order only matters in that the first fixer whose
`handles()` says yes wins; today no two fixers claim the same finding ID, so
in practice it doesn't matter yet.
"""
from __future__ import annotations

from models import Finding, Status
from remediation.base import Fixer
from remediation.dependencies import DependencyVersionFixer
from remediation.dockerfile import DockerRootUserFixer
from remediation.gitignore import GitignoreFixer
from remediation.headers_fix import SecurityHeaderFixer
from remediation.scaffolding import EnvExampleFixer, ReadmeFixer
from remediation.workflows import WorkflowPinFixer

FIXERS: list[Fixer] = [
    WorkflowPinFixer(),
    GitignoreFixer(),
    ReadmeFixer(),
    EnvExampleFixer(),
    DockerRootUserFixer(),
    SecurityHeaderFixer(),
    DependencyVersionFixer(),
]


def fixer_for(finding: Finding) -> Fixer | None:
    """The Fixer that handles `finding`, or `None` if there isn't one --
    which is the normal case for most findings (only tiers 1 and 2 ever
    have one; see remediation/tiers.py)."""
    for fixer in FIXERS:
        if fixer.handles(finding):
            return fixer
    return None


def fixable_findings(findings: list[Finding]) -> list[Finding]:
    """Every non-passing finding that has a registered Fixer, in the order
    they appear in `findings`.

    Only `handles()` runs here, never `plan()` -- `handles()` is a pure
    string check on `Finding.id` with no network involved, so this is safe
    to call from a hot path like the scan overview page. It does *not*
    guarantee `plan()` would actually produce something: a Fixer can still
    find nothing left to do once it reads the repo's current state (already
    fixed since the scan ran, the file gone). This is "might be fixable",
    the same honest hedge `FixPlanPanel`'s "Check for automatic fix" button
    already carries -- a live plan is still the only way to know for sure.
    """
    return [
        finding
        for finding in findings
        if finding.status != Status.PASS and fixer_for(finding) is not None
    ]
