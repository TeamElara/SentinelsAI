"""Fetches a public GitHub repository as a tarball and extracts it to a
temporary directory, with hard limits so an oversized or hostile repo can
never turn into a disk-fill, decompression-bomb, or path-traversal problem
for this machine.

Two entry points:

- `parse_github_url` -- turn whatever a user typed into (owner, repo, ref).
  Mirrors `orchestrator.normalize_url`'s exact contract (strip, reject
  empty, prepend a scheme if missing, then raise ValueError on anything
  unusable) so `main.py` can convert it to a 400 with the same one-line
  `except ValueError` it already has for URL scans.
- `fetch_repo` -- an async context manager. Streams the repo's tarball to
  a temp file (never into memory), extracts it into a fresh temp
  directory, yields that directory, then deletes everything on the way out
  -- success, failure or cancellation, via `finally`.

CLAUDE.md's repo-side non-negotiable applies here more than anywhere else in
this codebase: this only ever reads bytes out of the tarball. Nothing here
imports, installs, or executes a single line of the scanned repo's code.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import tarfile
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

GITHUB_API = "https://api.github.com"

# Matches ANY "word://" prefix, same reasoning as orchestrator._SCHEME_RE --
# only prepend https:// when there's truly no scheme at all, so a URL with
# an unsupported scheme falls through to the explicit rejection below.
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)"
    r"(?:\.git)?(?:/tree/(?P<ref>[\w./-]+))?/?$",
    re.IGNORECASE,
)

# Guards -- see fetch_repo() and _extract_tarball() for where each is enforced.
MAX_REPO_SIZE_KB = 50_000  # GitHub's reported on-disk repo size, ~50 MB
MAX_TOTAL_EXTRACTED_BYTES = 100_000_000  # ~100 MB uncompressed
MAX_FILE_COUNT = 5_000
MAX_INDIVIDUAL_FILE_BYTES = 5_000_000  # ~5 MB, single file
# The tarball is one gzip of the files at a single commit; `MAX_REPO_SIZE_KB`
# bounds the whole history, so a tarball this big means the size GitHub
# reported was stale or wrong. Checked live during the download, not just
# trusted from metadata.
MAX_DOWNLOAD_BYTES = 60_000_000  # ~60 MB compressed
# Longest the download may take in total. The client's own timeout only
# limits the gap between two chunks, which a slow trickle never trips.
DOWNLOAD_DEADLINE_SECONDS = 60.0

SKIP_DIRS = {
    "node_modules", ".venv", "venv", "dist", "build", ".git",
    "__pycache__", ".next", "target", "vendor", ".tox",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".tgz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".pyc", ".o", ".obj", ".wasm",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg", ".webm",
    ".pdf", ".db", ".sqlite", ".sqlite3",
}


def _raise_for_github_status(response: httpx.Response) -> None:
    """Turn any non-2xx GitHub API response into a `ValueError` — the one
    exception type everything upstream of here (`run_repo_scan_stream`,
    `main.py`'s `/repo/stream`) already knows how to turn into a clean
    `event: failed` instead of crashing the SSE stream outright.

    GitHub's unauthenticated API allows 60 requests/hour per IP — a real,
    foreseeable failure mode for a tool whose whole job is calling it, not
    an edge case. `raise_for_status()` alone raises `httpx.HTTPStatusError`,
    which nothing here was ever catching, so a rate limit (or any other
    GitHub-side error) crashed the stream instead of reporting cleanly.
    """
    if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
        reset_at = response.headers.get("x-ratelimit-reset")
        when = ""
        if reset_at:
            reset_time = datetime.fromtimestamp(int(reset_at), tz=timezone.utc)
            when = f" — resets at {reset_time.strftime('%H:%M UTC')}"
        raise ValueError(f"GitHub API rate limit exceeded{when}. Try again later.")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ValueError(
            f"GitHub returned an error ({response.status_code}) fetching this repo."
        ) from exc


def parse_github_url(raw: str) -> tuple[str, str, str | None]:
    """Turn whatever a user typed into (owner, repo, ref).

    "github.com/octocat/Hello-World", "https://github.com/octocat/Hello-World.git",
    and a URL ending in "/tree/main" all resolve. Raises ValueError on
    anything that isn't a recognizable GitHub repository URL.
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("Repository URL is empty")

    if not _SCHEME_RE.match(raw):
        raw = f"https://{raw}"

    match = _GITHUB_URL_RE.match(raw)
    if not match:
        raise ValueError(f"Not a recognizable GitHub repository URL: {raw!r}")

    return match.group("owner"), match.group("repo"), match.group("ref")


@dataclass
class RepoFetchResult:
    root: Path
    owner: str
    repo: str
    ref: str  # resolved -- never None, falls back to default_branch
    default_branch: str


@asynccontextmanager
async def fetch_repo(
    owner: str, repo: str, ref: str | None, client: httpx.AsyncClient
) -> AsyncIterator[RepoFetchResult]:
    """Download and extract one repo into a fresh temp directory.

    Use as `async with fetch_repo(...) as result:` -- everything under
    `result.root` is only guaranteed to exist inside that block. The
    directory is removed on the way out no matter how the block exits,
    including when a guard below raises partway through.
    """
    meta_response = await client.get(f"{GITHUB_API}/repos/{owner}/{repo}")
    if meta_response.status_code == 404:
        raise ValueError(f"GitHub repo {owner}/{repo} not found")
    _raise_for_github_status(meta_response)
    meta = meta_response.json()

    # Rejected here, before a single tarball byte is requested -- GitHub's
    # `size` is the repo's on-disk size in KB.
    if meta["size"] > MAX_REPO_SIZE_KB:
        raise ValueError(
            f"{owner}/{repo} is too large to scan safely "
            f"({meta['size'] / 1000:.0f} MB, limit {MAX_REPO_SIZE_KB / 1000:.0f} MB)"
        )

    default_branch = meta["default_branch"]
    resolved_ref = ref or default_branch

    # One working directory holding both the downloaded tarball and the
    # extracted tree, so the single `rmtree` below removes all of it however
    # this block ends -- including a cancelled scan, which arrives here as
    # `CancelledError` partway through the download.
    work_dir = Path(tempfile.mkdtemp(prefix="sentinels-repo-"))
    tarball_path = work_dir / "download.tar.gz"
    root = work_dir / "repo"
    root.mkdir()
    try:
        tarball_url = f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{resolved_ref}"
        try:
            async with asyncio.timeout(DOWNLOAD_DEADLINE_SECONDS):
                await _download_tarball(client, tarball_url, tarball_path, owner, repo, resolved_ref)
        except TimeoutError:
            raise ValueError(f"{owner}/{repo} took too long to download") from None

        _extract_tarball(tarball_path, root)
        # The extracted tree is all the agents read; don't keep a second
        # copy of the repo on disk for the rest of the scan.
        tarball_path.unlink(missing_ok=True)

        yield RepoFetchResult(
            root=root,
            owner=owner,
            repo=repo,
            ref=resolved_ref,
            default_branch=default_branch,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def _download_tarball(
    client: httpx.AsyncClient, url: str, dest: Path, owner: str, repo: str, ref: str
) -> None:
    """Stream the tarball to `dest` chunk by chunk, stopping at the size limit.

    Written to disk as it arrives: a repo near the limit used to sit in
    memory twice (the growing buffer, then the copy handed to `tarfile`),
    which on a 512 MB instance is most of the machine.
    """
    too_large = ValueError(f"{owner}/{repo}'s tarball exceeded the download size limit")
    # follow_redirects=True here (unlike exposure.py's checks) is correct:
    # GitHub's tarball endpoint always 302s to codeload.github.com, and
    # following that is the whole point of this request.
    async with client.stream("GET", url, follow_redirects=True) as response:
        if response.status_code == 404:
            raise ValueError(f"Ref {ref!r} not found in {owner}/{repo}")
        _raise_for_github_status(response)

        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
            raise too_large

        written = 0
        with dest.open("wb") as out:
            async for chunk in response.aiter_bytes():
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    raise too_large
                out.write(chunk)


def _extract_tarball(tarball: Path, dest: Path) -> None:
    """Extract a GitHub tarball into `dest`, skipping build/dependency
    directories and binaries, and refusing to extract anything that would
    blow past the file-count / total-size / per-file-size guards.

    `filter="data"` (stdlib, available from Python 3.12) is what blocks a
    `../../etc/passwd`-style path-traversal tar entry -- confirmed running
    on the Python 3.13 interpreter this project's venv uses.
    """
    total_bytes = 0
    file_count = 0

    with tarfile.open(tarball, mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue

            # GitHub tarballs wrap everything in one leading
            # "{owner}-{repo}-{sha}/" directory -- drop it so every
            # extracted path is repo-relative.
            parts = Path(member.name).parts[1:]
            if not parts:
                continue
            rel_path = Path(*parts)

            if any(part in SKIP_DIRS for part in rel_path.parts):
                continue
            if rel_path.suffix.lower() in BINARY_EXTENSIONS:
                continue
            if member.size > MAX_INDIVIDUAL_FILE_BYTES:
                continue  # skip this one file, not the whole repo

            file_count += 1
            total_bytes += member.size
            if file_count > MAX_FILE_COUNT or total_bytes > MAX_TOTAL_EXTRACTED_BYTES:
                raise ValueError("Repository has too many/too large files to scan safely")

            member.name = rel_path.as_posix()
            tar.extract(member, path=dest, filter="data")
