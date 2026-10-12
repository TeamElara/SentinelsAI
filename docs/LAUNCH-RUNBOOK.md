# Launch runbook: the steps that need a dashboard

Everything that can be done in code is on `main`. What is left needs a login
to GitHub, Render, Turso, Vercel or Sentry, so it can't be done from a code
change. This is those steps in the order that works, each with how to tell it
worked. `DEPLOY.md` explains why each setting exists; this file is the
checklist. Tick as you go.

**Never paste a secret** (a key, a token, a `.pem`, the webhook secret, the
Turso token, the Sentry DSN) into chat, a pull request, an issue or an AI
session. Type each one only into the dashboard field named below.

Placeholders: `<api>` is the Render URL (`https://….onrender.com`), `<web>` is
the Vercel URL, `<slug>` is the GitHub App's URL name.

---

## 0. Decide first

- [ ] **Whose Vercel account deploys.** Vercel Hobby is for personal,
      non-commercial use and has no team seats (`HOSTING-DECISION.md`). If more
      than one person operates this, or it will ever make money, pick an
      eligible plan before the beta.
- [ ] **Who is invited.** Collect each person's GitHub **numeric id**:
      `curl -s https://api.github.com/users/<login> | grep '"id"'`.
- [ ] **A contact for the privacy and terms pages.** They currently point at
      GitHub Issues. A grievance e-mail is better (`LegalPage.tsx`). Have the
      pages read by someone qualified before the public launch.

## 1. Turso (the database that survives a restart)

Render's disk is wiped on restart, redeploy and idle spin-down, so production
must not use the local SQLite file.

- [ ] Create a database and a token (Turso CLI, or the dashboard):
      `turso db create sentinels`, `turso db show sentinels --url`,
      `turso db tokens create sentinels`. Check the Turso docs if a command
      has changed.
- [ ] Keep the URL (`libsql://…`) and the token for step 3.

Worked when: step 3 deploys and the smoke test's "redeploy keeps my data" (§6)
passes.

## 2. GitHub App (`github.com/settings/apps/<slug>`)

Do these before the first install, because several are **refused outright**
until they're set.

- [ ] **Homepage URL**: `<web>`.
- [ ] **Callback URL**: `<web>/api/auth/github/callback` (exact path).
- [ ] **Setup URL**: the install-callback path, `<web>/api/auth/github/install/callback`,
      with *Redirect on update* ticked.
- [ ] **General → Identifying and authorizing users → "Request user
      authorization (OAuth) during installation": tick it.** Without this every
      install is refused with "GitHub didn't confirm who you are during the
      install".
- [ ] **Where can this App be installed**: *Any account*, or the invited
      people can't install it.
- [ ] **Permissions**: Contents (read & write), Pull requests (read & write),
      Metadata (read). Add **Workflows (read & write)** only if the workflow
      fixer ships at launch; otherwise leave it off.
- [ ] **Webhook**: tick *Active*; URL `<web>/api/github/webhook`; set a
      **Webhook secret** and keep it for step 3; under *Subscribe to events*
      tick **Installation**.
- [ ] Generate a **private key** (`.pem`). Keep it outside the repo.

Worked when: GitHub → App → *Advanced → Recent deliveries* shows the `ping`
with **200** (after step 3 is deployed). 401 = the secret doesn't match; 503 =
`GITHUB_APP_WEBHOOK_SECRET` isn't set on Render.

## 3. Render (backend)

Service settings: root directory `backend`, **Python 3.13**, build
`pip install -r requirements.txt`, start
`uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1` (one worker: scans
and quotas are built for it). Health check path `/health`.

**Secret File** (Environment → Secret Files): `github-app-key.pem` with the
key's contents. It mounts at `/etc/secrets/github-app-key.pem`.

| Variable | Value | Needed for |
|---|---|---|
| `PYTHON_VERSION` | `3.13.7` | Same runtime as CI |
| `SENTINELS_SESSION_SECRET` | a new long random string, **not** the one on your laptop | Sign-in |
| `SENTINELS_FRONTEND_ORIGIN` | `<web>`, https, no trailing slash | Redirects, CORS, `Secure` cookie |
| `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET` | from the App page | Sign-in |
| `GITHUB_APP_ID`, `GITHUB_APP_SLUG` | from the App page | Fixes, install |
| `GITHUB_APP_PRIVATE_KEY_PATH` | `/etc/secrets/github-app-key.pem` | Fixes |
| `GITHUB_APP_WEBHOOK_SECRET` | the secret from step 2 | Uninstall webhook |
| `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` | from step 1 | Durable data. Sentinels refuses to use ephemeral SQLite on Render |
| `SENTINELS_ALLOWED_GITHUB_IDS` | `1234,5678` (the invited ids) | Invite-only beta |
| `SENTINELS_ENABLED_FIXERS` | **leave unset** | Unset on a deployment means *no* fixer can open a PR |
| `GROQ_API_KEY` | optional | AI summaries and chat |
| `SENTRY_DSN` | optional, from step 5 | Error reports |
| `SENTINELS_PDF_ENABLED` | **leave unset** (PDF off) | Chromium's memory on 512 MB is unmeasured |
| `SENTINELS_DAILY_*` | optional | Defaults: 10 website scans, 10 repo scans, 10 verifications, 10 PDFs, 20 AI fixes, 20 chat, 100 AI calls a day |

Never set `SENTINELS_ALLOW_DEV_TOKEN` or `SENTINELS_GITHUB_DEV_TOKEN` on a
deployment.

