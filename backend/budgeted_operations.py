"""Quota-aware operations; HTTP routes must check ownership before calling.

URL/repo REST and streaming calls share these daily counters. Disconnecting a
stream does not cancel its work. The beta deployment requires one worker.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import ValidationError

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse

from auth.deps import current_user
from db import get_connection
from models import User
from rate_limit import enforce_scan_rate_limit, refund_scan_rate_limit
from storage.scans import get_scan, scan_owner
from usage import reserve, usage_for


def _is_busy(exc):
    # B4 supplies this type. Keep C7 independently testable before its merge.
    try:
        from scan_limits import ScanBusy
    except ImportError:
        return False
    return isinstance(exc, ScanBusy)

_scan_slots = asyncio.Semaphore(2)
_pdf_slots = asyncio.Semaphore(1)
_jobs: dict[str, Job] = {}
MAX_PENDING = 8
# One person may not fill the whole scanner: with a per-user cap the rest of
# the invite list still gets a turn.
MAX_PENDING_PER_USER = 2
DEADLINE_SECONDS = 150
# How long a job may wait for a free slot before it is turned away. The
# deadline above starts only once a slot is held, so waiting in line never eats
# into a job's own time. Queue wait plus deadline stays under
# `usage.STALE_SECONDS`, which is what lets another worker tell a dead job from
# a waiting one.
QUEUE_SECONDS = 20


def get_usage(user: User = Depends(current_user)):
    return usage_for(user.id)


def _busy():
    return HTTPException(503, 'Sentinels is busy right now. Try again in a minute; nothing was charged.',
                         headers={'Retry-After': '5'})


async def _acquire(slot):
    """Wait a bounded time for a slot, then give up with a 503 (refunded by callers)."""
    try:
        await asyncio.wait_for(slot.acquire(), QUEUE_SECONDS)
    except TimeoutError:
        raise _busy() from None


async def charged(user_id, kind, operation, *, unavailable_none=False, slot=None):
    """Charge once on start; refund server failure, cancellation or no AI result.

    With `slot`, the operation runs while holding that semaphore. Waiting for it
    is bounded by QUEUE_SECONDS and is outside the operation's own deadline.
    """
    reservation = await asyncio.to_thread(reserve, user_id, kind)
    try:
        if slot is not None:
            await _acquire(slot)
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                result = await operation()
        finally:
            if slot is not None:
                slot.release()
        if unavailable_none and result is None:
            await asyncio.to_thread(reservation.refund)
        else:
            await asyncio.to_thread(reservation.complete)
        return result
    except HTTPException as exc:
        if exc.status_code >= 500 or exc.status_code == 429:
            await asyncio.to_thread(reservation.refund)
        else:
            await asyncio.to_thread(reservation.complete)
        raise
    except ValueError as exc:
        if _is_busy(exc):
            await asyncio.to_thread(reservation.refund)
            raise HTTPException(429, str(exc), headers={'Retry-After': '5'}) from exc
        # A rejected target is a user failure after starting, not a server outage.
        await asyncio.to_thread(reservation.complete)
        raise
    except BaseException:
        await asyncio.shield(asyncio.to_thread(reservation.refund))
        raise


async def scan_operation(user_id, kind, operation):
    ticket = enforce_scan_rate_limit(user_id)
    try:
        return await charged(user_id, kind, operation, slot=_scan_slots)
    except BaseException as exc:
        if not isinstance(exc, ValueError) and not (
            isinstance(exc, HTTPException) and exc.status_code < 500 and exc.status_code != 429
        ):
            refund_scan_rate_limit(user_id, ticket)
        raise


async def pdf_operation(user_id, operation):
    from report.pdf import PdfDisabled, pdf_enabled
    if not pdf_enabled():
        raise PdfDisabled()
    return await charged(user_id, 'pdf', operation, slot=_pdf_slots)


@dataclass
class Job:
    events: list[tuple[str, str]] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    done: bool = False
    finished_at: float = 0
    task: asyncio.Task | None = None
    user_id: int | None = None

    async def emit(self, event, data):
        async with self.condition:
            self.events.append((event, data))
            if event in ('done', 'failed'):
                self.done = True
                self.finished_at = time.monotonic()
            self.condition.notify_all()


def _job_row(reservation_id):
    conn = get_connection()
    try:
        row = conn.execute('SELECT * FROM scan_jobs WHERE id=?', (reservation_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _finish(reservation_id, *, scan_id=None, error=None):
    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE scan_jobs SET status=?,scan_id=?,error=? WHERE id=?",
                     ('completed' if scan_id else 'failed', scan_id, error, reservation_id))
        if scan_id:
            conn.execute("UPDATE usage_reservations SET state='completed' WHERE id=? AND state='active'", (reservation_id,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _is_user_error(exc):
    """A ValueError the scan raised on purpose, whose message is meant for the user.

    JSON and pydantic errors are ValueErrors too, but their text describes our
    own internals, not the user's request.
    """
    return isinstance(exc, ValueError) and not isinstance(exc, (json.JSONDecodeError, ValidationError))


async def _run(job, reservation, runner, user_id=None, burst_ticket=None):
    try:
        await _acquire(_scan_slots)
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                async with aclosing(runner()) as events:
                    async for event, data in events:
                        if event == 'done':
                            scan_id = json.loads(data)['id']
                            await asyncio.to_thread(_finish, reservation.id, scan_id=scan_id)
                        await job.emit(event, data)
        finally:
            _scan_slots.release()
        if not job.done:
            raise RuntimeError('Scan ended without a stored report')
    except BaseException as exc:
        if job.done:
            # The report was already stored and announced; a late failure
            # (cleanup, a deadline landing as the stream closed) must not turn
            # a finished job into a failed one.
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        if isinstance(exc, HTTPException) and exc.status_code == 503:
            message = exc.detail
        elif _is_user_error(exc):
            message = str(exc)
        else:
            message = 'Scanner could not complete this job. Its daily allowance was refunded.'
        if _is_user_error(exc) and not _is_busy(exc):
            await asyncio.to_thread(reservation.complete)
        else:
            await asyncio.shield(asyncio.to_thread(reservation.refund))
            refund_scan_rate_limit(user_id, burst_ticket)
        await asyncio.to_thread(_finish, reservation.id, error=message)
        await job.emit('failed', json.dumps({'detail': message}))
        if isinstance(exc, asyncio.CancelledError):
            raise


async def _events(job):
    cursor = 0
    while True:
        async with job.condition:
            if cursor == len(job.events) and not job.done:
                try:
                    await asyncio.wait_for(job.condition.wait(), timeout=10)
                except TimeoutError:
                    pass
            batch = job.events[cursor:]
            cursor += len(batch)
            done = job.done
        for event, data in batch:
            yield f'event: {event}\ndata: {data}\n\n'
        if done:
            return
        if not batch:
            yield ': keepalive\n\n'


async def stream_response(user_id, kind, target, request_id, runner):
    """Authenticated same-site routes call this after their security checks."""
    try:
        if str(UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, 'A canonical UUID request_id is required.')
    # Prune completed replay buffers. Stored reports remain replayable in the DB.
    for key, job in list(_jobs.items()):
        if job.done and time.monotonic() - job.finished_at > 60:
            _jobs.pop(key, None)
    reservation = await asyncio.to_thread(reserve, user_id, kind, request_key=request_id, target=target)
    job = _jobs.get(reservation.id)
    if job is None and reservation.new:
        if sum(not j.done for j in _jobs.values()) >= MAX_PENDING:
            await asyncio.to_thread(reservation.refund)
            raise HTTPException(503, 'Scanner is busy. Try again shortly.', headers={'Retry-After': '5'})
        if sum(not j.done and j.user_id == user_id for j in _jobs.values()) >= MAX_PENDING_PER_USER:
            await asyncio.to_thread(reservation.refund)
            raise HTTPException(429, 'You already have scans waiting or running. Let them finish first.', headers={'Retry-After': '5'})
        try:
            burst_ticket = enforce_scan_rate_limit(user_id)
        except HTTPException:
            await asyncio.to_thread(reservation.refund)
            raise
        job = Job(user_id=user_id)
        _jobs[reservation.id] = job
        job.task = asyncio.create_task(_run(job, reservation, runner, user_id, burst_ticket))
    elif job is None:
        row = await asyncio.to_thread(_job_row, reservation.id)
        job = Job()
        if row and row['status'] == 'completed' and row['scan_id']:
            # Recheck ownership: deletion/reassignment must never replay another account's report.
            def owned_report():
                return get_scan(row['scan_id']) if scan_owner(row['scan_id']) == user_id else None
            report = await asyncio.to_thread(owned_report)
            if report is None:
                raise HTTPException(404, 'The stored report is no longer available.')
            await job.emit('done', report.model_dump_json())
        elif row and row['status'] == 'failed':
            await job.emit('failed', json.dumps({'detail': row['error'] or 'Job failed. Start a new scan.'}))
        else:
            raise HTTPException(409, 'Scanner restarted or this job is on another worker. Retry this request shortly.', headers={'Retry-After': '5'})
    return StreamingResponse(_events(job), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
