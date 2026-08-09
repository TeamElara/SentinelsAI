# 49 — Scoring dedup, decay, and per-agent caps

> **Status:** done. `calculate_score` now collapses duplicate issues, discounts
> repeats, and caps each new v4 agent at 20 points — a live scan of
> `example.com` still scores exactly 54/F, same as before this change.

## What we built

Right now Sentinels has 5 agents, and each one only ever looks at the one
site you scanned. Soon it'll have 8, and three of those new agents can look
at *many* hosts in a single scan (subdomains, API endpoints). That opens a
real problem: if `headers` and `subdomain` both notice the same site is
missing HSTS, should that cost you points twice? `scoring.py` now says no —
and also handles the trickier case of the same *kind* of problem showing up
on fifteen different subdomains.

## The one big idea: same problem, counted once

Every finding gets an **issue key** — a fingerprint of "what's wrong" plus
"where":

```python
issue_key = (canonical_base_id, host)
```

Two findings with the same key are the same problem, no matter which agent
reported them or in what order. So before anything gets subtracted from 100,
`calculate_score` groups all findings by this key and keeps only the
worst-severity one per group.

It's the same trick as deduplicating a shopping list. If you write "milk" on
your list twice, you don't buy milk twice — you cross off the duplicate and
buy one carton. Here, "milk" is `(base_id, host)`, and "crossing off the
duplicate" is keeping just the more severe finding and discarding the rest.

Two extra wrinkles on top of plain dedup:

- **Aliases.** New agents sometimes have their own id for an old problem —
  e.g. the subdomain agent's `subdomain-missing-hsts` on your main domain is
  really the same issue as headers' `missing-hsts`. A small lookup table
  (`ALIASES`) rewrites the new id onto the old one *before* the dedup step,
  so they collide into one group and cost points once.
- **Repeat decay.** If the *same* base problem shows up on many different
  hosts (say, 15 subdomains all missing HSTS), that's one operational
  mistake repeated, not 15 separate ones. So after dedup, occurrences of the
  same base id are counted in a fixed order (sorted by severity, then host,
  then id — never by "whichever agent finished first"): the 1st costs full
  price, the 2nd and 3rd cost half, the 4th onward cost nothing.

## The other idea: a spending limit per new agent

Even with decay, a very messy subdomain list could still add up. So the
three new agents (`api-security`, `subdomain`, `misconfig`) each get a hard
ceiling: **no more than 20 points off, total, no matter how much they find.**
It's like a store credit that maxes out at $20 — you can keep "spending" past
that only findings from those three agents, but the register won't ring up
anything beyond the limit. The five original agents aren't capped at all;
they never needed to be, since they only ever look at one host each.

## The actual code

```python
def _issue_key(finding: Finding, scanned_host: str) -> tuple[str, str]:
    base = ALIASES.get(_base_id(finding.id), _base_id(finding.id))
    host = _hostname(finding.affected_url) if finding.affected_url else scanned_host
    return base, host
```

`_base_id` just strips anything after the first `:` in an id (new agents can
suffix an id with its host, like `subdomain-missing-hsts:api.example.com`) —
existing agents never use `:`, so this is a no-op for them.

```python
for (base_id, _host), finding in ordered:
    occurrences[base_id] += 1
    weight = 1.0 if occurrences[base_id] <= 1 else 0.5 if occurrences[base_id] <= 3 else 0.0
    weighted_penalty = int(SEVERITY_PENALTY[finding.severity] * weight)
    if finding.agent in _CAPPED_AGENTS:
        capped_totals[finding.agent] += weighted_penalty
    else:
        uncapped_penalty += weighted_penalty

penalty = uncapped_penalty + sum(min(t, AGENT_PENALTY_CAP) for t in capped_totals.values())
```

The cap is applied at the very end, per agent — so it doesn't matter what
order the weighted penalties came in, only their total.

## Try it

- `cd backend && ./.venv/Scripts/python.exe -m pytest tests -q` — 11 tests
  covering dedup, aliasing, decay, and the cap, all passing offline (no
  network, no real site touched).
- Read `tests/test_scoring.py::test_five_agent_regression_matches_pre_v4_arithmetic`
  — it pins that today's 5-agent math is untouched.
- Run a live scan of a real site and compare the score to before this
  change landed — it should be identical, since nothing repeats yet (no v4
  agents exist to produce repeats).

## Words worth knowing

- **Issue key** — the `(base_id, host)` pair used to decide "is this the
  same problem as that other finding?"
- **Decay** — reducing a repeated penalty's weight the more times it repeats,
  instead of charging full price every time.
- **Deterministic sort** — sorting by fixed, explicit fields (severity, host,
  id) so the result never depends on which agent happened to finish first.

---

**Next:** V4 — the API Security agent, the first of the three new agents this
scoring logic was built for.
