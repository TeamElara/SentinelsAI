"""Builds the standalone HTML document behind the PDF export.

Extracted from `pdf.py` (M17) so the "what does a report look like"
logic sits in one place, separate from "print this HTML to a PDF" (still in
`pdf.py`) and separate from the other export formats. `group_by_category` is
exported (not `_`-prefixed) because `markdown.py` reuses it for the same
worst-category-first, worst-problem-first ordering — one grouping rule, not
one per format.

The six colours below are hand-copied from `frontend/app/globals.css`'s
`@theme` block rather than imported — there's no build step connecting a
Python file to a Tailwind CSS file, so the two have to be kept in sync by a
human. Same tradeoff A13 already made for `frontend/lib/api.ts` mirroring
`backend/models.py`.
"""
from __future__ import annotations

from html import escape
from math import pi
from typing import Optional

from models import AgentResult, ChecklistItem, EvidenceItem, Finding, FixSuggestion, ScanReport

_INK = "#0e0e0d"
_PARCHMENT = "#d9d7d4"
_MUTED = "#8b8884"
_RULE = "rgba(217, 215, 212, 0.14)"
_CRITICAL = "#8b3a2f"

# Font stacks chosen to need no network access — see the learning note for
# why Google Fonts (what the real frontend uses) was rejected here.
_DISPLAY_FONT = "Georgia, 'Times New Roman', serif"
_MONO_FONT = "'Courier New', ui-monospace, monospace"
_BODY_FONT = "'Segoe UI', Arial, sans-serif"

_LABEL = (
    f"font-family:{_MONO_FONT};font-size:10px;letter-spacing:0.3em;"
    f"text-transform:uppercase;color:{_MUTED};margin:0;"
)

# Mirrors frontend/lib/findings.ts SEVERITY_RANK.
_SEVERITY_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Info": 0}

_HEADLINE_SEVERITIES = ("Critical", "High", "Medium", "Low")

_RADIUS = 68
_CIRCUMFERENCE = 2 * pi * _RADIUS


def group_by_category(
    findings: list[Finding],
) -> list[tuple[str, list[Finding], list[Finding]]]:
    """Same grouping and ordering as `groupByCategory` in
    `frontend/lib/findings.ts`: worst category first, problems before passed
    checks within a category, worst problem first. Reimplemented here — not
    imported — because this code runs in Python, findings.ts runs in the
    browser, and nothing bridges the two languages."""
    problems_by_cat: dict[str, list[Finding]] = {}
    passed_by_cat: dict[str, list[Finding]] = {}
    order: list[str] = []

    for f in findings:
        if f.category not in problems_by_cat:
            problems_by_cat[f.category] = []
            passed_by_cat[f.category] = []
            order.append(f.category)
        bucket = passed_by_cat if f.status.value == "pass" else problems_by_cat
        bucket[f.category].append(f)

    for problems in problems_by_cat.values():
        problems.sort(key=lambda f: _SEVERITY_RANK[f.severity.value], reverse=True)

    def worst_rank(category: str) -> int:
        problems = problems_by_cat[category]
        return _SEVERITY_RANK[problems[0].severity.value] if problems else -1

    order.sort(key=lambda c: (worst_rank(c), len(problems_by_cat[c])), reverse=True)
    return [(c, problems_by_cat[c], passed_by_cat[c]) for c in order]


def _evidence_items_html(items: list[EvidenceItem]) -> str:
    """One block per structured evidence item — request, headers, DNS record,
    etc. Additive: a finding with none renders nothing extra (M4/M5's shape,
    unchanged by this export)."""
    if not items:
        return ""
    blocks = []
    for item in items:
        blocks.append(
            f'<div style="margin-top:8px;padding:8px 10px;background:rgba(255,255,255,0.03);'
            f'border:1px solid {_RULE};">'
            f'<p style="margin:0;font-family:{_MONO_FONT};font-size:9px;letter-spacing:0.2em;'
            f'text-transform:uppercase;color:{_MUTED};">{escape(item.kind.value)} &middot; {escape(item.label)}</p>'
            f'<p style="margin:4px 0 0;font-family:{_MONO_FONT};font-size:10px;line-height:1.5;'
            f'color:{_MUTED};word-break:break-word;">{escape(item.content)}</p>'
            f"</div>"
        )
    return "".join(blocks)


