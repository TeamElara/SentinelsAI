"""All prompt templates for the three AI features.

PROMPT_VERSION is the cache key for fix suggestions — bump it (e.g. "v2")
whenever any fix prompt changes. That invalidates every cached fix without a
DB migration: the old rows stay in the table but are never returned, because
the cache lookup matches on (finding_id, prompt_version).

The analyst prompt lives here too so every LLM-facing text is in one file.
"""
from __future__ import annotations

from models import Finding, ScanReport, ChecklistItem

# v3 (PLAN-v4 §V7): analyst prompt now splits findings into confirmed vs.
# needs-verification (by confidence) and carries affected_url per line; chat
# digest gained the subdomain inventory. Bumping this invalidates every
# cached fix suggestion with no DB migration -- the old rows just stop being
# returned, since the cache lookup matches on (finding_id, prompt_version)
# (see ai/fixes.py). Nothing needs cleaning up.
PROMPT_VERSION = "v3"

# A finding's confidence is None ("not applicable" -- the check either saw
# the thing or it didn't) or a 0.0-1.0 hedge, only ever set by the v4 agents
# (see models.Finding). Below this, the finding is a *lead*, not a fact --
# it needs a human to confirm it before anyone acts on it. 0.9 matches the
# threshold FindingRow.tsx already uses for its "needs verification" chip,
# so the report UI and the AI summary never disagree about which findings
# are hedged.
_NEEDS_VERIFICATION_THRESHOLD = 0.9

# ── Analyst (executive summary) ────────────────────────────────────────────

ANALYST_SYSTEM = (
    "You are a security analyst writing a short, plain-English summary of an "
    "automated website security scan, for a reader who is not a security expert. "
    "Write 2-4 sentences, no markdown formatting, no bullet points. "
    "Mention the most serious problem by name if one exists, and say clearly "
    "whether the site is in generally good or poor shape. "
    "Findings are given in two groups, CONFIRMED and NEEDS VERIFICATION -- "
    "the second group is not certain, so say so plainly whenever you mention "
    "one of those (e.g. 'a possible... that should be manually confirmed'). "
    "Never invent a finding that isn't listed. Never restate or change the "
    "score, grade, or any finding's severity -- those are computed, not "
    "yours to interpret. Prioritise what you mention by severity first, then "
    "confidence. Explain impact in terms of what could actually go wrong, "
    "not jargon."
)

# The repo-side analyst has a narrower, more specific job than the URL one:
# PLAN-v3's stated audience is "the 'vibe coder' who shipped something with an
# AI assistant and has no idea what they got wrong" -- so this leads with the
# mistake itself, not just a severity list.
REPO_ANALYST_SYSTEM = (
    "You are a security engineer reviewing an automated scan of a GitHub "
    "repository, for a developer who likely built this with an AI coding "
    "assistant and is not a security expert. "
    "Write 2-4 sentences, no markdown formatting, no bullet points. "
    "Lead with the single most important mistake in the repository, named "
    "plainly -- what it is and roughly where (which file, if one stands out). "
    "Then say clearly whether the codebase is in generally good or poor shape "
    "and, if it's not deployment-ready, why not."
)

_MAX_FINDINGS_IN_ANALYST_PROMPT = 15


def _finding_line(finding: Finding) -> str:
    line = f"- [{finding.status.value.upper()}] {finding.severity.value}: {finding.title}"
    if finding.affected_url:
        line += f" (on {finding.affected_url})"
    return line


