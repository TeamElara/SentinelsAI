"""Coverage and feature-switch contracts at the Track B/C boundary."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException

import budgeted_operations as ops
import net.policy
from agents.base import ScanContext
from agents.subdomain import _is_scannable
from agents.tls import TLSAgent
from report.registry import list_formats
from scan_coverage import begin, end
from scan_limits import ScanSlots
from tests.test_usage import account, count


@pytest.mark.parametrize('enabled', [False, True])
def test_formats_follow_server_pdf_switch(monkeypatch, enabled):
    if enabled:
        monkeypatch.setenv('SENTINELS_PDF_ENABLED', 'true')
    else:
        monkeypatch.delenv('SENTINELS_PDF_ENABLED', raising=False)
    formats = {f['format_id'] for f in list_formats()}
    assert ('pdf' in formats) == enabled
    assert formats >= {'json', 'markdown'}


async def test_unresolved_subdomain_is_unavailable_not_internal(monkeypatch):
    async def missing(host):
        return []
    monkeypatch.setattr(net.policy, 'system_resolver', missing)
    token, records = begin()
    try:
        assert not await _is_scannable('gone.example.com')
    finally:
        end(token)
    assert len(records) == 1
    assert records[0].status == 'unavailable'
    assert 'gone.example.com' in records[0].check
    assert 'resolved' in records[0].reason


async def test_tls_unresolved_host_has_no_certificate_finding(monkeypatch, mock_site):
    monkeypatch.setattr(net.policy, 'system_resolver_sync', lambda host: [])
    async with mock_site({}) as client:
        result = await TLSAgent().run(ScanContext(url='https://gone.example.com', client=client))
    assert result.error is None and result.findings == []
    assert result.coverage_status == 'unavailable'
    assert all(c.status == 'unavailable' for c in result.coverage)


async def test_disabled_pdf_never_reserves_quota(account, monkeypatch):
    monkeypatch.delenv('SENTINELS_PDF_ENABLED', raising=False)
    called = False
    async def render():
        nonlocal called
        called = True
    with pytest.raises(HTTPException) as exc:
        await ops.pdf_operation(account.id, render)
    assert exc.value.status_code == 404
    assert not called and count(account, 'pdf') == 0


@pytest.mark.parametrize('capacity', ['user', 'server'])
async def test_real_scan_slots_refund_rest_and_stream(account, monkeypatch, capacity):
    slots = ScanSlots(total=1)
    busy_user = account.id if capacity == 'user' else account.id + 1
    monkeypatch.setattr(ops, 'enforce_scan_rate_limit', lambda uid: None)
    monkeypatch.setattr(ops, '_jobs', {})
    async def rest():
        with slots.hold(account.id):
            pytest.fail('A refused scan must never start')
    async def stream():
        await rest()
        yield 'done', None
    with slots.hold(busy_user):
        with pytest.raises(HTTPException) as exc:
            await ops.scan_operation(account.id, 'url_scan', rest)
        assert exc.value.status_code == 429
        assert exc.value.headers['Retry-After'] == '5'
        assert count(account, 'url_scan') == 0
        response = await ops.stream_response(account.id, 'url_scan', 'site', str(uuid4()), stream)
        events = ''.join([part async for part in response.body_iterator])
        assert 'event: failed' in events
        assert count(account, 'url_scan') == 0
    assert slots.running == 0