def _fix_html(fix: FixSuggestion) -> str:
    """The cached AI fix suggestion for one finding, if any exist for this
    scan (M19). A finding with no cached fix renders nothing extra — the
    export must still look complete without it."""
    best_practices = "".join(f"<li>{escape(bp)}</li>" for bp in fix.best_practices)
    return f"""
    <div style="margin-top:14px;padding:12px 14px;background:rgba(255,255,255,0.03);
                border:1px solid {_RULE};">
      <p style="margin:0;font-family:{_MONO_FONT};font-size:9px;letter-spacing:0.25em;
                text-transform:uppercase;color:{_MUTED};">AI fix suggestion</p>
      <p style="margin:10px 0 0;font-size:12px;line-height:1.6;">
        <span style="color:{_MUTED};">Why it exists — </span>{escape(fix.why_it_exists)}
      </p>
      <p style="margin:8px 0 0;font-size:12px;line-height:1.6;">
        <span style="color:{_MUTED};">Security impact — </span>{escape(fix.security_impact)}
      </p>
      <p style="margin:8px 0 0;font-size:12px;line-height:1.6;">
        <span style="color:{_MUTED};">Recommended fix — </span>{escape(fix.recommended_fix)}
      </p>
      {f'<ul style="margin:8px 0 0;padding-left:18px;font-size:12px;line-height:1.6;">{best_practices}</ul>' if best_practices else ""}
    </div>"""


def _finding_html(f: Finding, fix: Optional[FixSuggestion] = None) -> str:
    critical = f.severity.value == "Critical"
    border = _CRITICAL if critical else _RULE
    label_color = _CRITICAL if critical else _MUTED

    rows = [
        f'<li style="border-left:2px solid {border};padding-left:20px;margin-top:32px;">',
        f'<div style="font-family:{_MONO_FONT};font-size:10px;letter-spacing:0.2em;'
        f'text-transform:uppercase;color:{label_color};">{escape(f.severity.value)}'
        f'&nbsp;&nbsp;<span style="color:{_MUTED};">{escape(f.status.value)}</span></div>',
        f'<h4 style="margin:8px 0 0;font-size:17px;font-weight:600;line-height:1.4;">'
        f"{escape(f.title)}</h4>",
    ]
    if f.description:
        rows.append(
            f'<p style="margin:6px 0 0;color:{_MUTED};font-size:13px;line-height:1.6;">'
            f"{escape(f.description)}</p>"
        )
    if f.evidence:
        rows.append(
            f'<p style="margin:10px 0 0;font-family:{_MONO_FONT};font-size:11px;'
            f'line-height:1.6;color:{_MUTED};word-break:break-word;">{escape(f.evidence)}</p>'
        )
    rows.append(_evidence_items_html(f.evidence_items))
    if f.remediation:
        rows.append(
            f'<p style="margin:10px 0 0;font-size:13px;line-height:1.6;">'
            f'<span style="color:{_MUTED};">Fix — </span>{escape(f.remediation)}</p>'
        )
    if f.owasp:
        rows.append(
            f'<p style="margin:10px 0 0;font-family:{_MONO_FONT};font-size:10px;'
            f'letter-spacing:0.15em;text-transform:uppercase;color:{_MUTED};">'
            f"{escape(f.owasp)}</p>"
        )
    if fix is not None:
        rows.append(_fix_html(fix))
    rows.append("</li>")
    return "".join(rows)


