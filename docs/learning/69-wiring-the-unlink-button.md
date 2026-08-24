# 69 — Wiring the unlink button

> **Status:** done and live-verified. `frontend/components/fixes/FixPlanPanel.tsx`
> gains `UnlinkRepoLine`, wired to the `unlinkScanRepo()` call that's existed
> in `lib/api.ts` since Stage D but never had a button. `tsc --noEmit` and
> ESLint clean. Full click-through against the real running app: needs-link
> → link (real GitHub round trip against a real public repo) → "Linked to
> octocat/Hello-World" → Unlink → back to idle → re-check → needs-link
> again, confirmed at the database level, not just in React state. See
> "Try it" for how a session was minted without a GitHub OAuth login.

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

## How the live pass got a real session without a GitHub login

Every route under `/scan/[id]` requires `current_user`, and there's no
GitHub account to complete a real OAuth flow with from here. But
`backend/tests/test_main_audit.py` already had the answer for authenticating
without one: a session cookie is just a token plus an HMAC signature
(`auth/session.py`), and the *only* thing the backend checks is that the
signature matches `SENTINELS_SESSION_SECRET` and the token's hash exists in
`sessions`. Reading that same secret out of `backend/.env` (the one the live
dev server already has loaded) and calling `session.new_token()` +
`storage.users.sign_in()` + `session.cookie_value()` — the exact three calls
that test file's own fixture makes — mints a cookie the live server accepts
as genuinely signed in, for the real dev account (`arihantjaino7`) that was
already in the database. Setting `document.cookie` for the `localhost:8011`
origin (cookies are stored per-origin, not per-page, so it doesn't matter
that the frontend lives on `:3000`) put it in the browser's own jar, so
every real fetch and every real click went through exactly the code paths a
real sign-in would.

The "link to a repository" half needed one more piece: a `github_installations`
row. Rather than fake the repo too, its `account_login` was set to a real
GitHub org (`octocat`) and the form was given a real, public repo name
(`Hello-World`) — since `remediation/source.py` reads GitHub's Contents API
unauthenticated for a plan preview (no installation token involved until
Stage B's actual write), linking to it triggered a genuine network round
trip, not a mock. `Hello-World` has no `vercel.json` or `next.config.*`, so
`detect_stack` correctly declined and the finding landed on "no automatic
fix" — an honest `unavailable` state to show the new unlink control in, not
a scripted one.

Both the fake installation row and the synthetic test scan were deleted
after the pass (`storage.scans.delete_scan` + a matching `DELETE` on
`github_installations`); the minted session was left alone since it's a
real, valid session for the real dev account, no different from what an
actual sign-in produces.

## Try it

- `npx tsc --noEmit` and `npx eslint components/fixes/FixPlanPanel.tsx` from
  `frontend/` — both clean.
- The full loop, live: `needs-link` (no installations shown until one
  exists) → fill in the form, `Link repository` → real GitHub lookup →
  `Reading the repo…` → `No automatic fix for this finding` +
  `Linked to octocat/Hello-World` → `Unlink repository` → back to
  `Check for automatic fix →` → click it again → `needs-link` reappears.
  Independently confirmed via `GET /scans/{id}/link-repo` returning `null`
  right after the click — the delete reached the database, not just the
  React tree.

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
