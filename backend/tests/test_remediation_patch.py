"""Tests for remediation/patch.py -- diff generation and validate_plan(),
the single safety gate every FixPlan passes through.

No network involved (see conftest.py's docstring) -- everything here builds
FixPlan/FilePatch objects by hand.
"""
from __future__ import annotations

import pytest

from models import FilePatch, Finding, FixPlan, Severity, Status
from remediation.patch import PlanValidationError, build_diff, make_patch, validate_plan
from remediation.source import SourceFile


def _finding(**overrides) -> Finding:
    defaults = dict(
        id="ci-unpinned-action-workflow-yml-L5",
        title="Unpinned action",
        category="Configuration",
        severity=Severity.MEDIUM,
        status=Status.WARN,
        file_path=".github/workflows/ci.yml",
        line=5,
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _plan(patches: list[FilePatch], **overrides) -> FixPlan:
    defaults = dict(
        finding_key="ci-unpinned-action-workflow-yml-L5",
        fixer_slug="ci-unpinned-action",
        tier=1,
        summary="test plan",
        patches=patches,
        created_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return FixPlan(**defaults)


# --- build_diff / make_patch -------------------------------------------------


def test_build_diff_shows_added_and_removed_lines():
    diff = build_diff("a.txt", "line1\nline2\n", "line1\nline3\n")
    assert "-line2" in diff
    assert "+line3" in diff
    assert "a/a.txt" in diff
    assert "b/a.txt" in diff


def test_build_diff_new_file_has_dev_null_source():
    diff = build_diff("new.txt", None, "hello\n")
    assert "/dev/null" in diff
    assert "+hello" in diff


def test_make_patch_create_has_no_original_sha():
    patch = make_patch(".gitignore", "create", None, ".env\n")
    assert patch.original_sha is None
    assert patch.original_content is None
    assert patch.new_content == ".env\n"
    assert patch.action == "create"


def test_make_patch_modify_carries_original_sha_as_drift_anchor():
    original = SourceFile(path="Dockerfile", content="FROM python\n", sha="abc123")
    patch = make_patch("Dockerfile", "modify", original, "FROM python\nUSER app\n")
    assert patch.original_sha == "abc123"
    assert patch.original_content == "FROM python\n"


# --- validate_plan: happy path -----------------------------------------------


def test_validate_plan_accepts_a_well_formed_tier1_plan():
    finding = _finding()
    patch = make_patch(
        finding.file_path,
        "modify",
        SourceFile(path=finding.file_path, content="uses: foo/bar@v1\n", sha="x"),
        "uses: foo/bar@abc123\n",
    )
    validate_plan(finding, _plan([patch]))  # must not raise


def test_validate_plan_accepts_create_when_finding_has_no_file_path():
    finding = _finding(id="gitignore-present", file_path=None, line=None)
    patch = make_patch(".gitignore", "create", None, ".env\n")
    validate_plan(finding, _plan([patch], finding_key="gitignore-present", tier=1))


# --- validate_plan: rejections -----------------------------------------------


def test_validate_plan_rejects_tier3_findings():
    finding = _finding(id="spf-record", file_path=None, line=None)
    patch = make_patch(".gitignore", "create", None, "x\n")
    with pytest.raises(PlanValidationError, match="tier 3"):
        validate_plan(finding, _plan([patch], finding_key="spf-record"))


def test_validate_plan_rejects_tier4_confidence_findings():
    finding = _finding(id="pattern-something", file_path=None, line=None, confidence=0.4)
    patch = make_patch("x.txt", "create", None, "x\n")
    with pytest.raises(PlanValidationError, match="tier 4"):
        validate_plan(finding, _plan([patch], finding_key="pattern-something"))


def test_validate_plan_rejects_empty_patch_list():
    finding = _finding()
    with pytest.raises(PlanValidationError, match="no patches"):
        validate_plan(finding, _plan([]))


def test_validate_plan_rejects_too_many_files():
    finding = _finding()
    patches = [
        make_patch(finding.file_path, "modify", SourceFile(path=finding.file_path, content="", sha="x"), "y")
        for _ in range(11)
    ]
    with pytest.raises(PlanValidationError, match="over the"):
        validate_plan(finding, _plan(patches))


@pytest.mark.parametrize("bad_path", ["/etc/passwd", "../../etc/passwd", "a/../../b", ".git/config", "sub/.git/hooks/pre-commit"])
def test_validate_plan_rejects_unsafe_paths(bad_path):
    finding = _finding(file_path=bad_path, line=1)
    patch = make_patch(bad_path, "modify", SourceFile(path=bad_path, content="", sha="x"), "y")
    with pytest.raises(PlanValidationError, match="Unsafe path"):
        validate_plan(finding, _plan([patch]))


def test_validate_plan_rejects_delete_outside_allowlist():
    finding = _finding()
    patch = FilePatch(path=finding.file_path, action="delete", original_sha="x", original_content="y", new_content=None, diff="")
    with pytest.raises(PlanValidationError, match="Delete is not permitted"):
        validate_plan(finding, _plan([patch]))


def test_validate_plan_rejects_patch_path_not_matching_finding_file_path():
    finding = _finding()  # file_path=".github/workflows/ci.yml"
    patch = make_patch("some/other/file.yml", "modify", SourceFile(path="some/other/file.yml", content="", sha="x"), "y")
    with pytest.raises(PlanValidationError, match="does not match"):
        validate_plan(finding, _plan([patch]))


def test_validate_plan_rejects_non_create_action_when_finding_has_no_file_path():
    finding = _finding(id="gitignore-present", file_path=None, line=None)
    patch = make_patch(".gitignore", "modify", SourceFile(path=".gitignore", content="x", sha="s"), "y")
    with pytest.raises(PlanValidationError, match="only 'create' actions"):
        validate_plan(finding, _plan([patch], finding_key="gitignore-present"))


# --- validate_plan: PLAN-v5 Stage D, conflict #12 ----------------------------
# security-headers is the one fixer allowed to *modify* a path even though its
# finding carries no file_path -- gated by LINK_REPO_FIXER_PATHS, not a wildcard.


def test_validate_plan_accepts_security_headers_modify_at_an_allowed_path():
    finding = _finding(id="missing-hsts", file_path=None, line=None)
    patch = make_patch(
        "vercel.json", "modify", SourceFile(path="vercel.json", content="{}", sha="s"), "{}\n"
    )
    validate_plan(
        finding,
        _plan([patch], finding_key="missing-hsts", fixer_slug="security-headers", tier=2),
    )


def test_validate_plan_rejects_security_headers_modify_outside_allowed_paths():
    finding = _finding(id="missing-hsts", file_path=None, line=None)
    patch = make_patch(
        "Caddyfile", "modify", SourceFile(path="Caddyfile", content="", sha="s"), "y"
    )
    with pytest.raises(PlanValidationError, match="may only touch"):
        validate_plan(
            finding,
            _plan([patch], finding_key="missing-hsts", fixer_slug="security-headers", tier=2),
        )