def _score_ring_svg(score: int, grade: str) -> str:
    """Same arc math as `frontend/components/ScoreRing.tsx`: the outline is
    one dash the full circumference, then `stroke-dashoffset` hides the part
    that shouldn't be drawn — the arc's length carries the score, not colour."""
    clamped = max(0, min(100, score))
    offset = _CIRCUMFERENCE * (1 - clamped / 100)
    return f"""
    <div style="position:relative;width:160px;height:160px;flex-shrink:0;">
      <svg viewBox="0 0 160 160" width="160" height="160"
           style="transform:rotate(-90deg);">
        <circle cx="80" cy="80" r="{_RADIUS}" fill="none" stroke="{_RULE}" stroke-width="1" />
        <circle cx="80" cy="80" r="{_RADIUS}" fill="none" stroke="{_PARCHMENT}" stroke-width="1"
                stroke-dasharray="{_CIRCUMFERENCE:.3f}" stroke-dashoffset="{offset:.3f}" />
      </svg>
      <div style="position:absolute;inset:0;display:flex;flex-direction:column;
                  align-items:center;justify-content:center;">
        <span style="font-family:{_DISPLAY_FONT};font-size:56px;line-height:1;">{escape(grade)}</span>
        <span style="margin-top:8px;font-family:{_MONO_FONT};font-size:10px;letter-spacing:0.25em;
                     text-transform:uppercase;color:{_MUTED};">{clamped}/100</span>
      </div>
    </div>"""


def _agent_log_html(agents: list[AgentResult], total_ms: int) -> str:
    rows = []
    for a in agents:
        detail = (
            escape(a.error)
            if a.error
            else f"{len(a.findings)} checks &middot; {a.duration_ms}ms"
        )
        rows.append(
            f'<div style="display:flex;justify-content:space-between;gap:24px;'
            f'padding:12px 16px;border-bottom:1px solid {_RULE};">'
            f'<span style="font-family:{_MONO_FONT};font-size:12px;letter-spacing:0.2em;'
            f'text-transform:uppercase;">{escape(a.agent)}</span>'
            f'<span style="font-family:{_MONO_FONT};font-size:11px;color:{_MUTED};">{detail}</span>'
            f"</div>"
        )
    return f"""
    <section style="margin-top:56px;">
      <h2 style="{_LABEL}">Agent log</h2>
      <div style="margin-top:20px;background:rgba(255,255,255,0.04);
                  border:1px solid rgba(255,255,255,0.08);">{''.join(rows)}</div>
      <p style="margin:14px 0 0;font-family:{_MONO_FONT};font-size:11px;color:{_MUTED};">
        Total {total_ms}ms — less than the sum above, because the five agents run
        concurrently.
      </p>
    </section>"""


_CHECKLIST_TIER_LABEL = {
    "auto": "Auto-verified",
    "inferred": "Passively inferred",
    "self_attested": "Self-attested",
}


def _checklist_html(checklist: list[ChecklistItem]) -> str:
    """The deployment checklist (M9-M11), added to exports in M19. Empty for
    any scan run before the checklist existed — renders nothing extra."""
    if not checklist:
        return ""
    rows = []
    for item in checklist:
        color = (
            _CRITICAL
            if item.state == "fail"
            else _MUTED
            if item.state in ("unknown",)
            else _PARCHMENT
        )
        rows.append(
            f'<div style="padding:10px 0;border-bottom:1px solid {_RULE};">'
            f'<div style="display:flex;justify-content:space-between;gap:16px;">'
            f'<span style="font-size:13px;">{escape(item.title)}</span>'
            f'<span style="font-family:{_MONO_FONT};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.15em;color:{color};">{escape(item.state)}</span>'
            f"</div>"
            f'<p style="margin:4px 0 0;font-family:{_MONO_FONT};font-size:9px;text-transform:uppercase;'
            f'letter-spacing:0.15em;color:{_MUTED};">{escape(_CHECKLIST_TIER_LABEL.get(item.tier, item.tier))}</p>'
            f'<p style="margin:4px 0 0;font-size:12px;color:{_MUTED};line-height:1.5;">{escape(item.explanation)}</p>'
            f"</div>"
        )
    return f"""
    <section style="margin-top:48px;">
      <h2 style="{_LABEL}">Deployment checklist</h2>
      <div style="margin-top:16px;">{''.join(rows)}</div>
    </section>"""


