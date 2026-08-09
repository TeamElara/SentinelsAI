"""Coordinates one repo scan -- the repo-side sibling of `orchestrator.py`.

Same shape as the URL orchestrator on purpose: parse the target, fetch/walk
it once, run every registered agent concurrently via `asyncio.gather`, then
hand the combined findings to the *same* `scoring.py` / `checklist/evaluator.py`
/ `ai/analyst.py` / `storage/scans.py` every URL scan already uses (PLAN-v3's
"extend, don't fork" architecture note). Only fetching and the agents
themselves are genuinely repo-specific.

`run_repo_scan` was pulled forward from its originally-planned home in R11
(the frontend/streaming milestone) because R9's own verification bar --
"scan the same repo twice, checklist and readiness must be identical" --
can't be checked without a real end-to-end repo scan to run twice. R11 still
owns the streaming variant (`GET /repo/stream`, mirroring `scan_stream`) and
the frontend that calls it; this module just gives R9 (and R10 after it)
something real to verify against, the same way `docs/PLAN-v3.md`'s "Resume
here" note anticipated.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from agents.repo.base import RepoContext, RepoFile, list_repo_files
from agents.repo_registry import AGENTS_REPO
from ai.analyst import summarize
from checklist.evaluator import compute_readiness, evaluate
from checklist.repo_rules import REPO_RULES
from models import AgentResult, RepoFileEntry, ScanReport
from repo.fetch import parse_github_url, fetch_repo
from scoring import calculate_score, count_by_severity, grade_for_score
from storage.scans import save_scan

# R12: guesses a file-tree badge language from its extension. Deliberately a
# flat lookup, not a real language-detection library (e.g. linguist) — the
# tree only needs a short label to show next to a filename, not the accuracy
# a syntax highlighter would need.
_LANGUAGE_BY_EXTENSION = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".go": "Go",
    ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++",
    ".hpp": "C++", ".cs": "C#", ".rs": "Rust", ".swift": "Swift",
    ".kt": "Kotlin", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".md": "Markdown",
    ".sql": "SQL", ".sh": "Shell",
}


def _guess_language(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    if name == "Dockerfile":
        return "Docker"
    if "." not in name:
        return None
    return _LANGUAGE_BY_EXTENSION.get("." + name.rsplit(".", 1)[-1].lower())


async def _finalize(
    raw_url: str,
    start: float,
    agent_results: list[AgentResult],
    repo_files: list[RepoFile] | None = None,
) -> ScanReport:
    """Shared by `run_repo_scan` and `run_repo_scan_stream` — turn finished
    agent results into a persisted report. The repo-side sibling of
    `orchestrator._finalize`, same reasoning: one place a repo scan becomes
    durable, so both callers agree on exactly what "done" means.

    `repo_files` (R12) is the walked file tree from `RepoContext.files` —
    optional only so this signature stays valid if `_finalize` is ever called
    without one; both real callers below always pass it.
    """
    findings = [finding for result in agent_results for finding in result.findings]
    duration_ms = int((time.perf_counter() - start) * 1000)

    score = calculate_score(findings, raw_url)
    grade = grade_for_score(score)
    summary = await summarize(raw_url, score, grade, findings, target_type="repo")

    checklist = evaluate(findings, rules=REPO_RULES)
    readiness_score, deployment_status = compute_readiness(checklist, rules=REPO_RULES)

    report = ScanReport(
        id=str(uuid.uuid4()),
        url=raw_url,
        target_type="repo",
        scanned_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=duration_ms,
        score=score,
        grade=grade,
        summary=summary,
        counts=count_by_severity(findings),
        findings=findings,
        agents=agent_results,
        readiness_score=readiness_score,
        deployment_status=deployment_status,
        checklist=checklist,
    )

    file_entries = None
    if repo_files is not None:
        # Counted from this same `findings` list, not re-queried later — the
        # tree's badges and the agent pages' issue counts are guaranteed to
        # agree because they come from one pass over one list.
        counts_by_path: dict[str, int] = {}
        for finding in findings:
            if finding.file_path:
                counts_by_path[finding.file_path] = counts_by_path.get(finding.file_path, 0) + 1
        file_entries = [
            RepoFileEntry(
                path=rf.path,
                size=rf.size,
                language=_guess_language(rf.path),
                finding_count=counts_by_path.get(rf.path, 0),
            )
            for rf in repo_files
        ]

    save_scan(report, repo_files=file_entries)
    return report


async def run_repo_scan(raw_url: str) -> ScanReport:
    """Parse a GitHub URL, fetch and walk the repo, run every repo agent
    against it, and assemble + persist the final report.

    Raises `ValueError` for anything `parse_github_url` or `fetch_repo`
    rejects (not a GitHub URL, repo not found, too large, ...) -- `main.py`
    turns that into a 400, the same one-line `except ValueError` the URL
    side's `POST /scan` already uses.
    """
    owner, repo, ref = parse_github_url(raw_url)
    start = time.perf_counter()

    async with httpx.AsyncClient(timeout=15.0) as client:
        async with fetch_repo(owner, repo, ref, client) as fetched:
            context = RepoContext(
                repo_url=raw_url,
                owner=owner,
                repo=repo,
                ref=fetched.ref,
                root=fetched.root,
                files=list_repo_files(fetched.root),
                client=client,
            )
            agent_results: list[AgentResult] = list(
                await asyncio.gather(*(agent_cls().run(context) for agent_cls in AGENTS_REPO))
            )

    return await _finalize(raw_url, start, agent_results, context.files)


async def run_repo_scan_stream(
    raw_url: str,
) -> AsyncIterator[tuple[str, AgentResult | ScanReport]]:
    """Like `run_repo_scan`, but yields progress instead of making the caller
    wait — the repo-side sibling of `orchestrator.run_scan_stream`.

    Yields `("agent", AgentResult)` once per agent, in real completion order,
    then exactly one `("done", ScanReport)`. `asyncio.as_completed` gives the
    real-time ordering the same way it does on the URL side; nothing about
    how the agents run changes, only when this generator finds out about each
    one.

    Can raise `ValueError` (from `parse_github_url` or `fetch_repo` — not a
    GitHub URL, repo not found, too large, ...) before yielding anything —
    same contract as `run_scan_stream`, the caller (`main.py`) turns it into
    an in-stream `event: failed` since the HTTP response's 200 has already
    committed by the time any event is available to inspect.
    """
    owner, repo, ref = parse_github_url(raw_url)
    start = time.perf_counter()
    agent_results: list[AgentResult] = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        async with fetch_repo(owner, repo, ref, client) as fetched:
            context = RepoContext(
                repo_url=raw_url,
                owner=owner,
                repo=repo,
                ref=fetched.ref,
                root=fetched.root,
                files=list_repo_files(fetched.root),
                client=client,
            )
            coros = [agent_cls().run(context) for agent_cls in AGENTS_REPO]
            for coro in asyncio.as_completed(coros):
                result = await coro
                agent_results.append(result)
                yield ("agent", result)

    report = await _finalize(raw_url, start, agent_results, context.files)
    yield ("done", report)
