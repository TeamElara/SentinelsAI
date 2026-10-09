"""Markdown exporter — a plain-text report for pasting into an issue, PR, or wiki page.

Reuses `html_doc.group_by_category` for the same worst-category-first,
worst-problem-first ordering as the PDF and the frontend — one grouping
rule shared across every format, rather than three copies that could drift.
"""
from __future__ import annotations

from typing import Optional

from models import Finding, FixSuggestion, ScanReport
from report.html_doc import group_by_category

_CHECKLIST_TIER_LABEL = {
    "auto": "Auto-verified",
    "inferred": "Passively inferred",
    "self_attested": "Self-attested",
}


def _finding_md(f: Finding, fix: Optional[FixSuggestion]) -> list[str]:
    lines = [f"### {f.title}", "", f"**{f.severity.value} · {f.status.value.upper()}**"]
    if f.description:
        lines += ["", f.description]
    if f.evidence:
        lines += ["", f"> {f.evidence}"]
    for item in f.evidence_items:
        lines += ["", f"> **{item.kind.value}** ({item.label}): {item.content}"]
    if f.remediation:
        lines += ["", f"**Fix:** {f.remediation}"]
    if f.owasp:
        lines += ["", f"_{f.owasp}_"]
    if fix is not None:
        lines += [
            "",
            "**AI fix suggestion**",
            "",
            f"- Why it exists: {fix.why_it_exists}",
            f"- Security impact: {fix.security_impact}",
            f"- Recommended fix: {fix.recommended_fix}",
        ]
        if fix.best_practices:
            lines.append("- Best practices:")
            lines += [f"  - {bp}" for bp in fix.best_practices]
    return lines


class MarkdownExporter:
    format_id = "markdown"
    media_type = "text/markdown"
    extension = "md"

    async def render(
        self, report: ScanReport, fixes: Optional[dict[str, FixSuggestion]] = None
    ) -> bytes:
        fixes = fixes or {}
        lines = [
            f"# Sentinels report — {report.url}",
            "",
            f"**Score:** {report.score}/100 (grade {report.grade}{' — provisional' if report.provisional else ''})  ",
            f"**Scorer:** {report.scorer_version}",
            f"**Scanned:** {report.scanned_at} · {report.duration_ms}ms",
        ]
        if report.deployment_status and report.readiness_score is not None:
            lines.append(
                f"**Deployment:** {report.deployment_status} (readiness {report.readiness_score}/100)"
            )

        lines += ["", "## Check coverage"]
        if report.provisional:
            lines += ["", "Incomplete or unrecorded coverage. Score and grade are provisional; deployment readiness is unknown."]
        for agent in report.agents:
            lines.append(f"- **{agent.agent}: {agent.coverage_status}**")
            for check in agent.coverage:
                lines.append(f"  - {check.check}: **{check.status}** — {check.reason}")
        if report.summary:
            lines += ["", "## Assessment", "", report.summary]

        groups = group_by_category(report.findings)
        problem_count = sum(len(problems) for _, problems, _ in groups)

        lines += ["", "## Findings"]
        if problem_count == 0:
            lines += ["", "No issues observed within completed checks. See coverage for limitations."]
        else:
            for category, problems, passed in groups:
                lines += ["", f"## {category}"]
                for f in problems:
                    lines += [""]
                    lines += _finding_md(f, fixes.get(f.id))
                if passed:
                    titles = " · ".join(p.title for p in passed)
                    lines += ["", f"_Passed — {titles}_"]

        if report.checklist:
            lines += ["", "## Deployment checklist"]
            for item in report.checklist:
                tier_label = _CHECKLIST_TIER_LABEL.get(item.tier, item.tier)
                lines.append(
                    f"- **{item.state.upper()}** ({tier_label}) — {item.title}: {item.explanation}"
                )

        lines += ["", "## Agent log"]
        for a in report.agents:
            detail = a.error if a.error else f"{len(a.findings)} checks · {a.duration_ms}ms"
            lines.append(f"- **{a.agent}** — {detail}")
        lines += [
            "",
            f"Total {report.duration_ms}ms — less than the sum above, because the "
            "agents run concurrently.",
        ]

        return "\n".join(lines).encode("utf-8")
