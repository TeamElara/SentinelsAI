"""Tests for remediation/pr_body.py -- the deterministic wording of the branch,
commit, and pull request. CLAUDE.md's remediation rule 9 ("every PR says what
it does *not* fix") is a property of this file, so it is tested as one.
"""
from __future__ import annotations

import re

from models import FilePatch, Finding, FixPlan, Severity, Status
from remediation.apply import BRANCH_PATTERN
from remediation.pr_body import (
    branch_name,
    commit_message,
    pull_request_body,
    pull_request_title,
)

SCAN_ID = "a1b2c3d4-0000-4000-8000-000000000001"


def _finding(key: str, title: str) -> Finding:
    return Finding(
        id=key, title=title, category="Repo Hygiene",
        severity=Severity.MEDIUM, status=Status.FAIL,
        description="Something was missing.", agent="config",
    )


def _plan(key: str, slug: str, tier: int = 1) -> FixPlan:
    return FixPlan(
        finding_key=key, fixer_slug=slug, tier=tier, summary="Adds the missing file.",
        patches=[FilePatch(path=".gitignore", action="create", new_content="x", diff="+x")],
        created_at="2026-08-12T00:00:00+00:00",
    )


def test_branch_name_matches_the_pattern_apply_enforces():
    assert BRANCH_PATTERN.match(branch_name(SCAN_ID, 1786000000))


def test_branch_name_changes_with_the_timestamp():
    """A second attempt on the same scan must be a different branch, not a
    collision with the first."""
    assert branch_name(SCAN_ID, 1) != branch_name(SCAN_ID, 2)


def test_commit_message_names_the_single_finding():
    message = commit_message([_finding("gitignore-present", "No .gitignore")])
    assert message.startswith("fix(security): No .gitignore")
    assert "(gitignore-present)" in message


def test_commit_message_counts_a_batch():
    findings = [_finding("a", "First"), _finding("b", "Second")]
    message = commit_message(findings)
    assert "resolve 2 findings" in message
    assert "- First (a)" in message
    assert "- Second (b)" in message


def test_title_is_singular_or_counted():
    assert pull_request_title([_finding("a", "No .gitignore")]) == "Sentinels: No .gitignore"
    assert pull_request_title([_finding("a", "x"), _finding("b", "y")]) == "Sentinels: 2 security fixes"


def test_body_always_states_what_the_change_does_not_do():
    body = pull_request_body(
        SCAN_ID, [(_finding("gitignore-present", "No .gitignore"),
                   _plan("gitignore-present", "gitignore-present"))]
    )
    assert "does _not_ do" in body
    assert "addresses only the specific finding" in body


def test_gitignore_body_says_it_does_not_erase_history():
    """Rule 9's most important instance: adding ignore rules does nothing
    about a secret already committed."""
    body = pull_request_body(
        SCAN_ID, [(_finding("gitignore-present", "No .gitignore"),
                   _plan("gitignore-present", "gitignore-present"))]
    )
    assert "git history" in body
    assert "rotated" in body


def test_secret_env_committed_body_says_rotate_and_not_erased_from_history():
    """Rule 9's most explicitly-named instance: deleting a committed secret
    must never be read as "the leak is resolved"."""
    body = pull_request_body(
        SCAN_ID, [(_finding("secret-env-committed-dot-env", "Committed .env file"),
                   _plan("secret-env-committed-dot-env", "secret-env-committed", tier=2))]
    )
    assert "does not resolve the leak" in body
    assert "git history" in body
    assert "Rotate every credential" in body
    assert ".gitignore" in body


def test_env_example_body_says_no_value_was_copied_and_nothing_was_rotated():
    body = pull_request_body(
        SCAN_ID, [(_finding("repo-env-example-present", "No .env.example"),
                   _plan("repo-env-example-present", "repo-env-example-present"))]
    )
    assert "No value from your `.env` appears" in body
    assert "rotate" in body


def test_a_tier_2_fix_says_review_required():
    body = pull_request_body(
        SCAN_ID, [(_finding("docker-root-user-Dockerfile", "Container runs as root"),
                   _plan("docker-root-user-Dockerfile", "docker-root-user", tier=2))]
    )
    assert "Review required" in body


def test_an_unknown_fixer_still_gets_the_generic_caveat():
    """Silence is never read as "nothing left to do" — a fixer added later
    with no entry in the limitations table still ships the disclaimer."""
    body = pull_request_body(
        SCAN_ID, [(_finding("something-new", "A new check"), _plan("something-new", "brand-new-fixer"))]
    )
    assert "does _not_ do" in body
    assert "addresses only the specific finding" in body


def test_body_states_no_model_wrote_the_diff():
    body = pull_request_body(
        SCAN_ID, [(_finding("gitignore-present", "x"), _plan("gitignore-present", "gitignore-present"))]
    )
    assert "No language model wrote any part of this diff" in re.sub(r"\*\*", "", body)


def test_body_has_one_section_per_finding():
    pairs = [
        (_finding("a", "First"), _plan("a", "gitignore-present")),
        (_finding("b", "Second"), _plan("b", "repo-readme-present")),
    ]
    body = pull_request_body(SCAN_ID, pairs)
    assert "### First" in body
    assert "### Second" in body
    assert body.count("**Finding ID:**") == 2
