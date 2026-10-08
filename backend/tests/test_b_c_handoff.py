"""Coverage and feature-switch contracts at the Track B/C boundary."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException

import budgeted_operations as ops
import net.policy
import httpcore
from net.client import PolicyBackend
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


async def test_connect_budget_cancels_dns_before_any_socket_is_opened():
    cancelled = asyncio.Event()
    async def resolver(host):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    class Inner(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, *args, **kwargs):
            pytest.fail('DNS exhausted the budget; no socket should open')
    with pytest.raises(httpcore.ConnectTimeout):
        await PolicyBackend(resolver=resolver, inner=Inner()).connect_tcp('example.com', 443, timeout=.02)
    assert cancelled.is_set()


async def test_address_retries_use_only_the_remaining_connect_budget():
    seen = []
    async def resolver(host):
        return ['93.184.216.34', '93.184.216.35']
    class Inner(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, timeout=None, **kwargs):
            seen.append(timeout)
            if len(seen) == 1:
                await asyncio.sleep(.01)
                raise httpcore.ConnectTimeout('first address unavailable')
            return 'connected'
    assert await PolicyBackend(resolver=resolver, inner=Inner()).connect_tcp('example.com', 443, timeout=.2) == 'connected'
    assert 0 < seen[1] < seen[0] <= .2


def test_tls_does_not_restart_timeout_for_every_address(monkeypatch):
    import agents.tls as tls
    seen = []
    clock = iter([0, 1, 11])
    monkeypatch.setattr(tls.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(tls, 'resolve_and_check_sync', lambda host: ['93.184.216.34', '93.184.216.35'])
    def connect(address, timeout=None):
        seen.append((address, timeout))
        raise OSError('first address unavailable')
    monkeypatch.setattr(tls.socket, 'create_connection', connect)
    with pytest.raises(TimeoutError, match='budget exhausted'):
        tls.fetch_certificate('example.com', 443, 10)
    assert seen == [(('93.184.216.34', 443), 9)]
