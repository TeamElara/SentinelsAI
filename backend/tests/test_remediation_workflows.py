"""Tests for remediation/workflows.py -- the GitHub Actions pinning fixer and
the pull_request_target downgrade fixer.

`rewrite_uses_line` is tested with no network at all (PLAN-v5.md conflict #3:
"a pure line-rewrite function, offline-testable"). `WorkflowPinFixer.plan`
is tested against a mocked transport (this suite's `mock_site` fixture),
never a real GitHub call.
"""
from __future__ import annotations

import base64
import json

from models import Finding, Severity, Status
from remediation.source import FileSource
from remediation.workflows import (
    PullRequestTargetFixer,
    WorkflowPinFixer,
    rewrite_trigger,
    rewrite_uses_line,
)

FULL_SHA = "a" * 40


def _finding(**overrides) -> Finding:
    defaults = dict(
        id="ci-unpinned-action-github-workflows-ci-yml-L2",
        title="Third-party action not pinned",
        category="Configuration",
        severity=Severity.MEDIUM,
        status=Status.WARN,
        file_path=".github/workflows/ci.yml",
        line=2,
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _contents_response(sha: str, content: str) -> tuple[int, dict, str]:
    body = json.dumps({
        "sha": sha,
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    })
    return (200, {"content-type": "application/json"}, body)


def _commit_response(sha: str) -> tuple[int, dict, str]:
    return (200, {"content-type": "application/json"}, json.dumps({"sha": sha}))


# --- rewrite_uses_line: pure, offline -----------------------------------


def test_rewrite_uses_line_replaces_ref_with_sha():
    line = "      uses: foo/bar@v2\n"
    result = rewrite_uses_line(line, "v2", FULL_SHA)
    assert result == f"      uses: foo/bar@{FULL_SHA}  # v2\n"


def test_rewrite_uses_line_preserves_crlf_ending():
    line = "uses: foo/bar@v2\r\n"
    result = rewrite_uses_line(line, "v2", FULL_SHA)
    assert result.endswith("\r\n")


def test_rewrite_uses_line_replaces_existing_trailing_comment():
    line = "uses: foo/bar@v2 # old note\n"
    result = rewrite_uses_line(line, "v2", FULL_SHA)
    assert result == f"uses: foo/bar@{FULL_SHA}  # v2\n"
    assert "old note" not in result


def test_rewrite_uses_line_returns_none_when_ref_not_present():
    line = "uses: foo/bar@v3\n"
    assert rewrite_uses_line(line, "v2", FULL_SHA) is None


def test_rewrite_uses_line_returns_none_without_uses_keyword():
    assert rewrite_uses_line("  - run: echo v2\n", "v2", FULL_SHA) is None


# --- WorkflowPinFixer.handles ---------------------------------------------


def test_handles_only_ci_unpinned_action_ids():
    fixer = WorkflowPinFixer()
    assert fixer.handles(_finding())
    assert not fixer.handles(_finding(id="docker-root-user-Dockerfile"))


# --- WorkflowPinFixer.plan: mocked network --------------------------------


async def test_plan_pins_the_unpinned_action(mock_site):
    workflow_text = "name: CI\njobs:\n  build:\n    steps:\n      uses: foo/bar@v2\n"
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": _contents_response("blobsha", workflow_text),
        "/repos/foo/bar/commits/v2": _commit_response(FULL_SHA),
    }
    finding = _finding(line=5)
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await WorkflowPinFixer().plan(finding, files)
    await client.aclose()

    assert plan is not None
    assert plan.tier == 1
    assert len(plan.patches) == 1
    patch = plan.patches[0]
    assert patch.path == ".github/workflows/ci.yml"
    assert patch.action == "modify"
    assert patch.original_sha == "blobsha"
    assert f"@{FULL_SHA}" in patch.new_content
    assert "# v2" in patch.new_content


