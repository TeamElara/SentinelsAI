"""Per-agent observation ledger, isolated across concurrent asyncio tasks.

Coverage describes execution, not whether a security check passed. The tracker
is also propagated by asyncio.to_thread; shared HTTP-cache failures are recorded
by each caller rather than attributed to whichever task initiated the request.
"""
from contextvars import ContextVar

from models import CheckCoverage

_records: ContextVar[list[CheckCoverage] | None] = ContextVar("check_coverage", default=None)


def begin():
    records: list[CheckCoverage] = []
    return _records.set(records), records


def end(token):
    _records.reset(token)


def record(check: str, status: str, reason: str, *, required: bool = True):
    records = _records.get()
    if records is not None:
        entry = CheckCoverage(check=check, status=status, reason=reason, required=required)
        if entry not in records:
            records.append(entry)


def finish(checks, findings, error, records):
    if error:
        status, reason = "failed", error
    elif any(c.required and c.status != "completed" for c in records):
        status = "partial" if findings else "unavailable"
        reason = "Some observations were unavailable or skipped; see the detailed coverage entries."
    else:
        status, reason = "completed", "Agent completed this check within its documented passive scope."
    explicit = {c.check for c in records}
    return [CheckCoverage(check=c, status=status, reason=reason)
            for c in (checks or ["Agent execution"]) if c not in explicit] + records


def notice(results):
    incomplete = [r.agent for r in results if r.coverage_status != "completed"]
    if not results:
        return "Coverage was not recorded. The score and grade are provisional; deployment readiness is unknown."
    if incomplete:
        return "Incomplete coverage (" + ", ".join(sorted(incomplete)) + "). The score and grade are provisional; deployment readiness is unknown."
    return ""
