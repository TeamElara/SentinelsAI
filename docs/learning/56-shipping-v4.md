# 56 — Shipping v4

> **Status:** done. `docs/PLAN-v4.md` is complete, V1 through V10, and the
> end-to-end pass confirms it against a real running app, not just the tests.

## What we built

This is the closing step for the whole v4 plan: bringing the project's
outward-facing docs (README, `CLAUDE.md`, the roadmap) up to date with what
V1-V9 actually shipped, then proving the finished thing works by running it
for real — not by re-reading the code and assuming it does.

`README.md`'s agent table grew from 5 rows to 8, plus a new "What Sentinels
does *not* do" section — a plain list of the boundary every new agent stops
at (no introspection queries, no method invocation, no takeover attempts).
`CLAUDE.md` picked up the two non-negotiables the plan's own header named
back at V0 — bounded probing and stated confidence — worded as rules for
future work, not just a description of what V4 did. `docs/ROADMAP.md` (which
had been untouched since the original 18-achievement roadmap closed out) got
a short "Beyond this roadmap" section pointing at PLAN-v2/v3/v4, since those
plans' own progress was never visible from the file a first-time reader
would open.

## The one big idea: a live check finds things a passing test suite can't

101 passing tests is real evidence the *logic* is correct — every check
fires on the right input and stays quiet on the wrong one. It says nothing
about whether the actual running app — server, browser, real network, real
env vars all present at once — behaves the same way. The two are different
questions, and this step is where the second one gets asked.

It found something the tests couldn't have: the "scan a website" dialog's
copy still said *"Five agents read what is already public..."* — plain
static text that no test would ever touch, because no test renders that
component and reads its words. Only opening the actual dialog in a real
browser surfaced it. One line fixed
(`frontend/components/landing/scan-select/ScanDialog.tsx`), reverified live.

## The actual verification

Everything below was run against a real backend (port 8011 — see the
"words worth knowing" note on why not 8010) and a real frontend, not
inferred from source:

- **Live 8-agent scan, in the browser:** `example.com` → **52/F**, all 8
  panels filled, matches every prior milestone's number exactly.
- **Determinism:** the same URL scanned twice via the API → **52/F** both
  times.
- **A messier real site:** `wordpress.org` → 61/D in 22.5s (well under the
  60s budget), all 8 agents ran with zero errors, subdomain agent found 11
  hosts and 7 findings — a genuinely busier report than the reference site.
- **`GROQ_API_KEY` removed:** a throwaway backend started with the key
  forced empty still returned a complete 52/F report with `summary: ""` and
  every agent clean.
- **A pre-v4 stored scan reloads correctly:** a scan saved back on
  2026-08-03, before any of this plan existed, still comes back as 54/F
  with exactly its original 5 agents and `subdomains: []` — the additive
  schema change never touched old rows.
- **All three export formats carry the new agents:** the `wordpress.org`
  scan's Markdown, JSON, and PDF exports (200 OK each) all include
  `api-security`/`misconfig`/`subdomain` findings and the subdomain
  inventory — checked by parsing the JSON export directly and grepping the
  Markdown, not assumed from the exporter's code.

The one item from V10's checklist not run here — killing one agent mid-scan
to watch the other seven still report — was already proven at the unit
level in V9's `test_orchestrator.py` (`test_one_broken_agent_leaves_the_
other_seven_intact`), which exercises the real `orchestrator.run_scan`
against eight real agent slots, seven working and one deliberately raising.
Re-running that same scenario by hand against a live server would test the
identical code path a second time, not a new one.

## Try it

- Start the backend and frontend per the README, scan `example.com`, and
  confirm 8 panels and a 52/F score.
- Open the "scan a website" dialog and read the copy — it now says "Eight
  agents."
- Delete a stored scan's `subdomains` rows by hand in SQLite and reload
  that scan's report — it should render with an empty inventory, not an
  error, proving the additive-field discipline still holds.

## Words worth knowing

- **Port 8010 gotcha (recurring):** a stale backend process from an earlier
  session was still answering on 8010 with only the original 5 agents —
  its PID (visible to `netstat`) doesn't exist in `Get-Process` or
  `tasklist`, so it can't be killed from either shell tool. The workaround,
  same as prior sessions: a fresh instance on a scratch port (8011 here),
  point `frontend/.env.local` at it for the verification pass, then revert.
- **Live verification vs. test coverage** — passing tests prove the logic;
  running the real app proves the logic is wired to the real UI, the real
  environment, and the real database. Neither replaces the other.

---

**Next:** nothing queued — `docs/PLAN-v4.md` is complete, V1 through V10.
