# C11 — direct header and browser evidence

The Track C stack reuses the header implementation from PR #13 (commit
06e9aba), rather than creating a competing policy. `headers()` applies CSP,
HSTS, nosniff, Referrer-Policy, X-Frame-Options and Permissions-Policy to all
frontend paths. HTTPS deployments use same-origin `/api` connections; local
production builds permit the configured API origin.

On 8 October 2026, a Node 24/Next 16.3.8 production build served the complete
header set on `/`, `/login`, `/url`, `/repo`, `/settings`, a missing page and a
missing `/api` path. Real Chromium rendered the login and launcher without CSP
violations, blocked framing of the application, and sent only
`http://localhost:3037/` on cross-origin navigation from a scan URL.

The browser also exercised C9 with API fixtures: the first health call timed
out, “Waking up the scanner…” appeared, the next probe started about five seconds
after the first, and exactly one stream opened and navigated to its report.
The report also displayed incomplete-coverage reasons and the specific PDF 429 message. Fixtures test browser behavior, not a real signed-in Render scan. Results are saved in docs/evidence/c11-local.json and c11-live.json.

Reproduce against a built/running frontend from `backend`:

```
python scripts/check_frontend_headers.py --url http://localhost:3037 --output ../c11-check.json --local-fixtures
```

Omit `--local-fixtures` when checking an accessible deployed host. The script
does not start scans on that host. It reads headers and uses the real browser
for login rendering, framing rejection and referrer behavior.

## Open deployment gate

The successful Vercel preview for PR #13,
`https://sentinels-omce0bbm5-elara17.vercel.app`, returned a 302 to Vercel SSO
on every tested application path. Those are deployment-protection responses;
they cannot prove the application's CSP or browser compatibility. Re-run on
an accessible deployment after review. Real GitHub login/callback, streaming,
PDF download and settings flows under the deployed CSP remain pending.
A supplemental self-scan also remains pending and cannot certify the app.

## Policy limit

`script-src` and `style-src` permit `unsafe-inline` for the existing static
Next pages and animation styles. This policy does not prevent injected inline
scripts. A nonce policy requires per-request rendering and review of hydration,
scripts, caching and hosting behavior. The current presence-only HeadersAgent
cannot identify this weakness; a green self-scan would not close that gap.
