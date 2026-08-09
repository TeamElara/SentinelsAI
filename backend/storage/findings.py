"""Write and read path for `agent_runs`, `findings`, and `evidence_items`."""
from __future__ import annotations

import sqlite3

from models import AgentResult, EvidenceItem


def save_agent_results(
    conn: sqlite3.Connection, scan_id: str, agent_results: list[AgentResult]
) -> None:
    """Insert one `agent_runs` row per agent, one `findings` row per finding
    it produced, and one `evidence_items` row per piece of evidence attached
    to that finding. Takes an open connection rather than opening its own,
    so callers (currently `storage.scans.save_scan`) can run this in the
    same transaction as the parent `scans` row.
    """
    for result in agent_results:
        conn.execute(
            """
            INSERT INTO agent_runs (scan_id, agent, duration_ms, error, verdict)
            VALUES (?, ?, ?, ?, ?)
            """,
            (scan_id, result.agent, result.duration_ms, result.error, None),
        )

        for finding in result.findings:
            cursor = conn.execute(
                """
                INSERT INTO findings (
                    scan_id, finding_key, agent, title, category,
                    severity, status, owasp, evidence, description, remediation,
                    file_path, line, affected_url, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    finding.id,
                    result.agent,
                    finding.title,
                    finding.category,
                    finding.severity.value,
                    finding.status.value,
                    finding.owasp,
                    finding.evidence,
                    finding.description,
                    finding.remediation,
                    finding.file_path,
                    finding.line,
                    finding.affected_url,
                    finding.confidence,
                ),
            )
            finding_id = cursor.lastrowid

            for item in finding.evidence_items:
                conn.execute(
                    """
                    INSERT INTO evidence_items (
                        finding_id, kind, label, content, content_type,
                        collected_at, agent
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        finding_id,
                        item.kind.value,
                        item.label,
                        item.content,
                        item.content_type,
                        item.collected_at,
                        item.agent,
                    ),
                )


def load_evidence(
    conn: sqlite3.Connection, scan_id: str
) -> dict[int, list[EvidenceItem]]:
    """Fetch every evidence item for a scan in one query, grouped by the
    `findings.id` it belongs to. One query rather than one-per-finding —
    a scan has ~10-25 findings, and N+1 queries here would be the same
    mistake `load_evidence` exists to avoid.
    """
    rows = conn.execute(
        """
        SELECT e.finding_id, e.kind, e.label, e.content, e.content_type,
               e.collected_at, e.agent
        FROM evidence_items e
        JOIN findings f ON f.id = e.finding_id
        WHERE f.scan_id = ?
        ORDER BY e.id
        """,
        (scan_id,),
    ).fetchall()

    by_finding: dict[int, list[EvidenceItem]] = {}
    for row in rows:
        by_finding.setdefault(row["finding_id"], []).append(
            EvidenceItem(
                kind=row["kind"],
                label=row["label"],
                content=row["content"],
                content_type=row["content_type"] or "text/plain",
                collected_at=row["collected_at"],
                agent=row["agent"],
            )
        )
    return by_finding
