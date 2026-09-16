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

- Update the GitHub App's **Callback URL** and **Homepage URL** (App settings
  page) to point at the Vercel domain instead of `localhost:3000`.
- Confirm `SENTINELS_FRONTEND_ORIGIN` on Render exactly matches that Vercel
  domain (scheme + host, no trailing slash) — a mismatch here is the most
  likely first bug: it'll show up as `state_mismatch` on sign-in or CORS
  rejections in the browser console, not a clean error message.
- Run one real sign-in and one real scan end to end before calling it done —
  the same rule this project applies to autofix (`docs/PLAN-v5.md`'s "a fix
  isn't done when a PR opens, it's done when the re-scan proves it") applies
  here too: a deploy isn't done because it built, it's done when someone
  actually used it.
