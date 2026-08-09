# 48 — One fetch cache for eight agents

> **Status:** done. `backend/agents/probe.py` is new; `ScanContext` grew two
> fields, both defaulted, so the five existing agents run identically to
> before — a live scan of `example.com` still scores exactly 54/F.

## What we built

Three new agents are coming (V4-V6), and all three want to look at things
like the homepage or `robots.txt`. Left alone, that's the same URL fetched
four times over by four different agents running at once. `probe.py` gives
every agent, old or new, four small tools instead:

- **`ResponseCache`** — ask for a URL; if another agent is already fetching
  that exact URL right now, you get *their* answer instead of starting a
  second request.
- **`RobotsGate`** — "is this path OK to fetch?", checked against
  `robots.txt`, fetched once.
- **`Budget`** — a hard stop: at most N requests, at most X seconds, for one
  agent's probing. No check can loop forever.
- **`safe_get` / `safe_head` / `safe_options`** — one request, with network
  failures turned into `None` instead of an exception.

Nothing about the five existing agents changed. `ScanContext` gained
`cache` and `shared`, both with defaults — an agent that never mentions them
behaves exactly as it did yesterday.

## New concept: a coroutine is a plan, a Task is that plan already running

This is the one worth sitting with, because it's the actual reason the cache
works.

```python
import asyncio

async def slow_double(x):
    print(f"starting work on {x}")
    await asyncio.sleep(1)
    return x * 2

async def main():
    plan = slow_double(5)        # <- nothing has run yet. `plan` is a coroutine object.
    print("plan created, but 'starting work on 5' hasn't printed")
    result = await plan          # <- NOW it runs, and we wait for it
    print(result)

asyncio.run(main())
```

Calling `slow_double(5)` does not run the function. It builds a *coroutine
object* — a paused function, like a recipe that hasn't been cooked yet.
Nothing happens until something `await`s it. And a coroutine can only be
awaited **once**: awaiting the same one twice raises `RuntimeError: cannot
reuse already awaited coroutine`. That's the trap for our cache — if two
agents both had the same coroutine object, only one of them could actually
await it.

A **Task** is different: `asyncio.ensure_future(coroutine)` hands that
coroutine to the event loop and says "start running this now, in the
background, whether or not anyone's watching." The Task object is a
handle you can `await` — and unlike a coroutine, you can await the *same*
Task from as many places as you like. Each `await` just waits for the one
underlying run to finish and hands back the one result:

```python
async def main():
    task = asyncio.ensure_future(slow_double(5))   # starts running immediately
    print(await task)   # 10
    print(await task)   # 10 again — no error, no second run
```

That's exactly what `ResponseCache` does. The first agent to ask for a URL
gets a fresh Task created and stored under that URL. Every agent asking for
the *same* URL afterwards — even one that arrives while the first fetch is
still in the air — gets handed that same Task and just waits for it too.
One request, several waiters.

## The actual code

`ResponseCache.get()` ([`backend/agents/probe.py`](../../backend/agents/probe.py)):

```python
async def get(self, client, url, *, method="GET", follow_redirects=True, timeout=5.0):
    key = (method, url, follow_redirects)
    async with self._lock:
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.ensure_future(
                client.request(method, url, follow_redirects=follow_redirects, timeout=timeout)
            )
            self._tasks[key] = task
    return await task
```

Why the `asyncio.Lock`? "Check the dict, then maybe create a task" is two
steps. Without the lock, two agents could both run the "check" step before
either had finished the "create" step, and both would decide they're first
and start their own fetch — the exact duplication the cache exists to
prevent. The lock makes those two steps atomic: whoever gets there first
finishes registering their Task before the second agent's check even runs
— so the second agent's check always finds the first agent's Task already
there.
Note the lock only guards the bookkeeping — `return await task` happens
*outside* the `async with` block, so agent B isn't holding the lock hostage
for the whole network round trip, only for the instant it took to look the
key up.

`RobotsGate` reuses the cache for its own fetch (`safe_get`), so an agent
that calls `robots_gate.load(context)` after `recon` already fetched
`robots.txt` this scan gets it for free — no second request. It's parsed
with `urllib.robotparser.RobotFileParser`, which is stdlib: `.parse(lines)`
just reads lines already in memory, no network call of its own.

`Budget` is plainer — a counter and a `time.monotonic()` reading, checked
before every probe:

```python
def allow(self) -> bool:
    if self._used >= self.max_requests or (time.monotonic() - self._start) >= self.deadline_seconds:
        self.partial = True
        return False
    self._used += 1
    return True
```

(`time.monotonic()` rather than `time.time()` because it can never jump
backwards — a clock adjustment mid-scan can't make a budget think negative
time has passed.) The `semaphore = asyncio.Semaphore(4)` alongside it is
separate: `Budget.allow()` caps the *total* count over the agent's whole
run; the semaphore caps how many of those requests are *in flight at once*
— at most 4, so a 16-path probe loop doesn't fire all 16 simultaneously.

## Try it

Run the coroutine-vs-Task example above yourself and change one thing:
replace `task = asyncio.ensure_future(slow_double(5))` with
`task = slow_double(5)` (a bare coroutine, no Task) and try `await task`
twice. It'll crash with `cannot reuse already awaited coroutine` on the
second line — that's the exact bug the cache avoids by always wrapping in
`ensure_future`.

Ask two "agents" for the same URL concurrently and watch the transport get
hit once:

```python
import asyncio, httpx
from agents.base import ScanContext
from agents.probe import ResponseCache

async def main():
    hits = 0
    async def handler(request):
        nonlocal hits
        hits += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, text="ok")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ctx = ScanContext(url="https://example.com", client=client)
        await asyncio.gather(
            ctx.cache.get(client, "https://example.com/same"),
            ctx.cache.get(client, "https://example.com/same"),
        )
    print(hits)  # 1

asyncio.run(main())
```

## Words worth knowing

- **Coroutine** — a paused function created by calling an `async def`
  function; does nothing until awaited, and can only be awaited once.
- **Task** — a coroutine handed to the event loop to run in the background;
  awaitable any number of times, always returning the same result.
- **`asyncio.ensure_future`** — turns a coroutine into a running Task.
- **Race condition** — a bug where the outcome depends on which of two
  things happens first; the reason the cache needs a lock around its
  check-then-create step.
- **Semaphore** — a counter that lets at most N things through at once;
  used here to cap *concurrent* requests, separately from the budget's cap
  on *total* requests.

---

**Next:** V3 — scoring gains deduplication, so the same underlying problem
seen by two of the eight agents costs the score once, not twice.
