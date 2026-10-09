"""Resolve which `(owner, repo, ref)` a scan's fix-plan/apply/verify pipeline
targets -- either parsed straight out of a repo scan's own GitHub URL, or
through the `scan_repo_links` row a URL scan gets once it's linked (PLAN-v5
Stage D).

Every module that used to call `parse_github_url(report.url)` directly
(`planning.py`, `apply.py`, `verify.py`'s `refresh_applications`) funnels
through `repo_target()` instead, so a linked URL scan behaves exactly like a
repo scan from that point on -- one place decides "where do I read/write
this scan's files", not three copies of the same branch.
"""
from __future__ import annotations

import re

from models import ScanReport
from repo.fetch import parse_github_url
from storage.scan_links import get_scan_repo_link


# GitHub owner and repository names are letters, digits, `.`, `-` and `_`.
# Anything else (a `/`, `?`, `#`, `%`) would change which GitHub API path the
# installation token is sent to, since both are pasted into request URLs.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


def is_valid_name(name: str) -> bool:
    """Whether `name` is safe as one GitHub owner or repository path segment."""
    return bool(_NAME_RE.match(name)) and name not in {".", ".."}


def is_valid_ref(ref: str) -> bool:
    """Whether `ref` is a plain branch, tag or sha: no `..`, no empty segments."""
    return (
        bool(_REF_RE.match(ref))
        and ".." not in ref
        and not ref.startswith("/")
        and not ref.endswith("/")
        and "//" not in ref
    )


class NoRepoTarget(ValueError):
    """Raised when a scan has neither a valid repo URL nor a linked
    repository -- a `ValueError` so every existing `except ValueError` catch
    site around the old direct `parse_github_url` calls keeps working
    unchanged."""


def repo_target(report: ScanReport) -> tuple[str, str, str | None]:
    """`(owner, repo, ref)` for this scan. `ref` is `None` when nothing pins
    one down, meaning "the repository's own default branch" to every caller
    -- the same convention a bare repo-scan URL (no `/tree/<ref>`) already
    carries.
    """
    if report.target_type == "repo":
        return parse_github_url(report.url)

    link = get_scan_repo_link(report.id)
    if link is None:
        raise NoRepoTarget(
            f"Scan {report.id!r} is a URL scan with no linked repository yet. "
            "Link one before planning, applying, or verifying a fix."
        )
    # Rows saved before link-repo validated its input may hold anything.
    if not (
        is_valid_name(link.owner)
        and is_valid_name(link.repo)
        and (link.ref is None or is_valid_ref(link.ref))
    ):
        raise NoRepoTarget(
            f"Scan {report.id!r} is linked to a repository name Sentinels can't use. "
            "Unlink it and link the repository again."
        )
    return link.owner, link.repo, link.ref
