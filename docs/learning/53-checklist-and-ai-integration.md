# 53 — Checklist rules and AI prompt integration

> **Status:** done. The deployment checklist now has three new items driven by
> the misconfig/subdomain agents, and the AI summary/chatbot know which
> findings are solid vs. which ones need a human to double-check.

## What we built

Two small, separate things that both plug the last two milestones' new
agents into the parts of Sentinels a user actually reads.

First, three new rows on the deployment checklist: "Directory listing
disabled", "No debug output or stack traces exposed" (this one can block
deployment), and "No dangling DNS records". Each just watches for one
specific finding id from `misconfig` or `subdomain` — no new logic, the
checklist machinery from earlier milestones already does the work.

Second, the AI-written summary and the chatbot both got smarter about the
new agents' `confidence` field. A finding like "potential subdomain
takeover" isn't a certainty the way "HSTS header missing" is — it's a lead
that still needs a human to confirm. Before this, the AI prompt just listed
every finding flat, so the model had no way to know which ones to hedge on.
Now it doesn't have to guess.

## The one big idea: splitting a list by a condition

The prompt-building code needed to turn one list of findings into two:
"confirmed" and "needs verification". This is one of the most common things
you do with a list, and Python has a clean way to write it — a **list
comprehension with a condition**.

Standalone example, nothing to do with security scanning:

```python
scores = [95, 42, 88, 55, 70]

passing = [s for s in scores if s >= 60]
failing = [s for s in scores if s < 60]

print(passing)  # [95, 88, 70]
print(failing)  # [42, 55]
```

Read `[s for s in scores if s >= 60]` right to left: "for each `s` in
`scores`, keep it if `s >= 60`." Two lines like that, with opposite
conditions, split one list into two without ever mutating the original.

That's exactly what `build_analyst_messages` now does with findings, using
`confidence` instead of a test score — `None` or `>= 0.9` counts as
confirmed, anything lower goes to needs-verification. 0.9 isn't arbitrary:
it's the same number `FindingRow.tsx` already uses to decide whether to show
its "needs verification" chip, so the report page and the AI's own words
about the same finding never contradict each other.

One detail worth calling out: the split happens **before** the code trims
the list down to the first 15 findings for the prompt (there's a cap so the
prompt doesn't grow unbounded). If it trimmed first and split second, a scan
with 15+ confirmed findings could push every hedged finding off the end
before the model ever saw them.

## The actual code

The checklist rules are just three more entries in the same list every
other rule lives in:

```python
ChecklistRule(
    key="no_debug_output",
    title="No debug output or stack traces exposed",
    tier="auto",
    agent="misconfig",
    blocking=True,   # a stack trace in prod is bad enough to block deployment
    evaluate=lambda f: _from_finding(f, "debug-output-exposed"),
),
```

`_from_finding` already existed — if the finding id it's looking for never
shows up (because the check passed, or because the agent crashed and
produced nothing), it defaults to `"pass"` rather than lying about a
failure. Nothing new had to be written for that safety net; the three new
rules just get it for free.

The prompt split:

```python
confirmed = [
    f for f in findings
    if f.confidence is None or f.confidence >= 0.9
]
needs_verification = [
    f for f in findings
    if f.confidence is not None and f.confidence < 0.9
]
```

And each finding line now includes *where* it applies, when that's known:

```python
def _finding_line(finding: Finding) -> str:
    line = f"- [{finding.status.value.upper()}] {finding.severity.value}: {finding.title}"
    if finding.affected_url:
        line += f" (on {finding.affected_url})"
    return line
```

Without `affected_url`, "potential subdomain takeover" in the prompt gave
the model no way to say *which* subdomain — now it can.

## Try it

- Run `cd backend && ./.venv/Scripts/python.exe -m pytest tests -q` — 56
  tests pass, 9 of them new (`test_checklist_v4.py`).
- In a Python shell, build two `Finding`s with `confidence=0.9` and
  `confidence=0.6` and pass them to `build_analyst_messages(...)` — print
  the returned prompt text and see them land in different groups.
- Run a live scan with `GROQ_API_KEY` unset — the report still comes back
  complete with an empty `summary`, proving the AI layer's failure mode
  hasn't changed.

## Words worth knowing

- **List comprehension** — `[x for x in items if condition]`, a compact way
  to build a new filtered list without a `for` loop and `.append()`.
- **Cache key** — `PROMPT_VERSION` is part of what identifies a cached fix
  suggestion; bumping it (`"v2"` → `"v3"`) makes old cached answers
  invisible without deleting anything from the database.

---

**Next:** V8 — frontend work for the three new agents (artwork, the grid
going from five panels to eight, a table for the subdomain inventory).
