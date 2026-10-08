import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import db
import usage
import budgeted_operations as ops
from models import ScanReport
from storage.scans import save_scan
from storage.users import upsert_user


@pytest.fixture
def account(temp_db):
    conn = db.get_connection()
    user = upsert_user(conn, 100, 'fixture', None)
    conn.commit()
    conn.close()
    return user


def count(user, kind):
    return usage.usage_for(user.id)['budgets'][kind]['used']


def test_atomic_cap_under_twenty_concurrent_connections(account, monkeypatch):
    monkeypatch.setenv('SENTINELS_DAILY_URL_SCAN', '5')
    def attempt(_):
        try:
            usage.reserve(account.id, 'url_scan').complete()
            return True
        except HTTPException as exc:
            assert exc.status_code == 429
            assert int(exc.headers['Retry-After']) > 0
            return False
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))
    assert sum(results) == 5
    assert count(account, 'url_scan') == 5


def test_restart_day_reset_and_idempotent_refund(account, monkeypatch):
    first = usage.reserve(account.id, 'chat', request_key='one')
    duplicate = usage.reserve(account.id, 'chat', request_key='one')
    assert not duplicate.new and duplicate.id == first.id
    first.refund()
    first.refund()
    assert count(account, 'chat') == 0
    now = datetime(2026, 10, 8, 23, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(usage, '_now', lambda: now)
    usage.reserve(account.id, 'chat').complete()
    monkeypatch.setattr(usage, 'WORKER_ID', 'restarted')
    assert count(account, 'chat') == 1
    monkeypatch.setattr(usage, '_now', lambda: now + timedelta(minutes=2))
    assert count(account, 'chat') == 0


def test_abandoned_other_worker_is_refunded_after_deadline(account, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(usage, '_now', lambda: now)
    usage.reserve(account.id, 'url_scan', target='https://example.com')
    monkeypatch.setattr(usage, 'WORKER_ID', 'replacement')
    assert count(account, 'url_scan') == 1
    monkeypatch.setattr(usage, '_now', lambda: now + timedelta(seconds=181))
    assert count(account, 'url_scan') == 0


def test_global_ai_cap_is_atomic_and_survives_restart(account, monkeypatch):
    monkeypatch.setenv('SENTINELS_DAILY_GLOBAL_AI', '1')
    usage.reserve_global_ai()
    monkeypatch.setattr(usage, 'WORKER_ID', 'replacement')
    with pytest.raises(HTTPException, match='') as exc:
        usage.reserve_global_ai()
    assert exc.value.status_code == 429


@pytest.mark.parametrize('kind', ['url_scan','repo_scan','verify','pdf','chat','ai_fix'])
async def test_all_operation_kinds_refund_server_failures(account, kind):
    async def failed():
        raise RuntimeError('server failure')
    with pytest.raises(RuntimeError):
        await ops.charged(account.id, kind, failed)
    assert count(account, kind) == 0
    await ops.charged(account.id, kind, AsyncMock(return_value='ok'))
    assert count(account, kind) == 1


async def test_none_ai_result_and_cancellation_refund(account):
    await ops.charged(account.id, 'chat', AsyncMock(return_value=None), unavailable_none=True)
    async def cancelled():
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await ops.charged(account.id, 'chat', cancelled)
    assert count(account, 'chat') == 0


async def test_stream_reconnect_joins_same_job_and_replays_after_restart(account, monkeypatch):
    monkeypatch.setattr(ops, '_jobs', {})
    monkeypatch.setattr(ops, 'enforce_scan_rate_limit', lambda uid: None)
    request_id = str(uuid4())
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    report = ScanReport(id=str(uuid4()), url='https://example.com', scanned_at='now', duration_ms=1, score=100, grade='A')
    async def runner():
        calls.append(1)
        started.set()
        yield 'agent', json.dumps({'agent':'headers'})
        await release.wait()
        save_scan(report, user_id=account.id)
        yield 'done', report.model_dump_json()
    response = await ops.stream_response(account.id, 'url_scan', report.url, request_id, runner)
    await started.wait()
    stream = response.body_iterator
    assert 'event: agent' in await anext(stream)
    await stream.aclose()  # Browser disconnects while the detached task remains alive.
    second = await ops.stream_response(account.id, 'url_scan', report.url, request_id, runner)
    release.set()
    body = ''.join([part async for part in second.body_iterator])
    assert 'event: agent' in body and 'event: done' in body
    assert len(calls) == 1 and count(account, 'url_scan') == 1
    monkeypatch.setattr(ops, '_jobs', {})
    third = await ops.stream_response(account.id, 'url_scan', report.url, request_id, runner)
    assert 'event: done' in ''.join([part async for part in third.body_iterator])
    assert len(calls) == 1
    with pytest.raises(HTTPException) as exc:
        await ops.stream_response(account.id, 'url_scan', 'https://different.example', request_id, runner)
    assert exc.value.status_code == 409


async def test_stream_failure_refunds_and_quota_429_is_before_headers(account, monkeypatch):
    monkeypatch.setattr(ops, '_jobs', {})
    monkeypatch.setattr(ops, 'enforce_scan_rate_limit', lambda uid: None)
    async def failed():
        raise RuntimeError('private server details')
        yield
    response = await ops.stream_response(account.id, 'repo_scan', 'repo', str(uuid4()), failed)
    body = ''.join([part async for part in response.body_iterator])
    assert 'event: failed' in body and 'private server details' not in body
    assert count(account, 'repo_scan') == 0
    monkeypatch.setenv('SENTINELS_DAILY_REPO_SCAN', '0')
    with pytest.raises(HTTPException) as exc:
        await ops.stream_response(account.id, 'repo_scan', 'repo', str(uuid4()), failed)
    assert exc.value.status_code == 429


async def test_cached_fixes_do_not_charge_and_provider_failure_refunds(account, monkeypatch):
    import ai.fixes as fixes
    from tests.test_ai_resilience import saved_report
    report = saved_report()
    raw = json.dumps(dict(why_it_exists='why', security_impact='impact', exploitation='concept', recommended_fix='fix', best_practices=[], framework_examples={}))
    monkeypatch.setenv('GROQ_API_KEY', 'fixture')
    provider = AsyncMock(return_value=raw)
    monkeypatch.setattr(fixes, 'call_groq', provider)
    results = await asyncio.gather(*[fixes.get_or_generate_fix(report.id, 'missing-csp', report.findings[0], user_id=account.id) for _ in range(5)])
    assert all(r is not None for r in results)
    assert count(account, 'ai_fix') == 1
    provider.assert_awaited_once()
    monkeypatch.delenv('GROQ_API_KEY')
    assert await fixes.get_or_generate_fix(report.id, 'missing-csp', report.findings[0], user_id=account.id) is not None
    assert count(account, 'ai_fix') == 1
    monkeypatch.setenv('GROQ_API_KEY', 'fixture')
    provider.return_value = None
    assert await fixes.get_or_generate_fix(report.id, 'missing-csp', report.findings[0], regenerate=True, user_id=account.id) is None
    assert count(account, 'ai_fix') == 1
    assert await fixes.get_or_generate_fix(report.id, 'missing-csp', report.findings[0], user_id=account.id) == results[0]


async def test_global_exhaustion_preserves_summary_and_refunds_interactive(account, monkeypatch):
    import ai.client as client
    monkeypatch.setenv('GROQ_API_KEY', 'fixture')
    monkeypatch.setenv('SENTINELS_DAILY_GLOBAL_AI', '0')
    assert await client.call_groq([]) is None
    with pytest.raises(HTTPException) as exc:
        await ops.charged(account.id, 'chat', lambda: client.call_groq([], interactive=True), unavailable_none=True)
    assert exc.value.status_code == 429
    assert count(account, 'chat') == 0
