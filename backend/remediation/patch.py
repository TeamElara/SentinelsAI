"""Unified diffs, `FilePatch` construction, and `validate_plan()` -- the
single safety gate every `FixPlan` passes through before it's shown to a
user or persisted, rather than trusting each of the (currently four, later
more) fixers to individually get every rule right.
"""
from __future__ import annotations

import difflib
from pathlib import PurePosixPath

from models import FilePatch, Finding, FixPlan
from remediation.budget import MAX_FILES_PER_PR
from remediation.source import SourceFile
from remediation.tiers import PLANNABLE_TIERS, tier_for

# Phase 1's secret-removal fixer (`remediation/secrets.py`) adds the first
# real entries here. Deliberately a small, closed set of *exact* root-level
# filenames, not a wildcard or a pattern: this is checked with plain `in`
# membership on the patch's full repo-relative path (see `validate_plan`
# below, which this table cannot influence the logic of -- only its
# contents), so a nested path like "backend/.env" can never match no matter
# how the string is spelled. That is a deliberate limit, not an oversight --
# `SecretEnvCommittedFixer` declines for anything not listed here rather than
# guessing that a nested path is just as safe to delete as these canonical
# ones. Names match the same shapes `GitignoreFixer`'s baseline template and
# `agents/repo/secrets.py`'s `_ENV_SHAPED_RE` already treat as "obviously a
# dotenv file", not an exhaustive list of every possible `.env.*` suffix.
DELETE_ALLOWLIST: frozenset[str] = frozenset({
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.staging",
    ".env.test",
})

# PLAN-v5 Stage D, conflict #12: a header finding (`agents/headers.py`) has
# no `file_path` -- it came from observing a live site, not a repository --
# so the "every touched path traces back to the finding's own file_path"
# rule below has nothing to anchor to. `security-headers` is the one Fixer
# that legitimately *modifies* an existing file despite that: the anchor for
# it is this small, named, closed table instead, the same shape
# DELETE_ALLOWLIST already uses for "the only paths this capability may ever
# touch." A `Finding` with a `file_path` is completely unaffected by this
# table; it only applies to the no-`file_path` branch below.
LINK_REPO_FIXER_PATHS: dict[str, frozenset[str]] = {
    "security-headers": frozenset(
        {
            "next.config.js", "next.config.ts", "next.config.mjs",
            "vercel.json", "netlify.toml", "nginx.conf",
        }
    ),
}


class PlanValidationError(ValueError):
    """Raised by `validate_plan()`. Always a rejection -- there is no
    "warn but continue" path for a fix plan."""


def build_diff(path: str, original: str | None, new: str | None) -> str:
    """A standard unified diff between `original` and `new`. `None` for
    `original` renders as `/dev/null` (a new file); `None` for `new` renders
    the same way (a deletion) -- the same convention `git diff` itself uses.
    """
    from_lines = (original or "").splitlines(keepends=True)
    to_lines = (new or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        from_lines,
        to_lines,
        fromfile="/dev/null" if original is None else f"a/{path}",
        tofile="/dev/null" if new is None else f"b/{path}",
    )
    return "".join(diff)


def make_patch(
    path: str,
    action: str,
    original: SourceFile | None,
    new_content: str | None,
) -> FilePatch:
    """Build one `FilePatch`, diff included. `original` is `None` for a
    brand-new file (`action="create"`); `new_content` is `None` for a
    deletion."""
    return FilePatch(
        path=path,
        action=action,  # type: ignore[arg-type]  # Fixer callers pass a Literal already
        original_sha=original.sha if original else None,
        original_content=original.content if original else None,
        new_content=new_content,
        diff=build_diff(path, original.content if original else None, new_content),
    )


def _is_safe_path(path: str) -> bool:
    """No traversal, no absolute paths, no writes under `.git/` --
    conflict/rule from CLAUDE.md's remediation section, enforced once here
    instead of trusting every fixer to construct paths carefully."""
    if not path or path.startswith("/") or path.startswith("\\"):
        return False
    posix = PurePosixPath(path)
    if posix.is_absolute():
        return False
    parts = posix.parts
    if ".." in parts:
        return False
    if ".git" in parts:
        return False
    return True


def validate_plan(finding: Finding, plan: FixPlan) -> None:
    """The single safety gate a `FixPlan` must clear. Raises
    `PlanValidationError` on the first violation found; returns silently
    for a plan that's safe to show, store, or (from Stage B) apply.
    """
    tier = tier_for(finding)
    if tier not in PLANNABLE_TIERS:
        raise PlanValidationError(
            f"Finding {finding.id!r} is tier {tier} -- not eligible for an automatic fix plan."
        )

    if not plan.patches:
        raise PlanValidationError("Fix plan has no patches.")
    if len(plan.patches) > MAX_FILES_PER_PR:
        raise PlanValidationError(
            f"Fix plan touches {len(plan.patches)} files, over the {MAX_FILES_PER_PR}-file limit."
        )

    for patch in plan.patches:
        if not _is_safe_path(patch.path):
            raise PlanValidationError(f"Unsafe path in fix plan: {patch.path!r}")

        if patch.action == "delete" and patch.path not in DELETE_ALLOWLIST:
            raise PlanValidationError(
                f"Delete is not permitted for {patch.path!r} -- not on the secret-file allowlist."
            )

        # "Every touched path traces back to the originating finding"
        # (PLAN-v5.md): a finding that names a file may only touch that
        # file; a finding that names none (the hygiene/scaffolding checks)
        # may only *create* new files, never silently modify or delete one
        # it was never told about.
        if finding.file_path is not None:
            if patch.path != finding.file_path:
                raise PlanValidationError(
                    f"Patch path {patch.path!r} does not match the finding's "
                    f"file_path {finding.file_path!r}."
                )
        elif plan.fixer_slug in LINK_REPO_FIXER_PATHS:
            allowed = LINK_REPO_FIXER_PATHS[plan.fixer_slug]
            if patch.path not in allowed:
                raise PlanValidationError(
                    f"Fixer {plan.fixer_slug!r} may only touch {sorted(allowed)}, "
                    f"got {patch.path!r}."
                )
        elif patch.action != "create":
            raise PlanValidationError(
                f"Finding {finding.id!r} has no file_path -- only 'create' actions "
                f"are permitted, got {patch.action!r} on {patch.path!r}."
            )
