"""Read/write path for the `scans` and `checklist_items` tables."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from db import get_connection
from models import AgentResult, ChecklistItem, Finding, RepoFileEntry, ScanReport, ScanSummary, Severity, Status
from scoring import count_by_severity
from storage.findings import load_evidence, save_agent_results
from storage.repo_files import save_repo_files
from storage.subdomains import load_subdomains, save_subdomains


def save_checklist(conn: sqlite3.Connection, scan_id: str, items: list[ChecklistItem]) -> None:
    """Persist all checklist items for one scan in the open connection."""
    for item in items:
        conn.execute(
            """
            INSERT INTO checklist_items
                (scan_id, item_key, title, tier, state, explanation, suggested_fix, agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id, item.item_key, item.title, item.tier,
                item.state, item.explanation, item.suggested_fix, item.agent,
            ),
        )


def load_checklist(conn: sqlite3.Connection, scan_id: str) -> list[ChecklistItem]:
    """Reconstruct all checklist items for a scan from the DB."""
    rows = conn.execute(
        "SELECT * FROM checklist_items WHERE scan_id = ? ORDER BY id",
        (scan_id,),
    ).fetchall()
    return [
        ChecklistItem(
            item_key=row["item_key"],
            title=row["title"],
            tier=row["tier"],
            state=row["state"],
            explanation=row["explanation"],
            suggested_fix=row["suggested_fix"] or "",
            agent=row["agent"],
        )
        for row in rows
    ]


def update_checklist_item(scan_id: str, item_key: str, state: str, explanation: str) -> bool:
    """Update the state/explanation of a self-attested checklist item.

    Only self_attested items can be updated — the WHERE clause enforces this,
    so auto/inferred items silently do nothing and return False.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE checklist_items
            SET state = ?, explanation = ?
            WHERE scan_id = ? AND item_key = ? AND tier = 'self_attested'
            """,
            (state, explanation, scan_id, item_key),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def scan_owner(scan_id: str) -> int | None:
    """Which user owns a scan, or None for the unowned ones.

    None means two different things that callers treat identically: the scan
    predates PLAN-v5 Stage 0 and has no owner, or there is no such scan. Both
    end the same way — a 404 or a permitted read — so they aren't separated.
    """
    conn = get_connection()
    try:
        row = conn.execute("SELECT user_id FROM scans WHERE id = ?", (scan_id,)).fetchone()
        return row["user_id"] if row is not None else None
    finally:
        conn.close()


