# Frontend error reporting (Sentry): design

Status: proposal, 9 October 2026. Nothing in this document is built. It is the
frontend half of launch-plan gate 5 ("Sentry with active redaction"); the backend
half is [PR #40](https://github.com/TeamElara/SentinelsAI/pull/40)
(`backend/observability.py`).

## What we want, and what we must not send

We want to know when a beta user hits a crash in the browser: an unhandled
exception, a rejected promise, or a React render failure on a page. That is all.

The frontend sees more sensitive data than the backend does, so the rules are
stricter. Facts about this code that shape the design:

| Fact (where) | Risk if Sentry's defaults are on |
| --- | --- |
| `lib/scan-stream.ts` builds `…/scan/stream?url=<scanned site>&request_id=…` | Default fetch/XHR breadcrumbs and the `request.url` field would carry the scanned site. |
| Report pages render findings, evidence excerpts and repo file contents into the DOM | Session Replay and DOM-based breadcrumbs would copy them. |
| `lib/api.ts` throws errors built from the backend's `detail` text, which can name the scanned host ("not allowed: …") | Exception messages would carry the scanned site. |
| `connect-src 'self'` in `next.config.ts` (C11) | A browser SDK posting to `*.ingest.sentry.io` is blocked, or tempts us to loosen the CSP. |
| `app/` has no `error.tsx` or `global-error.tsx` | Render crashes today show Next's default page and are reported nowhere. |
| Pages live at `/scan/<uuid>/…` | The URL identifies one user's scan. |

## Decisions

**1. Use `@sentry/browser` only, not `@sentry/nextjs`.** `@sentry/nextjs` also
instruments Next's server and edge code, wraps the build with `withSentryConfig`,
and injects its own tunnel and rewrites. Our Next server only relays `/api/*` to
Render and runs two small redirect routes; Vercel's logs already cover those. A
browser-only SDK has a far smaller surface to audit. Initialise it from
`frontend/instrumentation-client.ts`, which Next 16 loads before the app starts.

**2. Allowlist integrations; do not start from defaults and subtract.**
Keep only global error handlers (`onerror`, `unhandledrejection`), `dedupe`,
`linkedErrors`, `inboundFilters` and `functionToString`. Leave out breadcrumbs,
`httpContext` (adds URL, referrer and user agent), browser tracing, Replay,
User Feedback, and Logs. Set `sendDefaultPii: false`, `tracesSampleRate: 0`,
`maxBreadcrumbs: 0`, `attachStacktrace: false`. A future SDK default then cannot
switch on a collector we did not choose.

**3. Send through our own origin, not to Sentry directly.** Set the SDK's
`tunnel` option to `/monitoring` and add a rewrite in `next.config.ts`:

```ts
// sketch; destination parsed from NEXT_PUBLIC_SENTRY_DSN at build time
{ source: "/monitoring", destination: "https://<ingest-host>/api/<project-id>/envelope/" }
```

`connect-src 'self'` stays exactly as it is, so C11's header evidence is still
true, and ad-blockers that block `ingest.sentry.io` do not hide crashes. The
destination is fixed, so this is not an open relay. A third party can post junk
to `/monitoring`, but the DSN is public by design and they could post to Sentry
directly anyway; Sentry's rate limits and inbound filters are the control. It
costs Vercel proxy requests, not function invocations.

**4. Scrub in `beforeSend`, in a pure function with tests.** New file
`frontend/lib/sentry-scrub.ts`, same rules as the backend:

- Keep: exception type, stack frames of our own bundle, the route.
- Route: take the path only (no query, no hash) and replace a scan id with
  `:id`, so `/scan/3f2a…/files` becomes `/scan/:id/files`.
- Replace in `message` and exception values: URLs, bare hostnames, IPv4/IPv6,
  emails, tokens (same patterns as `backend/observability.py`).
- Drop: `request.headers`, cookies, `user`, `breadcrumbs`, `extra`, and every
  `contexts` entry except browser name and version.

**5. Report only what is a bug.** Our own `ApiError` with a 4xx status is
expected (bad URL, quota, not signed in) and is the user's input, so it is
dropped in `beforeSend`. `allowUrls` is set to our own origin, so errors thrown
by browser extensions and injected scripts never arrive. A per-session cap
(10 events) stops an error loop from flooding the quota.

**6. Add the missing error boundaries.** `app/error.tsx` and
`app/global-error.tsx` show a plain "Something went wrong" page and call one
helper, `reportError(error)`, which sends the error through the same scrub. They
send `error.digest` and nothing from React's `componentStack`.

**7. One env var.** `NEXT_PUBLIC_SENTRY_DSN`. Unset means the SDK is never
imported and nothing is sent, matching the backend. (`NEXT_PUBLIC_` is
inlined at build time, so a DSN change needs a Vercel redeploy.)

**8. Release tag shared with the backend.** `release` is
`VERCEL_GIT_COMMIT_SHA` on the frontend, and the same value goes in the backend's
`SENTRY_RELEASE`, so one deploy lines up across both projects.

**9. Source maps are phase 2.** Without them a production stack trace points at
minified chunks. Phase 2 uploads maps with `sentry-cli` during the Vercel build,
using `SENTRY_AUTH_TOKEN` as a build-time secret (never `NEXT_PUBLIC_`), and
keeps `productionBrowserSourceMaps` off so the maps are not also served. The repo
is public, so the maps expose nothing the repo does not.

## Alternatives rejected

| Option | Why not |
| --- | --- |
| `@sentry/nextjs` with defaults | Larger instrumented surface, turns on breadcrumbs and request capture we would then have to remove. |
| Direct to Sentry ingest, add it to `connect-src` | Loosens the CSP we just verified, and ad-blockers drop the events. |
| Session Replay | Copies findings, evidence and repo contents from the DOM. Not compatible with this app's data. |
| Skip frontend reporting | Acceptable fallback for the first invited week if we can't spare the work; the cost is that browser crashes surface only when someone tells us. |

## Tests the implementation must ship with

1. `node --test` unit tests for `sentry-scrub.ts` (the repo's runner, as in
   `tests/scan-stream.test.mjs`): a scanned URL, token, IP and scan id planted in
   every field of a sample event come out removed; a 4xx `ApiError` is dropped;
   the 11th event in a session is dropped.
2. A build check with the DSN pointed at a local capture server through the
   `/monitoring` rewrite: throw in a page, assert the envelope body contains none
   of the planted strings and no `breadcrumbs`, `request.headers` or `user`.
3. Re-run the C11 header check: the CSP string is byte-identical to before.
4. With `NEXT_PUBLIC_SENTRY_DSN` unset, the production bundle contains no
   Sentry code and the browser makes no `/monitoring` request.

## Other changes this needs

- `app/privacy/page.tsx`: add Sentry to the processors list (what is sent:
  error type, the page route, the browser name and version; not scan targets).
  This is the launch plan's requirement that privacy name every processor.
- `docs/DEPLOY.md`: `NEXT_PUBLIC_SENTRY_DSN` for Vercel.
- Backend follow-up: `backend/observability.py` keeps the raw request path, which
  includes the scan id. Normalising it to `/scans/:id/…` would match this design.

## Questions for the owner

1. Sentry region (US or EU)? It decides the ingest host and the wording on the
   privacy page.
2. Source maps in phase 1, or accept unreadable stacks for the first week?
3. One Sentry project for backend and frontend, or two?
   Two keeps quotas and alerts separate; one is simpler.
