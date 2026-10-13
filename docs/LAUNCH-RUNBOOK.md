# Launch runbook: the steps that need a dashboard

Everything that can be done in code is on `main`. What is left needs a login
to GitHub, Render, Turso, Vercel or Sentry, so it can't be done from a code
change. This is those steps in the order that works, each with how to tell it
worked. `DEPLOY.md` explains why each setting exists; this file is the
checklist. Tick as you go.

**Never paste a secret** (a key, a token, a `.pem`, the webhook secret, the
Turso token, the Sentry DSN) into chat, a pull request, an issue or an AI
session. Type each one only into the dashboard field named below.

## Where things stand (13 October 2026)

**Live:** the backend runs `main` on Render (Ohio, free plan, one worker, 3.13.7)
against the Turso database `sentinels` in the **same region** (`aws-us-east-2`,
organization `team-elara-organization`, created by the team's own Turso login so
no individual's name is on it). Schema version 20. The deployment contract
check and the frontend header/browser check pass. `/usage` and `/auth/me`
answer 401, PDF export is off, the webhook ping answers 200. A cold start after
16 minutes idle took about 42 s; a warm `/health` about 1 s.

**Not done yet** (each is a step below):
- App settings: the production callback URL first in the list, and the webhook
  **Active** box ticked (§2).
- One real install end to end on a throwaway repository (§6, rows 9-12). This
  is the first test of the install flow through GitHub itself.
- The pause switches timed on the real service (§7).
- The smoke test with two invited accounts and one uninvited (§6).
- Memory under load: free-plan metrics are locked, so Render can't show it. A
  local measurement (macOS, Python 3.11, the production database driver against
  a real Turso database, PDF off) gave **144 MB** idle (the server 99 MB, two
  database worker processes 15 and 29 MB) and a **peak of 194 MB** with three
  scans running at once, the most the app allows. That is well inside 512 MB,
  but Linux and Python 3.13 differ somewhat and a larger target means more
  findings in memory, so still watch the Render logs for out-of-memory
  restarts.
- Weekly encrypted backups (`docs/TRACK-C-OPERATIONS.md`), UptimeRobot, Sentry
  DSN, a rollback rehearsal, and every fixer's apply → merge → verify cycle.

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
- [ ] Put it **in the same region as the Render service** (Render shows its
      region on the service page; a database in another region adds a round
      trip to every query, and a scan or a quota check makes many). Turso's
      free plan names the organization after the account you sign in with, so
      sign in with an account that carries the team's name.
- [ ] Keep the URL (`libsql://…`) and the token for step 3. They go only into
      Render's Environment page; never into chat, a PR or an AI session.

Worked when: step 3 deploys and the smoke test's "redeploy keeps my data" (§6)
passes.

## 2. GitHub App (`github.com/settings/apps/<slug>`)

Do these before the first install, because several are **refused outright**
until they're set.

- [ ] **Homepage URL**: `<web>`.
- [ ] **Callback URL**: `<web>/api/auth/github/callback` (exact path), and make
      it the **first** one in the list. GitHub sends the browser to the first
      callback URL after an install, so a `localhost` URL listed above it would
      send every install there. Keep localhost URLs below it for local work,
      or leave them out.
- [ ] **General → Identifying and authorizing users → "Request user
      authorization (OAuth) during installation": tick it.** Without this every
      install is refused with "GitHub didn't confirm who you are during the
      install". Ticking it **turns the Setup URL off**, which is expected: GitHub
      then sends the browser to the callback URL after an install, with
      `installation_id` added, and Sentinels finishes the install there. Leave
      the Setup URL empty; there is no `/api/auth/github/install/callback` page
      on the frontend.
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
| `SENTINELS_GITHUB_API_TOKEN` | **wanted** | A fine-grained GitHub token with **no permissions**. Without it repository scans share Render's IP allowance of 60 GitHub API calls an hour and often fail with "GitHub API rate limit exceeded". Create it under the GitHub account the team uses (Settings → Developer settings → Personal access tokens → Fine-grained, Public repositories read-only); enter it only on Render |
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
- [ ] In the Sentry project: *Settings → Security & Privacy*, turn on **Prevent
      Storing of IP Addresses** and keep **Data Scrubber** on. The privacy page
      promises the IP isn't stored; browser reports pass through Vercel, which
      may forward the visitor's IP in a header.
- [ ] After the deploy, send one test event. Render's free plan has no shell, so
      do it from `backend/` on your own machine with the production DSN in the
      environment for that one command (`SENTRY_DSN=… python -c …`):
      `python -c "import observability, sentry_sdk; observability.init_error_tracking(); sentry_sdk.capture_exception(RuntimeError('test https://example.com/path?token=abc')); sentry_sdk.flush()"`
- [ ] Open the event in Sentry. **It must not contain** the address, the
      query string, a header, a cookie or an IP. The message should read
      `test <url>`. If anything else shows, stop and report it.

- [ ] Browser errors (optional): set `NEXT_PUBLIC_SENTRY_DSN` on Vercel to the
      same DSN and redeploy (it's read at build time). Then in the browser
      console on `<web>` run `setTimeout(() => { throw new Error('test https://example.com/x?token=abc') })`
      and check the event the same way: the message should read `test <url>`.

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
      `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{"url":"https://example.com","permission_confirmed":true}' <api>/scan`
      every couple of seconds. No session is needed: the pause is checked before
      sign-in, so it answers **401 while scans run and 503 once paused**. Note
      the seconds to the first 503 (and any 502s while the service restarts),
      then unset it and note the seconds until 401 returns. Write the first
      number into `DEPLOY.md` where it says "not measured yet".
- [ ] Repeat for `SENTINELS_WRITES_PAUSED=1`. This one **does** need a signed-in
      request, because a fix apply is refused with 401 before the pause is
      looked at: use a throwaway repository's fix, signed in as an invited
      account, and expect 503 once paused.

## 8. Backup before you widen access

- [ ] `cd backend && python scripts/database_backup.py export /safe/place/backup.db`
      (with the Turso variables in your environment). Restore only works into an
      **empty** database: try it once against a scratch Turso database so you
      know it works before you need it.

The logical export above is a plaintext SQLite file. For production backup
storage and the encrypted weekly workflow, follow
[`TRACK-C-OPERATIONS.md`](TRACK-C-OPERATIONS.md#weekly-encrypted-production-backups).
The workflow remains disabled until production secrets and a recovery key are
configured; verify a real export and scratch-database restore before counting
backups as operational. Complete the idle-wake smoke test before enabling the
5-minute uptime monitor, which otherwise keeps Render awake.

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
- [ ] Set `SENTINELS_REQUIRE_DOMAIN_VERIFICATION=1` on Render. Then, signed in:
      scanning a site you haven't verified answers "Verify that you control …"
      with a link to Settings; add the domain there, publish the TXT record (or
      the file), press Check, and the same scan starts. This must be on before
      open sign-ups.
