# 69 — Wiring the unlink button

> **Status:** done. `frontend/components/fixes/FixPlanPanel.tsx` gains
> `UnlinkRepoLine`, wired to the `unlinkScanRepo()` call that's existed in
> `lib/api.ts` since Stage D but never had a button. `tsc --noEmit` and
> ESLint clean. No live click-through: everything under `/scan/[id]` needs a
> signed-in GitHub session (`current_user` on every route, checked in
> `backend/main.py`), and this environment doesn't have one to sign in
> with — see "Try it" for exactly what was and wasn't verified.

## What we built

Stage D built the whole bridge — link a URL scan to the repo that serves
it, so a header finding has somewhere to patch — but only ever wrote the
"link" half of the round trip. `unlinkScanRepo()` and `fetchScanRepoLink()`
sat in `lib/api.ts` fully implemented, matching the backend's `DELETE` and
`GET /scans/{id}/link-repo` exactly, called by nothing. `FixPlanPanel` now
shows "Linked to owner/repo · Unlink repository" once a check has confirmed
a link exists, in both the "here's your fix" and "no fix for this one"
outcomes.

## The one big idea: local state can say something the server no longer agrees with

`FixPlanPanel` already held a `plan` in React state from the last time it
called `check()`. Once you click Unlink, that plan describes a repository
that, from the server's point of view, this scan isn't linked to anymore.
Nothing forces the UI to notice — it'll happily keep showing "Fix
available" with a live "Open pull request" button underneath it, pointed at
a link that no longer exists, until something makes it ask again.

## A tiny standalone version of the problem

```js
let lightIsOn = true; // what the switch on the wall shows

function flipSwitchElsewhere() {
  // someone unplugs the lamp from the other room
  // lightIsOn never finds out
}
```

`lightIsOn` isn't wrong because of a bug — it's wrong because nothing ever
told it the underlying thing changed. The fix is never "make the variable
smarter"; it's "make the action that changes the real thing also update (or
throw away) the copy that claims to describe it."

## The fix: unlink resets state, it doesn't just remove a repo name

```ts
async function unlink() {
  setUnlinking(true);
  setUnlinkError(null);
  try {
    await unlinkScanRepo(scanId);
    setRepoLink(null);
    setState({ kind: "idle" });   // <- the important line
  } catch (err) {
    setUnlinkError(err instanceof Error ? err.message : "Couldn't unlink that repository.");
  } finally {
    setUnlinking(false);
  }
}
```

Dropping back to `{ kind: "idle" }` throws away the stale plan along with
the repo name. The next "Check for automatic fix →" click re-asks the
server from scratch, and — since there's genuinely no link anymore — lands
back on the same `needs-link` form `check()` already knew how to show.
Nothing new needed inventing there; unlinking just re-opens a door that
Stage D had already built.

## Rejected: fetching the link status the moment the panel mounts

`fetchScanRepoLink` is a cheap local DB read (confirmed by reading the
backend route — no GitHub call, just `get_scan_repo_link`), so eagerly
calling it for every `FixPlanPanel` instance the moment it mounts wouldn't
cost much. It was rejected anyway: a findings list can mount many of these
panels at once, and this component's whole existing design (see the file's
own header comment) is manual-trigger — nothing here fetches until the user
clicks "Check for automatic fix." Firing a background request the instant
the page loads, just for this one panel, would be the one exception to a
rule the rest of the file follows on purpose. Fetching the link status
inside `check()` instead — right after confirming a plan came back, success
or not — means it only ever happens on the same click the user already made.

## Try it

- `npx tsc --noEmit` and `npx eslint components/fixes/FixPlanPanel.tsx` from
  `frontend/` — both clean.
- Read `backend/main.py`'s `scan_unlink_repo` (`DELETE
  /scans/{scan_id}/link-repo`, line ~428) against `unlinkScanRepo` in
  `lib/api.ts` — same path, same method, 404 mapped to the same
  `raiseApiError` every other write in the file already uses.
- **Not done this pass:** clicking the real button. `GET /scans/{id}` (and
  every route under it) requires a signed-in session; this environment has
  no GitHub account to complete that OAuth flow with, so the actual
  click-Unlink-see-it-reset loop needs a live pass from a real session —
  the same boundary noted in [68](68-telling-a-dead-session-from-a-dead-backend.md).

## Words worth knowing

- **Stale state** — a copy of some fact, held in memory, that used to be
  true and silently stopped being true when the real thing changed
  elsewhere. The `lightIsOn` example above is the smallest version of it;
  `plan` after an unlink is this codebase's version.
- **Idempotent-ish DELETE** — `DELETE /link-repo` returns 404 if there was
  nothing to unlink rather than silently succeeding twice. Not fully
  idempotent (a true idempotent DELETE would return 204 either way), but
  the same shape as every other "this thing must already exist" check in
  this backend (see invariant #4's ownership checks).

---

**Next:** the two remaining deferred PLAN-v5 fixers
(`remediation/dependencies.py`, `remediation/secrets.py`) or the
`netlify.toml`/`nginx.conf` header-fixer extension — all three still need
their own scoping pass before any code.
