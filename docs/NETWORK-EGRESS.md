# Outbound network: what the host restricts and what the app must

Launch plan task B7. Sentinels connects to hosts its users name, so the
question is what stops a scan from reaching something it shouldn't. This
page records what Render does and doesn't do about that, and what follows.

Checked against Render's documentation on 8 Oct 2026
([Free instances](https://render.com/docs/free),
[Outbound IP addresses](https://render.com/docs/outbound-ip-addresses),
[Private network](https://render.com/docs/private-network)). Re-check before
relying on it; none of this was tested on a live Render service.

## What Render restricts

| | On a Free web service |
|---|---|
| Outbound firewall / allow-list | **None.** Render documents no way to filter a service's outbound traffic. |
| Outbound SMTP | Blocked on ports 25, 465 and 587. Irrelevant to us: scans only use 80 and 443. |
| Private network, inbound | Free services can't receive private-network traffic. |
| Private network, outbound | **Allowed.** A Free service can send requests to other services in the same workspace and region by internal hostname. |
| Blocking private traffic per environment | Pro workspaces and above only. |
| Outbound IP addresses | Shared by every service in the region. Dedicated IPs are a paid add-on. |
| Traffic volume | Render "may suspend a Free web service that initiates an uncommonly high volume of traffic over the public internet." |

## What follows

**There is no network boundary.** Nothing at the host level stops the
backend from connecting to a private address, a cloud metadata address, or
another service in our Render workspace. The application-level policy in
`backend/net/` is the only line of defence, so:

- The beta stays invite-only until that changes.
- Keep nothing else in the same Render workspace and region as the backend
  (no internal admin service, no database reachable by internal hostname).
  If something has to live there, it must require its own authentication.
- A change to `net/policy.py` or `net/client.py` needs a second reviewer and
  its tests; a gap there is a gap everywhere.

**A scanner looks like heavy outbound traffic.** The limits that keep one
user from getting the service suspended are the per-user rate limit
(`rate_limit.py`), the concurrent-scan limit (3 overall, 1 per user), the
per-agent request budgets and the 90-second scan deadline.

**Scanned sites see Render's shared addresses.** A site that blocks or
reports them affects other Render customers and vice versa. We can't offer
site owners one address to allow-list without paying for dedicated IPs.

## What the application enforces

Every connection to a user-supplied or discovered host goes through
`net.policy`:

- Only `http`/`https`, only ports 80 and 443, no credentials in the URL.
- Every A and AAAA answer must be a public address; one private answer
  rejects the host. IPv6 addresses that wrap an IPv4 address (mapped,
  compatible, NAT64, 6to4, Teredo) are judged by the address inside.
- The connection is made to the address that was checked, not to the name,
  so DNS can't answer differently between the check and the connect.
- Every redirect hop is checked like a first request.
- Proxy environment variables are ignored.
- Response bodies stop at 2 MB after decompression.
- The raw TLS handshake (`agents/tls.py`, also used for subdomains) follows
  the same rules.
- The PDF renderer's browser has no network at all.

Covered by `tests/test_net_policy.py`, `test_net_client.py`,
`test_net_wiring.py`, `test_scan_limits.py` and `test_pdf_sandbox.py`.

## Known gaps

- **Clients for fixed hosts are outside the policy on purpose** (GitHub, OSV,
  Docker Hub, Groq, crt.sh via the scan client). They never take a host from
  a user. The repo tarball download follows GitHub's redirect to
  `codeload.github.com`; that redirect target is GitHub's choice, not ours.
- **The pinned client relies on a private httpx attribute** (`_pool`) because
  httpx has no public way to supply the connection layer. It is written for
  the pinned `httpx==0.28.1`; an upgrade must re-run `test_net_client.py`.
- **Blocking work can't be cancelled, only timed out.** A TLS handshake or a
  subdomain DNS lookup already running on a worker thread finishes or hits
  its own timeout (10 s and 5 s) after the scan that started it has ended.
- **Internal subdomains are named in the report.** A discovered host that
  resolves privately is listed as skipped. The names come from public DNS
  and certificate logs, so this reveals nothing new, but it is shown.

## PDF export on Render Free: not measured yet

`SENTINELS_PDF_ENABLED` is off by default and should stay off on Render
until both of these are settled:

1. **Chromium isn't installed by the documented build.** `docs/DEPLOY.md`'s
   build command is `pip install -r requirements.txt`, which installs the
   Playwright package but not a browser. PDF export needs
   `playwright install chromium` in the build as well (and may need system
   libraries the native runtime doesn't have).
2. **Whether it fits in 512 MB is unknown.** To find out: deploy a throwaway
   Free service with the browser installed and `SENTINELS_PDF_ENABLED=true`,
   export a PDF of a large report three times, and watch the service's
   memory graph and Events for an out-of-memory restart. If it restarts, or
   peaks close to the limit while a scan is also running, launch with PDF
   off.
