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

## PDF export on Render Free: launch with it off

`SENTINELS_PDF_ENABLED` is off by default and stays off on Render Free.
Chromium does not leave enough room on a 512 MB instance.

**Measured on 12 Oct 2026**, locally (macOS, Apple silicon, Python 3.12,
Playwright 1.61.0, Chromium headless shell 149), rendering real reports
through `report.pdf.generate_pdf` and sampling the memory of the browser's
process tree every 30 ms:

| Report | Browser + Playwright driver, peak | Render time |
|---|---|---|
| 10 findings | about 350 MB | 0.7 s |
| 150 findings | about 380-400 MB | 0.7 s |
| 600 findings | about 465 MB | 0.9 s |

The backend process itself is about 85 MB idle, before any scan runs. So one
PDF render puts the service at roughly 435-550 MB, at or over the limit with
nothing else going on. Chromium's `--single-process` mode brought the browser
down to about 305 MB (150 findings), which is still too close once a scan is
running, and that mode is unsupported and less stable.

How far to trust these numbers: they are the sum of each process's resident
memory, which counts shared pages more than once, and they come from macOS,
not Render's Linux. They could be off by tens of megabytes in either
direction. They are not off by the several hundred it would take to make
this comfortable.

What would change the decision:

- A paid instance with 2 GB, with `playwright install chromium` added to the
  build (the documented build installs the Python package but no browser,
  and the native runtime may lack system libraries Chromium needs). Measure
  again there before switching it on.
- Rendering PDFs somewhere else (a separate worker, or in the user's browser
  from the HTML export).

The sandbox itself was checked against the real browser the same day: a page
referencing a local server through a stylesheet, `@import`, a background
image, a font, scripts, `fetch`, images, an iframe, `<object>`, `<video>`,
a meta refresh and the cloud metadata address produced a PDF and **zero**
requests. `tests/test_pdf_real_browser.py` repeats that check wherever
Chromium is installed and is skipped where it isn't (CI).
