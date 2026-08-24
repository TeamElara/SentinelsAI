# 68 — Telling a dead session from a dead backend

> **Status:** done. `frontend/lib/api.ts`'s `handleStreamError`, called from
> both `streamScan` and `streamRepoScan`'s `onerror`. `tsc --noEmit` and
> ESLint clean (same 3 pre-existing errors elsewhere, untouched). No frontend
> test suite exists in this project, so verification was `tsc`/lint plus
> exercising the real logic live in the browser console against both a real
> 401 and a genuinely unreachable backend — which caught a real bug before
> it shipped (below).

## What we built

`streamScan`/`streamRepoScan` open a live `EventSource` connection to
`GET /scan/stream`. If that connection ever drops — backend down, or the
session cookie expired partway through a long scan — `source.onerror` fires
and the UI showed one flat message: "Lost connection to the scanner." Even
when the real reason was "you got signed out," the user saw a generic error
instead of landing back on `/login`, the way every other 401 in this file
already does via `checkAuth`.

## The one big idea: `onerror` throws away the reason

Every other function in `api.ts` calls plain `fetch`, gets back a `Response`,
and can just read `res.status`. `EventSource` doesn't work that way — its
`onerror` fires for *any* connection-level failure and hands you nothing
but the fact that it happened. A 401 and a backend that fell over look
identical from inside the handler.

## A tiny standalone version of the problem

```js
function knock(house) {
  try {
    return house.answerDoor();
  } catch {
    return "no answer"; // could be "nobody's home" or "house burned down"
  }
}
```

`"no answer"` is true either way, and it's useless for deciding what to do
next — call back later, or stop knocking on that house forever. To tell
them apart you have to go check something else: a lit window, a neighbor,
anything with more information than the knock itself gave you.

## The fix: one more cheap knock — that itself can fail the same way

`handleStreamError` is that second check. `fetchMe()` is a plain `fetch`
against `/auth/me` — a `Response`, so it *does* have a status. `null` means
signed out; anything else means the session is still good and the failure
was real. First cut:

```ts
async function handleStreamError(handlers: ScanStreamHandlers): Promise<void> {
  const me = await fetchMe();
  if (me === null && typeof window !== "undefined") {
    window.location.href = "/login";
    return;
  }
  handlers.onError("Lost connection to the scanner.");
}
```

That's the version that reached this session uncommitted. Live-testing it
against a genuinely unreachable backend caught the bug: `fetchMe` has no
`try`/`catch` of its own —

```ts
export async function fetchMe(): Promise<SessionUser | null> {
  const res = await fetch(`${API_BASE}/auth/me`, withAuth());
  if (!res.ok) return null;
  return res.json() as Promise<SessionUser>;
}
```

— and `fetch()` only resolves to a `Response` when the server actually
answers. When it can't even reach the server, `fetch` *throws* a `TypeError`
instead. A real 401 resolves cleanly (`res.ok` is `false`, `fetchMe` returns
`null`); a dead backend throws right out of `fetchMe`, out of
`handleStreamError`, and — since the call site is `void handleStreamError(handlers)`,
never awaited — off into an unhandled rejection nobody catches.
`handlers.onError` never runs. The exact silent-failure case this function
was built to close turned out to still be open, for the more common trigger
of the two.

The fix wraps the fallible call:

```ts
async function handleStreamError(handlers: ScanStreamHandlers): Promise<void> {
  let me: SessionUser | null;
  try {
    me = await fetchMe();
  } catch {
    handlers.onError("Lost connection to the scanner.");
    return;
  }
  if (me === null && typeof window !== "undefined") {
    window.location.href = "/login";
    return;
  }
  handlers.onError("Lost connection to the scanner.");
}
```

## Rejected: reading the cookie directly in the frontend

The session cookie is `httpOnly` (set that way in Stage 0, on purpose, so
JavaScript can't read or forge it). That rules out checking "is there still
a session cookie?" from client code at all; the only honest way to ask is
the same way `fetchMe` always has, by asking the backend — which is exactly
the call that needed its own failure handled.

## Try it

Live-verified in the browser console against the real running app (not a
reimplementation guessed from reading the code):

- **Real 401** — `fetch("http://localhost:8011/auth/me", {credentials:
  "include"})` while signed out returned `{ok: false, status: 401}`, so
  `fetchMe()` resolves `null` cleanly and `handleStreamError` redirects.
- **Unreachable backend** — the pre-fix logic against an unreachable port
  produced zero `onError` calls and the error vanished silently, reproducing
  the bug. The post-fix logic (with the `try`/`catch`) produced exactly one
  `onError("Lost connection to the scanner.")` call and no unhandled
  rejection.
- `npx tsc --noEmit` and `npx eslint lib/api.ts` from `frontend/` — both
  clean.

## Words worth knowing

- **`EventSource`** — the browser API behind Server-Sent Events (one-way,
  server-to-client streaming over plain HTTP). `fetch` gets you a `Response`
  object; `EventSource` gets you named events (`"agent"`, `"done"`) and one
  generic `"error"` with none of `fetch`'s status-code detail.
- **`httpOnly` cookie** — a cookie JavaScript in the page can't read
  (`document.cookie` skips it). The browser still sends it on every request
  automatically; it just can't be inspected or stolen by injected script.
- **Unhandled rejection** — a thrown error inside an `async` function whose
  returned promise nobody `await`s or `.catch()`s. It doesn't crash
  anything; it just vanishes past whatever was supposed to react to it,
  which is exactly what made this bug silent instead of loud.

---

**Next:** one of the deferred PLAN-v5 items — the `unlinkScanRepo()` button
wiring is the smallest.