Worked when: `curl <api>/health` returns `{"status":"ok",…}` and the deploy log
shows no error about the database.

## 4. Vercel (frontend)

- [ ] Root directory `frontend`, Node 24.
- [ ] `NEXT_PUBLIC_API_BASE` = `<api>` (https). The headers and the `/api`
      rewrite are built from it at build time, so **redeploy after changing it**.

Worked when: `<web>` loads, and
`cd backend && python scripts/check_frontend_headers.py --url <web> --output /tmp/headers.json`
reports the security headers present on every path. (It refuses to pass while
Vercel's deployment protection redirects to a login page, so turn that off for
the production domain first.)

## 5. Sentry (optional, but wanted for a beta)

- [ ] Create a project, copy its DSN into `SENTRY_DSN` on Render.
- [ ] After the deploy, from a Render shell in `backend/`, send one test event:
      `python -c "import observability, sentry_sdk; observability.init_error_tracking(); sentry_sdk.capture_exception(RuntimeError('test https://example.com/path?token=abc')); sentry_sdk.flush()"`
- [ ] Open the event in Sentry. **It must not contain** the address, the
      query string, a header, a cookie or an IP. The message should read
      `test <url>`. If anything else shows, stop and report it.

Browser-side Sentry doesn't exist yet; frontend errors only reach the browser
console and Vercel's logs.

## 6. Smoke test: run after the first deploy and after every deploy

Use **two** GitHub accounts: A (invited) and B (invited, a different person),
and one account C that is *not* invited. Use a throwaway repository you don't
mind changing.

| # | Do | Expected |
|---|---|---|
| 1 | Open `<web>`, `<web>/terms`, `<web>/privacy`, `<web>/nope` | All load; `/nope` shows "Nothing here" |
| 2 | Sign in as C (not invited) | Back at `/login` with "invite-only"; no session |
| 3 | Sign in as A | Lands on the site; the chip reads `@A · Settings` |
| 4 | Scan a site you own, with the checkbox ticked | Report loads with a grade; coverage shown; the checkbox is required |
| 5 | Scan `http://localhost:8000` and `http://169.254.169.254` | "Not allowed: …"; no scan |
| 6 | Scan a public repo | Report loads |
| 7 | As B, open A's `<web>/scan/<id>` | The 404 page, not A's report |
| 8 | `curl -s -o /dev/null -w '%{http_code}' -H 'Sec-Fetch-Site: cross-site' -b 'sentinels_session=<A's cookie>' '<web>/api/scan/stream?url=https://example.com&permission_confirmed=true'` | **403.** If it starts a scan instead, Vercel is dropping the header and the cross-site protection is not working in production |
| 9 | Settings → Connect a repository, choose *Only select repositories*, pick the throwaway repo | Back on Settings: "Connected — <account>" |
| 10 | Preview a fix on that repo's scan, then try to apply it | Preview works; apply is refused ("isn't enabled yet") because `SENTINELS_ENABLED_FIXERS` is unset |
| 11 | Set `SENTINELS_ENABLED_FIXERS` to one fixer's slug and redeploy; apply it | A pull request opens on a `sentinels/…` branch; Sentinels doesn't merge it |
| 12 | Merge that PR yourself, then Verify | `target_fixed: true` and a score change. **This cycle is what earns the fixer its place in the list.** |
| 13 | Export JSON and Markdown; look for a PDF button | Both download; **no PDF button** (PDF is off) |
| 14 | Scan until the daily limit | A 429 with a clear message; the usage badge counts down |
| 15 | GitHub → App → Recent deliveries | `ping` = 200 |
| 16 | Uninstall the App from the throwaway account on GitHub | Within a minute Settings no longer lists it |
| 17 | Redeploy Render, then reload | Still signed in; the scans from step 4 are still there. **If they're gone the database isn't durable: stop.** |
| 18 | Delete a throwaway account (Settings → Delete account) | Signed out; its scans are gone |
| 19 | Put the sleeping service to sleep (wait 15 min idle) and open the site | "Waking up the scanner…", then it works |

## 7. Measure the emergency switches

- [ ] Set `SENTINELS_SCANS_PAUSED=1` on Render and **start a stopwatch**. Poll
      `curl -s -o /dev/null -w '%{http_code}\n' -X POST <api>/scan` until the
      answer is 503 (401 means not signed in yet; use a signed-in request).
      Write the seconds into `DEPLOY.md` where it says "not measured yet", then
      unset it.
- [ ] Repeat for `SENTINELS_WRITES_PAUSED=1` with a fix apply.

## 8. Backup before you widen access

- [ ] `cd backend && python scripts/database_backup.py export /safe/place/backup.json`
      (with the Turso variables in your environment). Restore only works into an
      **empty** database: try it once against a scratch Turso database so you
      know it works before you need it.

## 9. Rollback

If a deploy breaks something: Render → *Events* → roll back to the previous
deploy; Vercel → *Deployments* → promote the previous one. Pause scanning
first (`SENTINELS_SCANS_PAUSED=1`) if the problem is that scans are doing
something wrong. Practise this once before inviting anyone.

## 10. Before widening past the invited few

- [ ] Every fixer you enable has a recorded apply → merge → verify cycle
      (step 12). Today only `repo-config` does.
- [ ] A week with ~10 invited users and no critical incident; every reported
      false positive reviewed.
- [ ] Domain verification exists (a DNS record or a file on the site proving the
      person owns it). It isn't built yet; the permission checkbox only records
      a claim. It must exist before open sign-ups.
