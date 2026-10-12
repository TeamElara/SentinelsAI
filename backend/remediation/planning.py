"""Orchestrates one fix-plan request: load the finding, find its Fixer,
build a `FileSource` against the repo's *current* GitHub state, produce and
validate a `FixPlan`. The one place `main.py`'s three fix/plan endpoints
share logic -- mirrors `ai/fixes.py`'s role for the AI-suggestion endpoint.
"""
from __future__ import annotations

import asyncio

import io
import zipfile

import httpx

from models import Finding, FixPlan, ScanReport
from remediation.linking import NoRepoTarget, repo_target
from remediation.patch import PlanValidationError, validate_plan
from remediation.registry import fixer_for
from remediation.source import FileSource, resolve_default_ref
from storage.remediation import list_fix_plans, save_fix_plan

# Kept as a distinct name from `linking.NoRepoTarget` (Stage D) even though
# both end up meaning "nothing to build a FileSource against": callers that
# want the specific "link a repository first" wording catch NoRepoTarget,
# while this stays the general "not fixable this way at all" refusal for a
# scan whose `target_type` isn't one of the two this pipeline understands.
class NotARepoScan(ValueError):
    """Raised when a fix is requested against a scan with no repository to
    build a patch against, and no linkable one either."""


def _finding(report: ScanReport, finding_key: str) -> Finding | None:
    return next((f for f in report.findings if f.id == finding_key), None)


async def _file_source(report: ScanReport, client: httpx.AsyncClient) -> FileSource:
    try:
        owner, repo, ref = await asyncio.to_thread(repo_target, report)
    except NoRepoTarget as exc:
        raise NotARepoScan(str(exc)) from exc
    if ref is None:
        ref = await resolve_default_ref(client, owner, repo)
    return FileSource(client=client, owner=owner, repo=repo, ref=ref)


async def preview_plan(report: ScanReport, finding_key: str) -> FixPlan | None:
    """`GET .../fix/plan` -- compute a fix plan live, never persisted.

    `None` means "no deterministic fixer for this finding" (suggest-only,
    per the fixability tiers) -- a normal answer, not an error; the frontend
    falls back to the existing AI `FixSuggestionPanel`.
    """
    finding = _finding(report, finding_key)
    if finding is None:
        return None
    fixer = fixer_for(finding)
    if fixer is None:
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        files = await _file_source(report, client)
        plan = await fixer.plan(finding, files)

    if plan is None:
        return None
    validate_plan(finding, plan)
    return plan


async def plan_and_save(
    report: ScanReport, finding_keys: list[str]
) -> dict[str, FixPlan | None]:
    """`POST /scans/{id}/fix/plan` -- plan every requested finding and
    persist the ones that produced a valid plan.

    Every key gets an entry in the result, `None` for "not fixable"
    (unknown finding, no fixer, or a rejected plan) -- one unfixable finding
    in a batch is never a reason to fail the whole request.
    """
    results: dict[str, FixPlan | None] = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        files = await _file_source(report, client)
        for finding_key in finding_keys:
            finding = _finding(report, finding_key)
            fixer = fixer_for(finding) if finding is not None else None
            if finding is None or fixer is None:
                results[finding_key] = None
                continue

            plan = await fixer.plan(finding, files)
            if plan is None:
                results[finding_key] = None
                continue

            try:
                validate_plan(finding, plan)
            except PlanValidationError:
                results[finding_key] = None
                continue

            await asyncio.to_thread(save_fix_plan, report.id, plan)
            results[finding_key] = plan
    return results


def build_bundle_zip(scan_id: str) -> bytes | None:
    """Zip every persisted FixPlan for a scan into one archive of unified
    diffs -- the "Download Patch" affordance for someone applying fixes by
    hand instead of going through Stage B's PR flow. `None` if nothing has
    been planned (and saved) yet for this scan.
    """
    plans = list_fix_plans(scan_id)
    if not plans:
        return None

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for plan in plans:
            for patch in plan.patches:
                slug = patch.path.replace("/", "-")
                zf.writestr(f"{plan.finding_key}--{slug}.patch", patch.diff)
    return buffer.getvalue()