def build_analyst_messages(
    url: str, score: int, grade: str, findings: list[Finding]
) -> list[dict]:
    # Split confirmed vs. needs-verification *before* truncating to the top
    # N, so a scan with 15+ confirmed findings doesn't silently push every
    # hedged one off the end of the prompt.
    confirmed = [
        f for f in findings
        if f.confidence is None or f.confidence >= _NEEDS_VERIFICATION_THRESHOLD
    ]
    needs_verification = [
        f for f in findings
        if f.confidence is not None and f.confidence < _NEEDS_VERIFICATION_THRESHOLD
    ]

    lines = [f"Site: {url}", f"Score: {score}/100 (grade {grade})", ""]
    lines.append("Confirmed findings:")
    if confirmed:
        for finding in confirmed[:_MAX_FINDINGS_IN_ANALYST_PROMPT]:
            lines.append(_finding_line(finding))
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Needs verification (lower confidence -- do not state as fact):")
    if needs_verification:
        for finding in needs_verification[:_MAX_FINDINGS_IN_ANALYST_PROMPT]:
            lines.append(f"{_finding_line(finding)} (confidence {finding.confidence:.0%})")
    else:
        lines.append("- none")

    return [
        {"role": "system", "content": ANALYST_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def build_repo_analyst_messages(
    repo_url: str, score: int, grade: str, findings: list[Finding]
) -> list[dict]:
    """Repo-side sibling of `build_analyst_messages`. Each finding line
    includes `file_path`/`line` when the finding has one (every repo finding
    does except a handful of whole-repo checks like "no CI configured") --
    that's the concrete detail `REPO_ANALYST_SYSTEM` needs to say *where*
    the worst mistake is, not just what kind of mistake it is.
    """
    lines = [f"Repository: {repo_url}", f"Score: {score}/100 (grade {grade})", "", "Findings:"]
    for finding in findings[:_MAX_FINDINGS_IN_ANALYST_PROMPT]:
        location = f" ({finding.file_path}:{finding.line})" if finding.file_path else ""
        lines.append(
            f"- [{finding.status.value.upper()}] {finding.severity.value}: "
            f"{finding.title}{location}"
        )
    return [
        {"role": "system", "content": REPO_ANALYST_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


# ── Fix suggestions ─────────────────────────────────────────────────────────

FIX_SYSTEM = (
    "You are a security engineer explaining a specific website security finding "
    "to a developer who is not a security expert. "
    "Respond with ONLY a valid JSON object matching exactly this structure — "
    "no markdown fences, no extra keys, no explanation outside the JSON:\n"
    "{\n"
    '  "why_it_exists": "one sentence — what misconfiguration causes this",\n'
    '  "security_impact": "one sentence — what an attacker can actually do",\n'
    '  "exploitation": "one paragraph — conceptual explanation of how it could be abused (no working exploit code)",\n'
    '  "recommended_fix": "one paragraph — concrete steps to remediate",\n'
    '  "best_practices": ["bullet 1", "bullet 2", "bullet 3"],\n'
    '  "framework_examples": {"Apache": "...", "Nginx": "..."}\n'
    "}\n"
    "framework_examples should include 1-3 relevant server/framework configs. "
    "best_practices should have 2-4 items."
)

# Same JSON contract as FIX_SYSTEM (so FixSuggestion / ai/fixes.py need no
# changes at all) -- only framework_examples' meaning changes. A repo finding
# isn't missing a server header; it's a specific line in a specific file, so
# "Apache/Nginx config" makes no sense here. This asks for the actual
# before/after diff instead.
REPO_FIX_SYSTEM = (
    "You are a security engineer explaining a specific finding from an "
    "automated GitHub repository scan to a developer who is not a security "
    "expert and likely built this repository with an AI coding assistant. "
    "Respond with ONLY a valid JSON object matching exactly this structure — "
    "no markdown fences, no extra keys, no explanation outside the JSON:\n"
    "{\n"
    '  "why_it_exists": "one sentence — what mistake in the code/config causes this",\n'
    '  "security_impact": "one sentence — what an attacker can actually do",\n'
    '  "exploitation": "one paragraph — conceptual explanation of how it could be abused (no working exploit code)",\n'
    '  "recommended_fix": "one paragraph — concrete steps to remediate, referencing the file/line given",\n'
    '  "best_practices": ["bullet 1", "bullet 2", "bullet 3"],\n'
    '  "framework_examples": {"Before": "the vulnerable line/block, as given", "After": "the corrected version"}\n'
    "}\n"
    "framework_examples MUST be a Before/After code diff for the exact file "
    "given, not a generic server config — this is a source-code finding, not "
    "a live-server one. best_practices should have 2-4 items."
)


def build_fix_messages(finding: Finding) -> list[dict]:
    """Repo findings (`finding.file_path` is set) get `REPO_FIX_SYSTEM` and a
    file/line-aware prompt; URL findings (file_path is always None for those
    — see models.Finding) get the original FIX_SYSTEM, unchanged. One
    function, not two, since `ai/fixes.py`'s single call site shouldn't need
    to know which kind of finding it's holding.
    """
    is_repo_finding = finding.file_path is not None
    lines = [
        f"Finding: {finding.title}",
        f"Severity: {finding.severity.value}",
        f"Category: {finding.category}",
    ]
    if is_repo_finding:
        location = f"{finding.file_path}:{finding.line}" if finding.line else finding.file_path
        lines.append(f"Location: {location}")
    if finding.owasp:
        lines.append(f"OWASP: {finding.owasp}")
    if finding.evidence:
        lines.append(f"Evidence: {finding.evidence}")
    if finding.description:
        lines.append(f"Description: {finding.description}")
    return [
        {"role": "system", "content": REPO_FIX_SYSTEM if is_repo_finding else FIX_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


# ── Chatbot ─────────────────────────────────────────────────────────────────

CHAT_SYSTEM = (
    "You are a security assistant for Sentinels, a passive website security scanner. "
    "You have full access to the results of one completed scan. "
    "Answer questions about the scan findings, their severity, and how to fix them. "
    "Be concise and plain-English. "
    "If asked about something the scan didn't check, say so clearly — do not guess or hallucinate findings. "
    "Never suggest active or offensive testing. "
    "Use the scan data provided; do not invent details not in it."
)

_MAX_CHAT_FINDINGS = 20
_MAX_CHAT_TURNS = 10  # last N turns of conversation kept in context


def build_chat_messages(
    report: ScanReport,
    checklist: list[ChecklistItem],
    history: list[dict],  # [{"role": "user"|"assistant", "content": "..."}]
    question: str,
) -> list[dict]:
    """Build the full messages list for one chatbot turn.

    Stuffs the scan digest into the system message rather than using RAG —
    a finished scan is 3-6k tokens, well within context. Revisit only if
    scans grow 10x.
    """
    # Scan digest as a plain-text context block appended to the system prompt.
    digest_lines = [
        "",
        "=== SCAN CONTEXT ===",
        f"URL: {report.url}",
        f"Score: {report.score}/100 (grade {report.grade})",
        f"Scanned at: {report.scanned_at}",
    ]
    if report.deployment_status:
        digest_lines.append(f"Deployment status: {report.deployment_status}")
    if report.readiness_score is not None:
        digest_lines.append(f"Readiness score: {report.readiness_score}/100")

    digest_lines.append("")
    digest_lines.append("Findings:")
    for f in report.findings[:_MAX_CHAT_FINDINGS]:
        line = f"  [{f.status.value.upper()}] {f.severity.value} — {f.title}"
        if f.affected_url:
            line += f" (on {f.affected_url})"
        if f.confidence is not None:
            line += f" | confidence {f.confidence:.0%}"
        if f.evidence:
            line += f" | Evidence: {f.evidence[:120]}"
        digest_lines.append(line)

    if report.subdomains:
        digest_lines.append("")
        digest_lines.append("Discovered subdomains:")
        for s in report.subdomains:
            entry = f"  {s.host} ({s.record_type} {s.record_value}, via {s.source})"
            if s.http_status is not None:
                entry += f" — HTTP {s.http_status}"
            if s.issue_count:
                entry += f", {s.issue_count} issue(s)"
            digest_lines.append(entry)

    if checklist:
        digest_lines.append("")
        digest_lines.append("Checklist:")
        for item in checklist:
            digest_lines.append(f"  {item.state.upper()} ({item.tier}) — {item.title}")

    system_with_context = CHAT_SYSTEM + "\n".join(digest_lines)

    messages: list[dict] = [{"role": "system", "content": system_with_context}]

    # Last N turns of prior conversation
    for turn in history[-(_MAX_CHAT_TURNS * 2):]:
        messages.append(turn)

    # Current question
    messages.append({"role": "user", "content": question})
    return messages
