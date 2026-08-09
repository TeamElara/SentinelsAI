# Sentinels — working conventions

AI-powered **passive** website security auditor. URL in → 8 agents scan concurrently →
0–100 score + A–F grade + plain-language report, in under 60 seconds.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the 18-achievement build order and current position.

## Non-negotiable: scope

**Passive checks only.** Reading response headers, TLS config, DNS records, and GET
requests to public paths. Never send attack traffic — no SQLi, no brute force, no
fuzzing, no DoS, no automated form submission. This is a defensive/educational tool.
If a feature idea requires sending something harmful, it's out of scope.

## Non-negotiable: bounded probing

Every agent declares a hard cap on how many requests it may issue against a target,
and a wall-clock deadline (see `backend/agents/probe.py`'s `Budget`). No check may
loop over an unbounded list. Caps are constants at the top of each agent file so
they can be read in five seconds. Exhausting a budget stops further probing and
reports itself honestly (`*-scan-partial`) — it is never a silent truncation.

## Non-negotiable: confidence is stated, never implied

Any finding that can't be proven from what was observed says so — in its title
("Potential…"), in its wording ("manual verification recommended"), and in
`Finding.confidence` (0.0–1.0, `None` = certain/not applicable). A guess is never
upgraded into a claim. A CNAME pointing at a known hosting provider, for example,
is never on its own a "takeover" — see `backend/agents/takeover_signatures.py`.

## Non-negotiable: every change gets explained

This project is a learning exercise as much as a build. **No code lands without its
learning note.**

- One note per achievement: `docs/learning/AN-name.md`
- Format is defined in [`docs/learning/00-how-to-use-these-notes.md`](docs/learning/00-how-to-use-these-notes.md) — follow it exactly
- Every new concept needs a **tiny standalone example** (3–10 lines, runnable alone,
  about something ordinary — not about security) *before* the real code is shown
- Show rejected alternatives and why they lost, not just the final choice

## Calibration for this developer

- **Python:** comfortable with normal Python. **async/await, the event loop, and
  coroutines are new** — explain those from first principles, every time they appear.
  Don't re-explain functions, loops, or basic classes.
- **JavaScript/React:** new to both. When Act 4 starts, explain JS syntax and React
  concepts (components, state, props, hooks) from the ground up.
- **Workflow:** Claude writes the code and the note together; the developer reads,
  runs, and asks. Explain before writing, not after.

## Code style

- Comments explain **why**, not what. Assume the reader knows Python syntax but not
  the security domain — so `# HSTS missing means a downgrade attack is possible` is
  useful, `# loop through the headers` is noise.
- Agents must never crash the scan. Every agent catches its own exceptions and reports
  them via `AgentResult.error`.
- Scoring stays **deterministic** — no model in the loop. Same site, same score, always.
- The AI layer only *enriches*. If `GROQ_API_KEY` is missing or the call fails,
  the scan must still produce a complete report.

## Layout

```
backend/    FastAPI app, agents/, ai/, report/
frontend/   Next.js App Router + Tailwind
docs/       ROADMAP.md + learning/ notes
```
