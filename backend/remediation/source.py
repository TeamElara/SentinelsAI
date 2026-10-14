"""Fetches a single file's *current* content and blob SHA straight from
GitHub's Contents API -- the drift anchor a `FixPlan`'s diff is built
against, and the same value Stage B (unimplemented here) re-checks
immediately before ever writing anything (CLAUDE.md's remediation rule 7,
"Drift aborts").

Deliberately independent of `repo/fetch.py`'s tarball: that tarball is
deleted the moment a scan finishes (its own docstring says so), and it
never carried a per-file blob SHA anyway -- a tarball is a flat archive, not
`git` objects. The Contents API is the one unauthenticated GitHub endpoint
that hands back a blob SHA for a single path without a full clone.

`FileSource` is what a `Fixer.plan()` actually holds -- one bound to a
specific (owner, repo, ref) so fixers never see a raw `httpx.AsyncClient` or
construct a GitHub URL themselves.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from github_api import GitHubUnavailable, check_response, public_api_headers

GITHUB_API = "https://api.github.com"


@dataclass
class SourceFile:
    """One file's content as GitHub has it right now."""

    path: str
    content: str   # decoded text; "" if GitHub reports it as binary/oversized
    sha: str       # blob SHA -- the drift anchor


async def resolve_default_ref(client: httpx.AsyncClient, owner: str, repo: str) -> str:
    """The branch a bare GitHub URL (no `/tree/<ref>`) actually points at --
    the same lookup `repo/fetch.py`'s `fetch_repo` makes, kept separate here
    since this module never touches the tarball path."""
    response = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=public_api_headers())
    if response.status_code == 404:
        raise GitHubUnavailable(f"GitHub can't find {owner}/{repo}, or it is private.", 404)
    check_response(response, what=f"{owner}/{repo}")
    return response.json()["default_branch"]


async def get_file(
    client: httpx.AsyncClient, owner: str, repo: str, path: str, ref: str
) -> SourceFile | None:
    """Fetch one file's current content + blob SHA. `None` if it doesn't
    exist at `ref` -- a Finding can point at a file since deleted or
    renamed, and a Fixer treats that as "nothing left to fix", not a crash.
    """
    response = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        params={"ref": ref},
        headers=public_api_headers(),
    )
    if response.status_code == 404:
        return None
    check_response(response, what=f"{owner}/{repo}")
    data = response.json()
    if isinstance(data, list):
        return None  # `path` names a directory, not a file
    if data.get("encoding") != "base64":
        return None  # an unexpected shape -- refuse to guess at the content
    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return SourceFile(path=path, content=content, sha=data["sha"])


async def resolve_ref_sha(
    client: httpx.AsyncClient, owner: str, repo: str, ref: str
) -> str | None:
    """Resolve a tag/branch on *any* repo to its full commit SHA.

    Used by the GitHub Actions pinning fixer to turn `uses: owner/repo@v2`
    into an immutable SHA -- `owner`/`repo` here is the *action's* repo, not
    necessarily the one being scanned. Kept separate from the pure
    line-rewrite it feeds (see `remediation/workflows.py`) so that function
    stays offline-testable and only this one needs a mocked transport
    (PLAN-v5.md conflict #3: "Tag -> SHA resolution needs the network").
    """
    response = await client.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/commits/{ref}", headers=public_api_headers()
    )
    if response.status_code == 404:
        return None
    check_response(response, what=f"{owner}/{repo}")
    return response.json()["sha"]


@dataclass
class FileSource:
    """A `Fixer.plan()`'s only window onto GitHub -- bound to one
    (owner, repo, ref) so a fixer can ask for a path without knowing how
    that translates into a request."""

    client: httpx.AsyncClient
    owner: str
    repo: str
    ref: str

    async def get(self, path: str) -> SourceFile | None:
        """Current content + blob SHA for `path` in the repo this
        FileSource is bound to."""
        return await get_file(self.client, self.owner, self.repo, path, self.ref)

    async def resolve_sha(self, owner: str, repo: str, ref: str) -> str | None:
        """Resolve a ref on any repo (not necessarily the bound one) to its
        commit SHA -- see `resolve_ref_sha` above."""
        return await resolve_ref_sha(self.client, owner, repo, ref)
