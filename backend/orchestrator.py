"""Coordinates one scan: normalize the URL, run every agent, build a ScanReport.

All five agents run concurrently via `asyncio.gather` — the whole scan takes
about as long as the slowest single agent, not the sum of all five. Adding a
sixth agent later means one more line in `AGENTS` below; nothing else here
changes for it.

Two ways to run a scan, sharing the same agents and the same final report
logic (`_finalize`), differing only in *when the caller finds out about each
result*:

- `run_scan` — used by `POST /scan`. Waits for all five, returns one
  complete `ScanReport`. Simple; the right shape for `curl` and any client
  that just wants an answer.
- `run_scan_stream` — used by `GET /scan/stream` (A16). Yields each
  `AgentResult` the instant it finishes, in real completion order, then a
  final `ScanReport` once everything's done. Same concurrency as
  `run_scan` — every agent is still started at once — the only difference
  is the caller stops having to wait for the slowest agent before hearing
  about the fastest one.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from agents.base import ScanContext
from agents.registry import AGENTS
from ai.analyst import summarize
from checklist.evaluator import compute_readiness, evaluate
from models import AgentResult, ScanReport
from scoring import calculate_score, count_by_severity, grade_for_score
from storage.scans import save_scan

# Matches ANY "word://" prefix (http, https, ftp, javascript, ...) — not just
# http/https. We need to detect "a scheme is already present" in general, so
# we only prepend https:// when there's truly none; a URL with an unsupported
# scheme must fall through to the explicit rejection below instead of being
# mangled into "https://ftp://example.com".
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def normalize_url(raw: str) -> str:
    """Turn whatever a user typed into one canonical https://host/path form.

    "example.com", "EXAMPLE.com/", and "https://example.com" should all become
    the same string, so the same target isn't scanned differently depending on
    how it was typed. Raises ValueError on anything that isn't a usable
    http(s) URL.
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("URL is empty")

    if not _SCHEME_RE.match(raw):
        raw = f"https://{raw}"

    parsed = urlsplit(raw)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    if not parsed.netloc:
        raise ValueError("URL has no host")

    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        parsed.query,
        "",       # fragments never leave the browser, so they're dropped here
    ))


async def _check_reachable(url: str, client: httpx.AsyncClient) -> None:
    """Fail fast, with one clear message, if the site can't be reached at all.

    Without this, a typo'd or nonexistent domain still "completes": every
    agent independently hits the same DNS/connection failure, catches it
    under BaseAgent.run's crash-proofing contract, and the report comes back
    looking like a clean scan (no findings, since nothing ever ran) instead
    of an honest "this site doesn't exist." One request here, before any
    agent starts, turns that into the same rejected-URL path normalize_url
    already uses — a ValueError the frontend shows as "Inspection failed."
    """
    try:
        await client.get(url, timeout=10.0)
    except httpx.TransportError as exc:
        host = urlsplit(url).netloc
        raise ValueError(
            f"Couldn't reach {host}. Check the address — the site may not "
            "exist or isn't responding right now."
        ) from exc


async def _finalize(
    url: str, start: float, agent_results: list[AgentResult], subdomains: list | None = None
) -> ScanReport:
    """Shared by both run functions: turn finished agent results into a report.

    `start` is a `time.perf_counter()` reading taken before any agent ran.
    `duration_ms` is measured right here, before the AI summary call below —
    unchanged from the original `run_scan` — so it reports how long the five
    agents took (the number `AgentLog`'s "less than the sum, because
    concurrent" claim is about), not the summary call on top of it.

    Persists the finished report to SQLite before returning — every caller
    (`run_scan` and `run_scan_stream`) goes through this one function, so
    this is the single place a scan becomes durable.
    """
    findings = [finding for result in agent_results for finding in result.findings]
    duration_ms = int((time.perf_counter() - start) * 1000)

    score = calculate_score(findings, url)
    grade = grade_for_score(score)
    summary = await summarize(url, score, grade, findings)

    checklist = evaluate(findings)
    readiness_score, deployment_status = compute_readiness(checklist)

    report = ScanReport(
        id=str(uuid.uuid4()),
        url=url,
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
        subdomains=subdomains or [],
    )
    save_scan(report)
    return report


async def run_scan(raw_url: str) -> ScanReport:
    """Normalize the URL, run every agent against it, assemble the report."""
    url = normalize_url(raw_url)
    start = time.perf_counter()

    async with httpx.AsyncClient(timeout=10.0) as client:
        await _check_reachable(url, client)
        context = ScanContext(url=url, client=client)
        agent_results = await asyncio.gather(
            *(agent_cls().run(context) for agent_cls in AGENTS)
        )

    return await _finalize(url, start, list(agent_results), context.shared.get("subdomains"))


async def run_scan_stream(
    raw_url: str,
) -> AsyncIterator[tuple[str, AgentResult | ScanReport]]:
    """Like `run_scan`, but yields progress instead of making the caller wait.

    Yields `("agent", AgentResult)` once per agent, in real completion
    order — not `AGENTS`' declared order, and not always the same order
    twice, since it depends on real network timing — followed by exactly
    one `("done", ScanReport)` once all five are in.

    `asyncio.as_completed` is what makes this different from `run_scan`'s
    `asyncio.gather`: `gather` hands back one list only once every coroutine
    is done, while `as_completed` hands back each coroutine's result as
    *that one* finishes, still running every coroutine concurrently the
    whole time. Nothing about how the agents run changes — only when this
    function finds out about each one.

    Can raise `ValueError` (from `normalize_url`, or from `_check_reachable`
    if the host can't be reached at all) before yielding anything at all —
    the caller (`main.py`) is responsible for turning that into an in-stream
    event, since by the time this generator has produced its first item the
    HTTP response has already committed to status 200.
    """
    url = normalize_url(raw_url)
    start = time.perf_counter()
    agent_results: list[AgentResult] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        await _check_reachable(url, client)
        context = ScanContext(url=url, client=client)
        coros = [agent_cls().run(context) for agent_cls in AGENTS]
        for coro in asyncio.as_completed(coros):
            result = await coro
            agent_results.append(result)
            yield ("agent", result)

    report = await _finalize(url, start, agent_results, context.shared.get("subdomains"))
    yield ("done", report)
