"""Second round of review fixes: how the scanner queue, the per-user cap, late
failures and odd settings behave. See `budgeted_operations.py` and `usage.py`."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

import budgeted_operations as ops
import db
import usage
from models import ScanReport
from storage.scans import save_scan
from storage.users import upsert_user
from tests.test_main_audit import SECRET, _signed_in_cookie
from tests.test_review_fixes import SCAN_ID, _link, _linkable  # noqa: F401  (fixtures reused below)
from tests.test_review_fixes import client  # noqa: F401


@pytest.fixture
def account(temp_db, monkeypatch):
    monkeypatch.setattr(ops, '_jobs', {})
    monkeypatch.setattr(ops, 'enforce_scan_rate_limit', lambda uid: None)
    conn = db.get_connection()
    user = upsert_user(conn, 100, 'fixture', None)
    conn.commit()
    conn.close()
    return user


def used(user, kind):
    return usage.usage_for(user.id)['budgets'][kind]['used']


async def drain(response):
    return ''.join([part async for part in response.body_iterator])


# --- a venv symlink must not be tracked again ---------------------------------


def test_gitignore_matches_a_symlinked_venv():
    lines = (Path(__file__).resolve().parents[2] / '.gitignore').read_text().splitlines()
    assert '.venv' in lines and '.venv/' not in lines


# --- waiting for a slot is not running ----------------------------------------


async def test_time_spent_queued_does_not_count_against_the_deadline(account, monkeypatch):
    monkeypatch.setattr(ops, 'DEADLINE_SECONDS', .08)
    slot = asyncio.Semaphore(1)
    await slot.acquire()
    asyncio.get_running_loop().call_later(.15, slot.release)   # longer than the deadline

    async def work():
        await asyncio.sleep(.03)
        return 'ok'

    assert await ops.charged(account.id, 'url_scan', work, slot=slot) == 'ok'
    assert used(account, 'url_scan') == 1


async def test_a_job_that_cannot_get_a_slot_is_turned_away_and_refunded(account, monkeypatch):
    monkeypatch.setattr(ops, 'QUEUE_SECONDS', .02)
    slot = asyncio.Semaphore(1)
    await slot.acquire()
    ran = []

    async def work():
        ran.append(1)

    with pytest.raises(HTTPException) as exc:
        await ops.charged(account.id, 'url_scan', work, slot=slot)

    assert exc.value.status_code == 503 and not ran
    assert used(account, 'url_scan') == 0
    assert slot.locked()                      # the holder's permit was not stolen or leaked


async def test_a_stream_that_cannot_get_a_slot_fails_cleanly_and_is_refunded(account, monkeypatch):
    monkeypatch.setattr(ops, 'QUEUE_SECONDS', .02)
    taken = asyncio.Semaphore(1)
    await taken.acquire()
    monkeypatch.setattr(ops, '_scan_slots', taken)

    async def runner():
        raise AssertionError('must not start')
        yield

    response = await ops.stream_response(account.id, 'url_scan', 'site', str(uuid4()), runner)
    body = await drain(response)

    assert 'event: failed' in body and 'busy' in body
    assert used(account, 'url_scan') == 0


# --- one person cannot take every pending place -------------------------------


async def test_a_user_cannot_hold_more_than_their_share_of_pending_scans(account, monkeypatch):
    monkeypatch.setattr(ops, 'MAX_PENDING_PER_USER', 1)
    release = asyncio.Event()

    async def stuck():
        await release.wait()
        yield 'agent', '{}'

    first = await ops.stream_response(account.id, 'url_scan', 'one', str(uuid4()), stuck)
    with pytest.raises(HTTPException) as exc:
        await ops.stream_response(account.id, 'url_scan', 'two', str(uuid4()), stuck)
    assert exc.value.status_code == 429
    assert used(account, 'url_scan') == 1            # the refused start was refunded

    conn = db.get_connection()
    other = upsert_user(conn, 200, 'other', None)
    conn.commit()
    conn.close()
    third = await ops.stream_response(other.id, 'url_scan', 'three', str(uuid4()), stuck)
    assert third is not None                         # someone else still gets in

    release.set()
    await drain(first)
    await drain(third)
    for job in list(ops._jobs.values()):
        await asyncio.gather(job.task, return_exceptions=True)


# --- how a job's failure is reported ------------------------------------------


async def test_a_failure_after_the_report_was_stored_does_not_undo_the_job(account):
    report = ScanReport(id=str(uuid4()), url='https://example.com', scanned_at='now',
                        duration_ms=1, score=100, grade='A')

    async def runner():
        save_scan(report, user_id=account.id)
        yield 'done', report.model_dump_json()
        raise RuntimeError('cleanup blew up')

    request_id = str(uuid4())
    response = await ops.stream_response(account.id, 'url_scan', report.url, request_id, runner)
    body = await drain(response)
    for job in list(ops._jobs.values()):
        await asyncio.gather(job.task, return_exceptions=True)

    assert 'event: done' in body and 'event: failed' not in body
    row = ops._job_row(next(iter(ops._jobs)))
    assert row['status'] == 'completed' and row['scan_id'] == report.id
    assert used(account, 'url_scan') == 1            # the finished scan stays charged


@pytest.mark.parametrize('kind', ['json', 'pydantic'])
async def test_internal_value_errors_are_not_shown_to_the_user(account, kind):
    if kind == 'json':
        error = json.JSONDecodeError('Expecting value: internal buffer', '', 0)
    else:
        try:
            ScanReport.model_validate({'id': 1})
        except ValueError as exc:                    # pydantic's ValidationError
            error = exc

    async def runner():
        raise error
        yield

    response = await ops.stream_response(account.id, 'url_scan', 'site', str(uuid4()), runner)
    body = await drain(response)

    assert 'event: failed' in body
    assert str(error) not in body and 'Expecting value' not in body and 'validation' not in body.lower()
    assert used(account, 'url_scan') == 0            # our bug, not the user's: refunded


async def test_a_deliberate_value_error_still_reaches_the_user_and_is_charged(account):
    async def runner():
        raise ValueError('Not allowed: that host is private.')
        yield

    response = await ops.stream_response(account.id, 'url_scan', 'site', str(uuid4()), runner)

    assert 'that host is private' in await drain(response)
    assert used(account, 'url_scan') == 1


# --- reconnects are not new permission statements -----------------------------


def test_reconnecting_does_not_log_the_permission_statement_again(temp_db):
    import main
    from storage.remediation import list_audit_for_user

    conn = db.get_connection()
    user = upsert_user(conn, 100, 'fixture', None)
    conn.commit()
    conn.close()
    request_id = str(uuid4())

    assert not usage.has_reservation(user.id, 'url_scan', request_id)
    main.require_permission(True, user, 'https://example.com')
    usage.reserve(user.id, 'url_scan', request_key=request_id, target='https://example.com')
    assert usage.has_reservation(user.id, 'url_scan', request_id)

    main.require_permission(True, user, 'https://example.com', record=False)   # the reconnect
    assert len(list_audit_for_user(user.id, limit=10)) == 1

    with pytest.raises(HTTPException) as exc:                                   # still has to say it
        main.require_permission(False, user, 'https://example.com', record=False)
    assert exc.value.status_code == 400


def test_one_persons_reservation_is_not_anothers(temp_db):
    conn = db.get_connection()
    a = upsert_user(conn, 1, 'a', None)
    b = upsert_user(conn, 2, 'b', None)
    conn.commit()
    conn.close()
    request_id = str(uuid4())
    usage.reserve(a.id, 'url_scan', request_key=request_id, target='x')

    assert usage.has_reservation(a.id, 'url_scan', request_id)
    assert not usage.has_reservation(b.id, 'url_scan', request_id)
    assert not usage.has_reservation(a.id, 'repo_scan', request_id)


# --- fix routes cap their work -------------------------------------------------


@pytest.mark.parametrize('method, path, extra', [
    ('post', '/scans/{id}/fix/plan', {}),
    ('post', '/scans/{id}/fix/apply', {'dry_run': True}),
])
def test_fix_routes_refuse_an_oversized_key_list(client, method, path, extra):
    _linkable(client)
    url = path.format(id=SCAN_ID)

    too_many = getattr(client, method)(url, json={'finding_keys': [f'k{i}' for i in range(101)], **extra})
    too_long = getattr(client, method)(url, json={'finding_keys': ['x' * 201], **extra})

    assert too_many.status_code == 422 and too_long.status_code == 422


def test_fix_plan_accepts_a_normal_list(client):
    _linkable(client)

    res = client.post(f'/scans/{SCAN_ID}/fix/plan', json={'finding_keys': [f'k{i}' for i in range(100)]})

    assert res.status_code != 422                    # reaches the handler (400: URL scan, not a repo)


# --- a mistyped budget setting --------------------------------------------------


def test_a_mistyped_daily_limit_falls_back_to_the_default(account, monkeypatch):
    monkeypatch.setenv('SENTINELS_DAILY_URL_SCAN', 'ten')
    monkeypatch.setenv('SENTINELS_DAILY_GLOBAL_AI', '')

    assert usage.usage_for(account.id)['budgets']['url_scan']['limit'] == usage.DEFAULT_LIMITS['url_scan']
    usage.reserve(account.id, 'url_scan').complete()
    usage.reserve_global_ai()                        # does not raise


def test_a_negative_daily_limit_is_still_refused(account, monkeypatch):
    monkeypatch.setenv('SENTINELS_DAILY_URL_SCAN', '-1')

    with pytest.raises(ValueError):
        usage.reserve(account.id, 'url_scan')
