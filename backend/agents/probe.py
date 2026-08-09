"""Shared probing machinery for agents that fetch several paths per scan.

Three new agents (API Security, Misconfiguration, Subdomain — V4-V6) each
want to fetch things like the homepage or robots.txt. Without this module
they'd each fetch those independently, quadrupling requests to the same
paths. Everything here is opt-in: an agent that never touches
`context.cache` or `context.shared` behaves exactly as before.

- `ResponseCache` — memoizes one in-flight-or-finished request per
  (method, url, follow_redirects), so concurrent agents asking for the same
  thing share one request instead of issuing two.
- `RobotsGate` — fetches robots.txt once (through the cache) and answers
  "is this path OK to fetch" for `User-agent: *`.
- `Budget` — a hard cap on how many requests and how much wall-clock time
  one agent may spend probing, so no check can loop unboundedly.
- `safe_get` / `safe_head` / `safe_options` — one request each, network
  errors turned into `None` instead of an exception.
"""
from __future__ import annotations

import asyncio
import ssl
import time
import urllib.robotparser
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx

if TYPE_CHECKING:
    from agents.base import ScanContext

# Errors a single dead probe can raise. None of these should ever end an
# agent's whole run — a timed-out or refused path is just one missing data
# point, reported as such, not a reason to abort everything else the agent
# was checking.
_PROBE_ERRORS = (httpx.HTTPError, asyncio.TimeoutError, ssl.SSLError)


class ResponseCache:
    """Memoizes responses so two agents fetching the same URL at the same
    moment share one request instead of making two.

    The cache stores the `asyncio.Task` doing the fetch, not just its
    eventual result. That distinction is the whole point: a coroutine is
    just a paused function — asking for its result twice runs it twice. A
    task is a coroutine handed to the event loop to run *now*, in the
    background, with its result cached on the task object once it finishes.
    Two callers awaiting the *same* task both simply wait for that one
    fetch and both receive its one result — no re-request, no race.
    """

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str, bool], asyncio.Task[httpx.Response]] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        method: str = "GET",
        follow_redirects: bool = True,
        timeout: float = 5.0,
    ) -> httpx.Response:
        """Fetch `url`, or await the in-flight/finished fetch already doing so.

        Raises whatever `httpx` raises on failure — callers that want a
        crash-proof fetch should go through `safe_get`/`safe_head`/
        `safe_options` below instead of calling this directly.
        """
        key = (method, url, follow_redirects)
        # The lock guards only "check the dict, maybe create a task" — a
        # handful of instructions — never the fetch itself. Without it, two
        # callers could both see "no task yet" before either has registered
        # one, and each would start its own fetch anyway.
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.ensure_future(
                    client.request(method, url, follow_redirects=follow_redirects, timeout=timeout)
                )
                self._tasks[key] = task
        return await task


async def safe_get(
    context: "ScanContext", url: str, *, follow_redirects: bool = True, timeout: float = 5.0
) -> httpx.Response | None:
    """GET through the shared cache; `None` instead of raising on failure."""
    return await _safe_request(context, "GET", url, follow_redirects=follow_redirects, timeout=timeout)


async def safe_head(
    context: "ScanContext", url: str, *, follow_redirects: bool = True, timeout: float = 5.0
) -> httpx.Response | None:
    """HEAD through the shared cache; `None` instead of raising on failure."""
    return await _safe_request(context, "HEAD", url, follow_redirects=follow_redirects, timeout=timeout)


async def safe_options(
    context: "ScanContext", url: str, *, follow_redirects: bool = True, timeout: float = 5.0
) -> httpx.Response | None:
    """OPTIONS through the shared cache; `None` instead of raising on failure."""
    return await _safe_request(context, "OPTIONS", url, follow_redirects=follow_redirects, timeout=timeout)


async def _safe_request(
    context: "ScanContext", method: str, url: str, *, follow_redirects: bool, timeout: float
) -> httpx.Response | None:
    try:
        return await context.cache.get(
            context.client, url, method=method, follow_redirects=follow_redirects, timeout=timeout
        )
    except _PROBE_ERRORS:
        return None


class RobotsGate:
    """Answers "is this path OK to fetch" against the target's robots.txt.

    Fetched once, through the shared cache (so a `recon`-style agent that
    already fetches robots.txt doesn't cause a second request), and parsed
    with the standard library's own parser — `RobotFileParser.parse()` just
    reads lines already in memory, no network call of its own.
    """

    def __init__(self) -> None:
        self._parser: urllib.robotparser.RobotFileParser | None = None
        self._skipped: list[str] = []

    async def load(self, context: "ScanContext") -> None:
        """Fetch and parse robots.txt. Safe to call more than once — only
        the first call does any work."""
        if self._parser is not None:
            return
        robots_url = urljoin(context.url, "/robots.txt")
        response = await safe_get(context, robots_url)
        parser = urllib.robotparser.RobotFileParser()
        # No robots.txt, or a fetch that failed outright: nothing tells us
        # not to fetch a path, so everything is allowed — the same default
        # a real crawler applies.
        parser.parse(response.text.splitlines() if response and response.status_code == 200 else [])
        self._parser = parser

    def allowed(self, path: str) -> bool:
        """True if `User-agent: *` may fetch `path`. Call `load()` first;
        before that, fails open (allowed) rather than blocking every probe
        on a gate nobody set up."""
        if self._parser is None:
            return True
        ok = self._parser.can_fetch("*", path)
        if not ok:
            self._skipped.append(path)
        return ok

    @property
    def skipped_paths(self) -> list[str]:
        """Paths this gate refused, in the order they were checked — an
        agent should surface this list as evidence, not drop it silently."""
        return list(self._skipped)


class Budget:
    """A hard cap on one agent's probing: at most `max_requests` requests,
    within `deadline_seconds` of wall-clock time, at most 4 in flight at
    once (`semaphore`).

    Not a rate limiter — Sentinels never needs to go *slower*, only to
    guarantee it stops. Use it as:

    ```python
    for path in candidate_paths:
        if not budget.allow():
            break
        async with budget.semaphore:
            response = await safe_head(context, urljoin(context.url, path))
    ```

    `allow()` returning `False` once sets `partial` for the rest of the
    agent's run — a budget doesn't reset mid-scan. The agent is expected to
    read `budget.partial` and say so in its evidence ("stopped after N
    requests — results may be incomplete"); that's honest, where silently
    truncating the list would not be.
    """

    def __init__(self, max_requests: int, deadline_seconds: float) -> None:
        self.max_requests = max_requests
        self.deadline_seconds = deadline_seconds
        self.semaphore = asyncio.Semaphore(4)
        self.partial = False
        self._used = 0
        self._start = time.monotonic()

    def allow(self) -> bool:
        """Reserve capacity for one more request. Returns False (and sets
        `partial`) once the count or the deadline is exhausted."""
        if self._used >= self.max_requests or (time.monotonic() - self._start) >= self.deadline_seconds:
            self.partial = True
            return False
        self._used += 1
        return True