def list_scans(limit: int = 20, offset: int = 0, user_id: int | None = None) -> list[ScanSummary]:
    """Return scan summaries, newest first.

    `user_id` scopes the list to one person's scans plus every unowned one.
    Unowned scans (`user_id IS NULL`) are the ones taken before Stage 0 added
    identity — hiding them would make a working install look empty after an
    upgrade, so they stay visible to everyone rather than being orphaned.
    """
    conn = get_connection()
    try:
        where = "" if user_id is None else "WHERE user_id = ? OR user_id IS NULL"
        params: tuple = (limit, offset) if user_id is None else (user_id, limit, offset)
        rows = conn.execute(
            f"""
            SELECT id, url, target_type, score, grade, scanned_at, duration_ms, summary,
                   readiness_score, deployment_status, scorer_version
            FROM scans
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [
            ScanSummary(
                id=row["id"],
                url=row["url"],
                target_type=row["target_type"],
                score=row["score"],
                grade=row["grade"],
                scorer_version=row["scorer_version"],
                scanned_at=row["scanned_at"],
                duration_ms=row["duration_ms"],
                summary=row["summary"] or "",
                readiness_score=row["readiness_score"],
                deployment_status=row["deployment_status"],
            )
            for row in rows
        ]
    finally:
        conn.close()


def get_scan(scan_id: str) -> ScanReport | None:
    """Reconstruct a full ScanReport from the DB, or None if not found."""
    conn = get_connection()
    try:
        scan_row = conn.execute(
            "SELECT * FROM scans WHERE id = ?", (scan_id,)
        ).fetchone()
        if scan_row is None:
            return None

        agent_rows = conn.execute(
            "SELECT * FROM agent_runs WHERE scan_id = ? ORDER BY id", (scan_id,)
        ).fetchall()

        finding_rows = conn.execute(
            "SELECT * FROM findings WHERE scan_id = ? ORDER BY id", (scan_id,)
        ).fetchall()
        evidence_by_finding_id = load_evidence(conn, scan_id)

        findings_by_agent: dict[str, list[Finding]] = {}
        all_findings: list[Finding] = []
        for row in finding_rows:
            f = Finding(
                id=row["finding_key"],
                title=row["title"],
                category=row["category"],
                severity=Severity(row["severity"]),
                status=Status(row["status"]),
                owasp=row["owasp"],
                evidence=row["evidence"] or "",
                description=row["description"] or "",
                remediation=row["remediation"] or "",
                agent=row["agent"],
                evidence_items=evidence_by_finding_id.get(row["id"], []),
                file_path=row["file_path"],
                line=row["line"],
                affected_url=row["affected_url"],
                confidence=row["confidence"],
            )
            all_findings.append(f)
            findings_by_agent.setdefault(row["agent"], []).append(f)

        agents = [
            AgentResult(
                agent=ar["agent"],
                findings=findings_by_agent.get(ar["agent"], []),
                duration_ms=ar["duration_ms"],
                error=ar["error"],
            )
            for ar in agent_rows
        ]

        # The same function a live scan uses (orchestrator._finalize), rather
        # than a second tally written by hand here. The hand-written one
        # counted PASS findings too, so a stored scan's severity counts came
        # back higher than the ones shown the moment the scan finished -- a
        # small discrepancy while agents emit ~13 findings, a glaring one once
        # they emit inventory findings by the dozen.
        counts = count_by_severity(all_findings)

        checklist = load_checklist(conn, scan_id)
        subdomains = load_subdomains(conn, scan_id)

        return ScanReport(
            id=scan_row["id"],
            url=scan_row["url"],
            target_type=scan_row["target_type"],
            scanned_at=scan_row["scanned_at"],
            duration_ms=scan_row["duration_ms"],
            score=scan_row["score"],
            grade=scan_row["grade"],
            scorer_version=scan_row["scorer_version"],
            summary=scan_row["summary"] or "",
            counts=counts,
            findings=all_findings,
            agents=agents,
            readiness_score=scan_row["readiness_score"],
            deployment_status=scan_row["deployment_status"],
            checklist=checklist,
            subdomains=subdomains,
        )
    finally:
        conn.close()


def delete_scan(scan_id: str) -> bool:
    """Delete a scan and all its child rows (CASCADE). Returns False if not found."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def save_scan(
    report: ScanReport,
    repo_files: list[RepoFileEntry] | None = None,
    user_id: int | None = None,
) -> None:
    """Persist a finished scan — the scan row, agent runs, findings, and
    checklist items — in a single transaction. `report.id` must already be
    set by the caller (`orchestrator._finalize` generates it).

    `repo_files` is additive and optional: only `repo_orchestrator._finalize`
    ever passes it (R12), so the URL side's call site here never changes.

    `user_id` is likewise additive (PLAN-v5 Stage 0) and defaults to None, so
    every existing caller and test keeps working unchanged and simply records
    an unowned scan."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO scans (
                id, url, target_type, scanned_at, duration_ms, score, grade,
                summary, readiness_score, deployment_status, created_at, user_id, scorer_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.id,
                report.url,
                report.target_type,
                report.scanned_at,
                report.duration_ms,
                report.score,
                report.grade,
                report.summary,
                report.readiness_score,
                report.deployment_status,
                datetime.now(timezone.utc).isoformat(),
                user_id,
                report.scorer_version,
            ),
        )
        save_agent_results(conn, report.id, report.agents)
        save_checklist(conn, report.id, report.checklist)
        if repo_files:
            save_repo_files(conn, report.id, repo_files)
        if report.subdomains:
            save_subdomains(conn, report.id, report.subdomains)
        conn.commit()
    finally:
        conn.close()
