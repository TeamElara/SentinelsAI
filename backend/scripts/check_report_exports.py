"""Generate an offline report fixture through the real PDF export path.

Run from backend: python scripts/check_report_exports.py <output-directory>.
The fixture deliberately has incomplete coverage and attacker-shaped markup.
"""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import AgentResult, CheckCoverage, ScanReport
from report.pdf import generate_pdf
from report.html_doc import render_html
from report.markdown import MarkdownExporter
from report.json_export import JsonExporter


async def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = ScanReport(id="offline-coverage", url="https://example.com", scanned_at="2026-10-08",
        duration_ms=12, score=100, grade="A", deployment_status="ready", readiness_score=0,
        agents=[AgentResult(agent="headers", coverage=[
            CheckCoverage(check="Header inspection <script>", status="unavailable", reason="Fixture network timeout; not evidence of a secure target."),
            CheckCoverage(check="Redirect inspection", status="skipped", reason="Fixture robots policy blocked this path."),
        ])])
    (output / "coverage.html").write_text(render_html(report), encoding="utf-8")
    (output / "coverage.md").write_bytes(await MarkdownExporter().render(report))
    (output / "coverage.json").write_bytes(await JsonExporter().render(report))
    pdf = await generate_pdf(report)
    assert pdf.startswith(b"%PDF")
    (output / "coverage.pdf").write_bytes(pdf)
    print(f"Real Chromium PDF export passed; {len(pdf)} bytes. Fixtures: {output}")


asyncio.run(main())
