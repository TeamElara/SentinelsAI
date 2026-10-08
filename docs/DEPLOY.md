# Deploying Sentinels

Two services, deployed separately: the FastAPI backend (Render) and the
Next.js frontend (Vercel). Neither needs a Dockerfile — both platforms build
straight from this repo.

## 1. Backend — Render

Render → New → Web Service → point at this repo.

- **Root directory:** `backend`
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
  (Render sets `$PORT` itself — don't hardcode 8011/8000, those are only the
  local dev ports in `.claude/launch.json`.)

### Environment variables

Set these under the service's *Environment* tab — see `backend/.env.example`
for the full explanation of each one. The short version:

| Variable | Required? | Notes |
|---|---|---|
| `SENTINELS_SESSION_SECRET` | Yes | Any long random string — sign-in refuses to work without it. |
| `SENTINELS_FRONTEND_ORIGIN` | Yes, for a real deploy | Your Vercel URL, e.g. `https://sentinels.vercel.app`. Used for the post-sign-in redirect, the CORS allow-list, and whether the session cookie is marked `Secure` — one value now drives all three, so it must be exact (scheme included). |
| `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET` | Yes | From the GitHub App's settings page. Powers "Sign in with GitHub". |
| `GITHUB_APP_SLUG` / `GITHUB_APP_ID` | Only for autofix | Needed to open pull requests, not for sign-in. |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Only for autofix | See below — this is the one setting that needs a Render-specific trick. |
| `GROQ_API_KEY` | No | Scans work fully without it; only the AI summary sentence is skipped. |

### The GitHub App private key on Render

`GITHUB_APP_PRIVATE_KEY_PATH` deliberately expects a *file path*, not the PEM
pasted into an env var (see the comment in `.env.example` for why — a
multi-line secret in an env var ends up in shell history and CI logs).
Render's answer to this is **Secret Files**: Environment tab → Secret Files →
add a file (e.g. `github-app-key.pem`) with the `.pem` contents pasted in.
Render mounts it at `/etc/secrets/github-app-key.pem` on every instance —
set `GITHUB_APP_PRIVATE_KEY_PATH=/etc/secrets/github-app-key.pem` and nothing
else about the code needs to change.

## 2. Frontend — Vercel

Vercel → New Project → import this repo → set **Root Directory** to
`frontend` (Vercel auto-detects Next.js from there, no build command needed).

### Environment variables

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_BASE` | Your Render backend URL, e.g. `https://sentinels-api.onrender.com` |

## 3. After both are live

- Update the GitHub App's **Homepage URL** (App settings page) to the Vercel
  domain instead of `localhost:3000`.
- Set the GitHub App's **Callback URL** to
  `https://<your-vercel-domain>/api/auth/github/callback` — note the
  `/api/auth/github/callback` path, not the bare Vercel domain and not the
  Render backend's own `/auth/github/callback`. Vercel and Render are
  different origins, so a cookie Render sets directly is third-party to the
  browser and gets blocked by Safari/Firefox (and increasingly Chrome).
  `backend/auth/github_oauth.py`'s `get_callback_url()` sends GitHub through
  a same-origin Next.js route (`frontend/app/api/auth/github/{login,callback}/route.ts`)
  instead, which re-sets the session cookie on the Vercel domain so every
  later `/api/*` call from the browser carries a first-party cookie. If the
  App's registered Callback URL doesn't match this path exactly, GitHub
  refuses the OAuth attempt outright.
- Confirm `SENTINELS_FRONTEND_ORIGIN` on Render exactly matches that Vercel
  domain (scheme + host, no trailing slash) — a mismatch here is the most
  likely first bug: it'll show up as `state_mismatch` on sign-in or CORS
  rejections in the browser console, not a clean error message.
- On the App's installation page (org or account settings → Installations),
  confirm it actually shows the **permissions** the App requests (Contents,
  Pull requests, Metadata, Workflows — see "What only the developer can do"
  in `docs/PLAN-v5.md`) and has a **repository** selected. An installation
  showing "No permissions" / "No repositories" means the App itself currently
  requests zero permissions on its own settings page — fix that on
  `github.com/settings/apps/<slug>` → Permissions & events first, then
  reinstall/accept the upgrade; there is nothing to configure per-installation
  until the App is requesting something.
- On the App's settings page (`github.com/settings/apps/<slug>` → General →
  Identifying and authorizing users), turn on **Request user authorization
  (OAuth) during installation**. The install callback
  (`/auth/github/install/callback`) needs the `code` GitHub then adds to prove
  that the person finishing the install is the signed-in Sentinels user and can
  access that installation. With the setting off, every install is refused with
  "GitHub didn't confirm who you are during the install". The App must also be
  allowed to read a collaborator's permission on a repository (Metadata: read);
  Sentinels checks that the signed-in user can push to a repository before it
  opens a fix pull request there, and treats anything it can't confirm as no.
- Set the beta gate before inviting anyone (all in `backend/.env.example`):
  `SENTINELS_ALLOWED_GITHUB_IDS` (numeric GitHub ids of the people you invited)
  and `SENTINELS_ENABLED_FIXERS` (leave it unset and **no** fixer can open a
  pull request on a deployment; list a fixer's slug only after it has been
  through a real preview → apply → merge → verify cycle). To stop one account,
  from a Render shell in `backend/`:
  `python -c "from storage.users import set_blocked; print(set_blocked('login', True))"`.
- The emergency switches are `SENTINELS_SCANS_PAUSED=1` (no new scans or
  re-verifications) and `SENTINELS_WRITES_PAUSED=1` (no new pull requests;
  previews still work). Render restarts the service when an environment
  variable changes, so they take effect after the restart, not instantly. Flip
  one on the real service once and write the measured time here:
  **not measured yet**.
- Run one real sign-in and one real scan end to end before calling it done —
  the same rule this project applies to autofix (`docs/PLAN-v5.md`'s "a fix
  isn't done when a PR opens, it's done when the re-scan proves it") applies
  here too: a deploy isn't done because it built, it's done when someone
  actually used it.
