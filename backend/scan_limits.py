"""Hard limits on how much scanning work runs at once, and for how long.

Three things, shared by the URL and repo orchestrators:

- `SCAN_DEADLINE_SECONDS` — one overall budget per scan.
- `run_with_deadline` — runs a scan's agents as tasks and guarantees that
  none of them outlives the scan: at the deadline, or when the caller stops
  listening (the browser closed the stream), every unfinished task is
  cancelled *and awaited*.
- `scan_slots` — at most a few scans at once overall, and one per user.

Why cancel-and-await and not `asyncio.wait_for`: `wait_for` stops waiting,
but a task it gives up on has only been *asked* to stop. Awaiting it is what
proves it has stopped — and what lets the caller close the HTTP client those
tasks were using without pulling it out from under one still running. Work
already on a worker thread (the blocking TLS handshake, a DNS lookup) can't
be cancelled at all, which is why each of those carries its own timeout.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Iterator

from models import AgentResult

# Longest a scan's agents may run, start to finish. A normal URL scan takes
# 20-25 s (the subdomain agent dominates), so this only ever ends a scan
# that is stuck.
SCAN_DEADLINE_SECONDS = 90.0

# The free hosting tier has one small instance; each scan holds sockets,
# worker threads and, for repos, an extracted archive.
MAX_CONCURRENT_SCANS = 3
MAX_SCANS_PER_USER = 1


class ScanBusy(ValueError):
    """No slot for this scan right now.

    A `ValueError` so it takes the same route to the user as every other
    refused scan (a 400, or an in-stream `failed` event) with its own
    message, without the routes needing to know about it.
    """


class ScanSlots:
    """Counts running scans overall and per user.

    No lock: `hold` never awaits between its check and its increment, and
    the server is a single event loop, so nothing can run in between.
    """

    def __init__(self, total: int = MAX_CONCURRENT_SCANS, per_user: int = MAX_SCANS_PER_USER) -> None:
        self._total = total
        self._per_user = per_user
        self._running = 0
        self._by_user: dict[int, int] = {}

    @property
    def running(self) -> int:
        return self._running

    @contextlib.contextmanager
    def hold(self, user_id: int | None) -> Iterator[None]:
        """Hold one slot for the duration of the `with` block.

        Raises `ScanBusy` instead of queueing — a scan that waited behind
        others would start minutes after the user asked for it, with
        nothing on screen to say why.
        """
        if user_id is not None and self._by_user.get(user_id, 0) >= self._per_user:
            raise ScanBusy("You already have a scan running. Wait for it to finish, then try again.")
        if self._running >= self._total:
            raise ScanBusy("Sentinels is running as many scans as it can right now. Try again in a minute.")

        self._running += 1
        if user_id is not None:
            self._by_user[user_id] = self._by_user.get(user_id, 0) + 1
        try:
            yield
        finally:
            self._running -= 1
            if user_id is not None:
                self._by_user[user_id] -= 1
                if not self._by_user[user_id]:
                    del self._by_user[user_id]


scan_slots = ScanSlots()


async def cancel_and_wait(tasks: "set[asyncio.Task] | list[asyncio.Task]") -> None:
    """Cancel every task and wait until each has actually finished."""
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_with_deadline(
    runs: list[tuple[str, Awaitable[AgentResult]]],
    deadline: float,
) -> AsyncIterator[AgentResult]:
    """Run every agent at once; yield each `AgentResult` as it finishes.

    `runs` is `(agent name, agent.run(context))` pairs and `deadline` is a
    `loop.time()` value. Whatever is still running at the deadline is
    cancelled and reported as a failed agent, in the order given, so a
    timed-out check shows up as "did not complete" instead of going missing.

    If the caller stops early — it closed this generator, or was itself
    cancelled because the browser went away — the `finally` cancels the
    rest. Either way, no task is left running when this generator ends.
    """
    loop = asyncio.get_running_loop()
    started = time.perf_counter()
    names = {asyncio.ensure_future(run): name for name, run in runs}
    order = list(names)
    pending = set(order)
    try:
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            for task in sorted(done, key=order.index):
                yield task.result()

        timed_out = [task for task in order if task in pending]
        await cancel_and_wait(timed_out)
        pending = set()
        for task in timed_out:
            yield AgentResult(
                agent=names[task],
                findings=[],
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=(
                    f"TimeoutError: stopped at the scan's {SCAN_DEADLINE_SECONDS:.0f}-second "
                    "deadline before this check finished"
                ),
            )
    finally:
        await cancel_and_wait(pending)
