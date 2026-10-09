"""C7 disconnects close the subscriber; the bounded job remains reconnectable.

The job owns the orchestrator and awaits its cleanup on completion or deadline.
Direct orchestrator cancellation is still covered in test_scan_limits.py.
"""
import asyncio
from uuid import uuid4

import anyio
import pytest

from models import AgentResult, ScanReport
from storage.scans import save_scan
from tests.test_main_audit import SECRET, _signed_in_cookie
from usage import usage_for

ROUTES = [
    ('/scan/stream', 'run_scan_stream', 'https://example.com', 'url_scan'),
    ('/repo/stream', 'run_repo_scan_stream', 'https://github.com/octo/demo', 'repo_scan'),
]


@pytest.fixture
def app_module(temp_db, monkeypatch):
    monkeypatch.setenv('SENTINELS_SESSION_SECRET', SECRET)
    import main
    import budgeted_operations as ops
    monkeypatch.setattr(ops, '_jobs', {})
    monkeypatch.setattr(ops, 'enforce_scan_rate_limit', lambda uid: None)
    return main


class Request:
    headers = {}


async def disconnect(response):
    sent = []
    first_event = anyio.Event()
    async def send(message):
        sent.append(message)
        if message['type'] == 'http.response.body' and message.get('more_body'):
            first_event.set()
            await anyio.sleep_forever()
    async def receive():
        await first_event.wait()
        return {'type':'http.disconnect'}
    await response({'type':'http','asgi':{'spec_version':'2.3'}}, receive, send)
    assert any(m['type'] == 'http.response.body' for m in sent)


async def start(module, path, target, user, request_id):
    if path == '/scan/stream':
        return await module.scan_stream(target, Request(), request_id, permission_confirmed=True, user=user)
    return await module.repo_scan_stream(target, Request(), request_id, user=user)


@pytest.mark.parametrize('path,attr,target,kind', ROUTES)
async def test_disconnect_reconnect_keeps_one_job_and_awaits_cleanup(app_module, monkeypatch, path, attr, target, kind):
    user, _ = _signed_in_cookie(1, 'alice')
    released, cleaned = asyncio.Event(), asyncio.Event()
    starts = []
    async def stream(url, user_id=None):
        starts.append(url)
        try:
            yield 'agent', AgentResult(agent='headers', duration_ms=1)
            await released.wait()
            report = ScanReport(id=str(uuid4()), url=url, scanned_at='now', duration_ms=1, score=100, grade='A')
            save_scan(report, user_id=user_id)
            yield 'done', report
        finally:
            await asyncio.sleep(.01)
            cleaned.set()
    monkeypatch.setattr(app_module, attr, stream)
    request_id = str(uuid4())
    response = await start(app_module, path, target, user, request_id)
    try:
        await disconnect(response)
        assert not cleaned.is_set(), 'Disconnect must retain the bounded, quota-reserved job'
        assert usage_for(user.id)['budgets'][kind]['used'] == 1
        reconnected = await start(app_module, path, target, user, request_id)
        released.set()
        chunks = ''.join([chunk async for chunk in reconnected.body_iterator])
        assert 'event: agent' in chunks and 'event: done' in chunks
        await asyncio.wait_for(cleaned.wait(), 1)
        assert starts == [target]
        assert usage_for(user.id)['budgets'][kind]['used'] == 1
    finally:
        released.set()
        import budgeted_operations as ops
        await asyncio.gather(*(job.task for job in ops._jobs.values() if job.task), return_exceptions=True)


@pytest.mark.parametrize('path,attr,target,kind', ROUTES)
async def test_abandoned_job_deadline_closes_runner_and_refunds(app_module, monkeypatch, path, attr, target, kind):
    import budgeted_operations as ops
    monkeypatch.setattr(ops, 'DEADLINE_SECONDS', .05)
    user, _ = _signed_in_cookie(1, 'alice')
    cleaned = asyncio.Event()
    async def stream(url, user_id=None):
        try:
            yield 'agent', AgentResult(agent='headers', duration_ms=1)
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(.01)
            cleaned.set()
    monkeypatch.setattr(app_module, attr, stream)
    request_id = str(uuid4())
    await disconnect(await start(app_module, path, target, user, request_id))
    await asyncio.wait_for(asyncio.gather(*(job.task for job in ops._jobs.values() if job.task)), 1)
    assert cleaned.is_set()
    response = await start(app_module, path, target, user, request_id)
    assert 'event: failed' in ''.join([chunk async for chunk in response.body_iterator])
    assert usage_for(user.id)['budgets'][kind]['used'] == 0


@pytest.mark.parametrize('path,attr,target,kind', ROUTES)
async def test_bad_target_keeps_its_failure_message(app_module, monkeypatch, path, attr, target, kind):
    user, _ = _signed_in_cookie(1, 'alice')
    async def refuses(*args, **kwargs):
        raise ValueError('That URL could not be parsed.')
        yield
    monkeypatch.setattr(app_module, attr, refuses)
    response = await start(app_module, path, target, user, str(uuid4()))
    chunks = ''.join([chunk async for chunk in response.body_iterator])
    assert 'event: failed' in chunks and 'That URL could not be parsed.' in chunks
    assert usage_for(user.id)['budgets'][kind]['used'] == 1