def render_html(
    report: ScanReport, fixes: Optional[dict[str, FixSuggestion]] = None
) -> str:
    """Build the full standalone HTML document Playwright will print.

    `fixes` (M19) maps finding slug -> cached AI fix; None/empty is the
    normal case for a scan nobody has requested fixes for yet, and the
    report still renders completely without that section.
    """
    fixes = fixes or {}

    counts_html = "".join(
        f'<div style="margin-right:28px;">'
        f'<div style="{_LABEL}">{sev}</div>'
        f'<div style="margin-top:4px;font-family:{_DISPLAY_FONT};font-size:22px;'
        f'{f"color:{_CRITICAL};" if sev == "Critical" and report.counts.get(sev, 0) else ""}">'
        f"{report.counts.get(sev, 0)}</div></div>"
        for sev in _HEADLINE_SEVERITIES
    )

    readiness_html = ""
    if report.readiness_score is not None and report.deployment_status:
        status_color = (
            _CRITICAL if report.deployment_status == "blocked" else _PARCHMENT
        )
        readiness_html = (
            f'<div style="margin-left:auto;text-align:right;">'
            f'<div style="{_LABEL}">Deployment</div>'
            f'<div style="margin-top:4px;font-family:{_MONO_FONT};font-size:13px;'
            f'text-transform:uppercase;letter-spacing:0.1em;color:{status_color};">'
            f"{escape(report.deployment_status)} &middot; {report.readiness_score}/100</div></div>"
        )

    summary_html = ""
    if report.summary:
        summary_html = f"""
        <section style="margin-top:48px;">
          <h2 style="{_LABEL}">Assessment</h2>
          <p style="margin:16px 0 0;font-family:{_DISPLAY_FONT};font-size:19px;line-height:1.6;">
            {escape(report.summary)}
          </p>
        </section>"""

    groups = group_by_category(report.findings)
    problem_count = sum(len(problems) for _, problems, _ in groups)

    if problem_count == 0:
        findings_html = '<p style="margin-top:16px;font-size:18px;">Every check passed. Nothing to report.</p>'
    else:
        sections = []
        for category, problems, passed in groups:
            passed_html = ""
            if passed:
                titles = " &middot; ".join(escape(f.title) for f in passed)
                passed_html = (
                    f'<p style="margin:20px 0 0;font-family:{_MONO_FONT};font-size:10px;'
                    f'line-height:1.6;color:{_MUTED};"><span style="text-transform:uppercase;'
                    f'letter-spacing:0.2em;">Passed — </span>{titles}</p>'
                )
            sections.append(
                f'<section style="margin-top:48px;">'
                f'<h3 style="font-family:{_DISPLAY_FONT};font-size:24px;margin:0;">{escape(category)}</h3>'
                f'<ul style="list-style:none;margin:0;padding:0;">'
                f'{"".join(_finding_html(f, fixes.get(f.id)) for f in problems)}</ul>'
                f"{passed_html}</section>"
            )
        findings_html = "".join(sections)

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 48px 56px;
    background: {_INK};
    color: {_PARCHMENT};
    font-family: {_BODY_FONT};
  }}
</style>
</head>
<body>
  <header style="display:flex;flex-wrap:wrap;align-items:center;gap:40px;">
    {_score_ring_svg(report.score, report.grade)}
    <div style="min-width:0;flex:1;">
      <p style="{_LABEL}">Inspection record</p>
      <p style="margin:10px 0 0;font-family:{_MONO_FONT};font-size:16px;word-break:break-all;">
        {escape(report.url)}
      </p>
      <p style="margin:6px 0 0;font-family:{_MONO_FONT};font-size:11px;color:{_MUTED};">
        {escape(report.scanned_at)} &middot; {report.duration_ms}ms &middot; Scorer: {escape(report.scorer_version)}
      </p>
      <div style="margin-top:20px;display:flex;flex-wrap:wrap;">{counts_html}</div>
    </div>
    {readiness_html}
  </header>

  {summary_html}

  <section style="margin-top:48px;">
    <h2 style="{_LABEL}">Findings</h2>
    {findings_html}
  </section>

  {_checklist_html(report.checklist)}

  {_agent_log_html(report.agents, report.duration_ms)}
</body>
</html>"""