async def test_plan_returns_none_when_already_pinned(mock_site):
    workflow_text = f"uses: foo/bar@{FULL_SHA}\n"
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": _contents_response("blobsha", workflow_text),
    }
    finding = _finding(line=1)
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await WorkflowPinFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_file_is_gone(mock_site):
    routes: dict = {}
    finding = _finding(line=1)
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await WorkflowPinFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_sha_resolution_fails(mock_site):
    workflow_text = "uses: foo/bar@v2\n"
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": _contents_response("blobsha", workflow_text),
        # no /repos/foo/bar/commits/v2 route -- resolves to a 404
    }
    finding = _finding(line=1)
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await WorkflowPinFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_without_file_path_or_line():
    files = FileSource(client=None, owner="o", repo="r", ref="main")  # type: ignore[arg-type]
    finding = _finding(file_path=None, line=None)
    plan = await WorkflowPinFixer().plan(finding, files)
    assert plan is None


# --- rewrite_trigger: pure, offline -------------------------------------------


def test_rewrite_trigger_swaps_a_safe_mapping_form_trigger():
    text = "on:\n  pull_request_target:\n    types: [opened]\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
    result = rewrite_trigger(text)
    assert result is not None
    assert "pull_request_target" not in result
    assert "pull_request:" in result


def test_rewrite_trigger_swaps_a_safe_list_form_trigger():
    text = "on: [pull_request_target, push]\n"
    assert rewrite_trigger(text) == "on: [pull_request, push]\n"


def test_rewrite_trigger_returns_none_when_trigger_already_gone():
    assert rewrite_trigger("on: push\n") is None


def test_rewrite_trigger_returns_none_when_pr_head_sha_is_checked_out():
    text = (
        "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "        with:\n"
        "          ref: ${{ github.event.pull_request.head.sha }}\n"
    )
    assert rewrite_trigger(text) is None


def test_rewrite_trigger_returns_none_when_pr_head_ref_is_referenced():
    text = "on:\n  pull_request_target:\njobs:\n  x:\n    env:\n      REF: ${{ github.head_ref }}\n"
    assert rewrite_trigger(text) is None


def test_rewrite_trigger_returns_none_when_plain_pull_request_trigger_also_exists():
    text = "on:\n  pull_request_target:\n  pull_request:\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
    assert rewrite_trigger(text) is None


# --- PullRequestTargetFixer.handles -------------------------------------------


def test_pr_target_handles_only_ci_pull_request_target_ids():
    fixer = PullRequestTargetFixer()
    assert fixer.handles(Finding(
        id="ci-pull-request-target-ci-yml", title="t", category="c",
        severity=Severity.HIGH, status=Status.WARN, file_path=".github/workflows/ci.yml", line=1,
    ))
    assert not fixer.handles(_finding())  # a ci-unpinned-action-* finding


# --- PullRequestTargetFixer.plan: mocked network -------------------------------


def _pr_target_finding(**overrides) -> Finding:
    defaults = dict(
        id="ci-pull-request-target-ci-yml",
        title="Workflow triggers on pull_request_target",
        category="Configuration",
        severity=Severity.HIGH,
        status=Status.WARN,
        file_path=".github/workflows/ci.yml",
        line=1,
    )
    defaults.update(overrides)
    return Finding(**defaults)


async def test_pr_target_plan_swaps_the_trigger_when_safe(mock_site):
    workflow_text = "on:\n  pull_request_target:\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": _contents_response("blobsha", workflow_text),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await PullRequestTargetFixer().plan(_pr_target_finding(), files)
    await client.aclose()

    assert plan is not None
    assert plan.tier == 2
    patch = plan.patches[0]
    assert patch.path == ".github/workflows/ci.yml"
    assert patch.action == "modify"
    assert "pull_request_target" not in patch.new_content


async def test_pr_target_plan_declines_when_fork_head_is_checked_out(mock_site):
    workflow_text = (
        "on:\n  pull_request_target:\njobs:\n  x:\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "        with:\n"
        "          ref: ${{ github.event.pull_request.head.sha }}\n"
    )
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": _contents_response("blobsha", workflow_text),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await PullRequestTargetFixer().plan(_pr_target_finding(), files)
    await client.aclose()
    assert plan is None


async def test_pr_target_plan_returns_none_when_file_is_gone(mock_site):
    routes: dict = {}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await PullRequestTargetFixer().plan(_pr_target_finding(), files)
    await client.aclose()
    assert plan is None
