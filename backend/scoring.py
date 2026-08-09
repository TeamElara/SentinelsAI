"""Deterministic scoring: findings in, score/grade/counts out.

Every function here is a pure function — no network, no clock, no randomness,
no model. CLAUDE.md's rule ("scoring stays deterministic — same site, same
score, always") is enforced simply by never importing anything that could
make it otherwise.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from models import Finding, Severity, Status, SEVERITY_PENALTY

# Score -> grade cutoffs, checked highest-first. A fixed table, not a formula,
# so "why did this get a B" always has a one-line answer: the score, and this
# table.
_GRADE_THRESHOLDS: list[tuple[int, str]] = [
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
]

_SEVERITY_RANK = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}

# Different agents' names for the same underlying issue, collapsed onto one
# canonical id before dedup — see the module docstring on calculate_score for
# why this exists. The apex-host duplicate (headers vs. subdomain both seeing
# missing HSTS on the *same* host) is caught by dedup alone; this table is for
# when two agents use different ids for one problem.
ALIASES = {
    "subdomain-missing-hsts": "missing-hsts",
    "subdomain-missing-csp": "missing-csp",
    "subdomain-tls-invalid": "tls-cert-invalid",
    "api-missing-hsts": "missing-hsts",
}

# The three v4 attack-surface agents may each deduct at most this many points
# in total, no matter how many hosts/endpoints they find the same problem on.
# Existing agents (headers, recon, tls, exposure, dns) are not capped.
AGENT_PENALTY_CAP = 20
_CAPPED_AGENTS = {"api-security", "subdomain", "misconfig"}


def _base_id(finding_id: str) -> str:
    """The part of a finding id before the first ':'.

    New agents (V4-V6) suffix an id with the host it's about, e.g.
    "subdomain-missing-hsts:api.example.com" — this strips that suffix so
    repeats of the same underlying check group together. Existing agents'
    ids never contain ':', so this is a no-op for them: `_base_id` returns
    the id unchanged.
    """
    return finding_id.split(":", 1)[0]


def _hostname(value: str | None) -> str:
    """Best-effort hostname from a URL or bare host string, lowercased.

    Handles both "https://api.example.com/foo" and a bare "api.example.com"
    by forcing urlsplit to see a netloc either way. Falls back to the raw
    value if it still doesn't parse as a host (e.g. empty string).
    """
    if not value:
        return ""
    parsed = urlsplit(value if "//" in value else f"//{value}")
    return (parsed.hostname or value).lower()


def _issue_key(finding: Finding, scanned_host: str) -> tuple[str, str]:
    """The (canonical_base_id, host) pair that identifies "one problem".

    Two findings — from the same agent or different agents — that share this
    key are the same underlying issue and must cost points only once.
    `affected_url` is what makes the host explicit for the new agents; the
    five pre-v4 agents never set it, so they all resolve to `scanned_host`
    (the apex) — exactly where they've always reported.
    """
    base = ALIASES.get(_base_id(finding.id), _base_id(finding.id))
    host = _hostname(finding.affected_url) if finding.affected_url else scanned_host
    return base, host


def calculate_score(findings: list[Finding], url: str = "") -> int:
    """Start at 100, subtract a deduplicated penalty, clamp at 0.

    PASS findings cost nothing — only FAIL and WARN represent an actual
    problem (see Status in models.py). Beyond that, three rules apply, in
    order, so eight agents can never charge for the same problem twice:

    1. **Dedup.** Findings are grouped by `_issue_key` (canonical id + host);
       only the highest-severity finding in each group survives.
    2. **Alias collapse** (part of `_issue_key`, via `ALIASES`) folds a new
       agent's name for an old problem onto the old id, so a subdomain
       finding about the apex host collapses onto the headers agent's
       finding about the same host.
    3. **Repeat decay + per-agent cap.** The same surviving `base_id` seen on
       many different hosts is one operational mistake, not N of them: the
       1st occurrence (in a fixed, agent-order-independent sort) costs full
       price, the 2nd-3rd cost half, the 4th+ cost nothing. Separately, the
       three v4 attack-surface agents (`api-security`, `subdomain`,
       `misconfig`) may each deduct at most `AGENT_PENALTY_CAP` points in
       total — pre-v4 agents are never capped.

    For the five pre-v4 agents this is a no-op: each emits one finding per
    id, all on the apex host with `affected_url=None`, so every issue key is
    already unique — dedup keeps everything, decay never triggers (nothing
    repeats), and none of them are in the capped set. The score for a
    five-agent scan is therefore identical to before this function existed.
    """
    scanned_host = _hostname(url)
    non_passing = [f for f in findings if f.status != Status.PASS]

    # Rules 1 + 2: collapse to one finding per (canonical base_id, host),
    # keeping the highest-severity finding when more than one agent reports
    # the same issue.
    survivors: dict[tuple[str, str], Finding] = {}
    for finding in non_passing:
        key = _issue_key(finding, scanned_host)
        current = survivors.get(key)
        if current is None or _SEVERITY_RANK[finding.severity] > _SEVERITY_RANK[current.severity]:
            survivors[key] = finding

    # Rule 3: sort deterministically (never depends on which agent finished
    # first), then apply repeat decay per canonical base_id and track each
    # capped agent's running total separately.
    ordered = sorted(
        survivors.items(),
        key=lambda item: (-_SEVERITY_RANK[item[1].severity], item[0][1], item[0][0]),
    )

    occurrences: dict[str, int] = {}
    capped_totals: dict[str, int] = {agent: 0 for agent in _CAPPED_AGENTS}
    uncapped_penalty = 0

    for (base_id, _host), finding in ordered:
        occurrences[base_id] = occurrences.get(base_id, 0) + 1
        occurrence = occurrences[base_id]
        if occurrence <= 1:
            weight = 1.0
        elif occurrence <= 3:
            weight = 0.5
        else:
            weight = 0.0
        weighted_penalty = int(SEVERITY_PENALTY[finding.severity] * weight)

        if finding.agent in _CAPPED_AGENTS:
            capped_totals[finding.agent] += weighted_penalty
        else:
            uncapped_penalty += weighted_penalty

    penalty = uncapped_penalty + sum(min(total, AGENT_PENALTY_CAP) for total in capped_totals.values())
    return max(0, 100 - penalty)


def grade_for_score(score: int) -> str:
    """Turn a 0-100 score into a letter grade using the fixed cutoffs above."""
    for threshold, grade in _GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def count_by_severity(findings: list[Finding]) -> dict[str, int]:
    """How many non-passing findings fall into each severity bucket.

    Every severity level is a key in the result, even at 0 — so the frontend
    can always render all five rows without first checking which keys exist.
    """
    counts = {severity.value: 0 for severity in Severity}
    for finding in findings:
        if finding.status != Status.PASS:
            counts[finding.severity.value] += 1
    return counts
