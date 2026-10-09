# C5 hosting decision — 8 October 2026

**Use the existing Vercel frontend and Render API, with Turso as the remote
database.** This is the user's selected setup. Keep launch gated on reviewed
security integration, persistent quotas and live database/recovery evidence.

| Component | Decision and evidence |
| --- | --- |
| GitHub | TeamElara/SentinelsAI is public. Private-organization repository restrictions are not a blanket barrier for this repository. |
| Vercel | Existing project links identify elara17/sentinels-ai. Real PR previews have successfully deployed. Runtime: Node 24; frontend/package.json engines enforces it. No new project or paid plan was created. |
| Vercel ownership | Confirm the dashboard owner and plan before public launch. Hobby is limited to personal, non-commercial use and excludes team collaboration features. A public repository does not resolve those terms. Commercial/team operation needs an eligible plan/provider chosen by the owner. |
| Render | Keep the existing service; Python 3.13, one worker, bounded scans and one PDF at a time. Free services sleep after 15 idle minutes and can take about a minute to wake. Local SQLite data is lost on restart/redeploy/spin-down, so production must use remote Turso. |
| Turso | Direct primary via maintained libsql 0.1.11. Local full-suite and snapshot restore checks pass. Remote migration, concurrent quota, latency and Render redeploy checks remain pending credentials. |
| Resource ceiling | Render can suspend unusually high service-initiated external traffic; bandwidth/build limits also apply. Durable daily budgets are necessary. Keep a small beta and inspect provider usage before increasing limits. |

**Configuration to verify in the existing dashboards:** Vercel root
`frontend`, Node 24, NEXT_PUBLIC_API_BASE set to the Render HTTPS origin.
Render root `backend`, Python 3.13, install requirements and Chromium dependencies,
start `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`, and set
TURSO_DATABASE_URL/TURSO_AUTH_TOKEN plus the existing session/GitHub secrets.
Keep secrets server-side. Existing OAuth callback and frontend origin must
continue to match the deployed URLs.

**Access boundary:** no Turso, Render or Vercel CLI credentials and no matching
GitHub repository secrets are available in this workspace. Successful Vercel
previews prove deployment of commits, not account-plan eligibility or database
durability. Dashboard changes and the real redeploy/restore gate remain open.

Sources checked 8 October 2026: [Vercel Git restrictions](https://vercel.com/docs/git),
[Hobby terms/features](https://vercel.com/docs/plans/hobby),
[Render free-service limits](https://render.com/docs/free),
[Turso Python SDK](https://docs.turso.tech/sdk/python/quickstart).
