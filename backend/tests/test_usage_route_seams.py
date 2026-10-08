"""Exercise the prepared C7 route patch against an A1-shaped ownership seam.

This is NOT A2's route-walking test and does not satisfy its merge gate.
"""
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from auth.deps import current_user
from models import ScanReport
from scripts.prepare_usage_integration import transform
from storage.scans import save_scan
from tests.test_usage import account, count


OWNERSHIP_SEAM = '''
def load_owned_scan(scan_id, user):
    if scan_owner(scan_id) != user.id:
        raise HTTPException(404, 'Scan not found')
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(404, 'Scan not found')
    return report

def reject_cross_site_scan_start(request):
    if request.headers.get('sec-fetch-site', 'same-origin') not in ('same-origin', 'same-site'):
        raise HTTPException(403, 'Cross-site start refused')
'''


@pytest.fixture
def routes(account, monkeypatch):
    source_path = Path(__file__).resolve().parents[1] / 'main.py'
    source = source_path.read_text(encoding='utf-8') + OWNERSHIP_SEAM
    module = types.ModuleType('usage_route_fixture')
    module.__file__ = str(source_path)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(transform(source), str(source_path), 'exec'), module.__dict__)
    module.app.dependency_overrides[current_user] = lambda: account
    import budgeted_operations as ops
    monkeypatch.setattr(ops, 'enforce_scan_rate_limit', lambda uid: None)
    with TestClient(module.app, raise_server_exceptions=False) as client:
        yield module, client


def test_patch_rejects_unprotected_main():
    source = (Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8')
    with pytest.raises(ValueError, match='prerequisite'):
        transform(source)


def test_rest_and_stream_share_daily_budget_and_reconnect(account, routes, monkeypatch):
    module, client = routes
    monkeypatch.setenv('SENTINELS_DAILY_URL_SCAN', '2')
    def report():
        r = ScanReport(id=str(uuid4()), url='https://example.com', scanned_at='now', duration_ms=1, score=100, grade='A')
        save_scan(r, user_id=account.id)
        return r
    async def rest(*args, **kwargs):
        return report()
    async def stream(*args, **kwargs):
        yield 'done', report()
    monkeypatch.setattr(module, 'run_scan', rest)
    monkeypatch.setattr(module, 'run_scan_stream', stream)
    assert client.post('/scan', json={'url':'https://example.com'}).status_code == 200
    params = {'url':'https://example.com', 'request_id':str(uuid4())}
    first = client.get('/scan/stream', params=params)
    assert first.status_code == 200 and 'event: done' in first.text
    assert client.get('/scan/stream', params=params).text == first.text
    exhausted = client.post('/scan', json={'url':'https://example.com'})
    assert exhausted.status_code == 429 and exhausted.headers['retry-after']
    assert count(account, 'url_scan') == 2
    assert client.get('/usage').json()['budgets']['url_scan']['remaining'] == 0


def test_cross_site_and_unowned_actions_use_no_budget(account, routes):
    module, client = routes
    response = client.get('/repo/stream', params={'url':'repo','request_id':str(uuid4())}, headers={'Sec-Fetch-Site':'cross-site'})
    assert response.status_code == 403
    for path, method, body in [('/scans/stranger/export/pdf','get',None),('/scans/stranger/findings/no/fix','post',None),('/scans/stranger/findings/no/verify','post',None),('/scans/stranger/chat','post',{'question':'Hello'})]:
        response = getattr(client, method)(path, **({'json':body} if body else {}))
        assert response.status_code == 404
    from usage import usage_for
    assert all(value['used'] == 0 for value in usage_for(account.id)['budgets'].values())


def test_pdf_failure_refunds_and_legacy_alias_reloads_owned_report(account, routes, monkeypatch):
    module, client = routes
    report = ScanReport(id='owned', url='https://example.com', scanned_at='now', duration_ms=1, score=50, grade='F')
    save_scan(report, user_id=account.id)
    observed = []
    async def render(stored, fixes):
        observed.append(stored.score)
        return b'%PDF-fixture'
    exporter = types.SimpleNamespace(render=render, media_type='application/pdf', extension='pdf')
    monkeypatch.setattr(module, 'get_exporter', lambda fmt: exporter)
    body = report.model_dump()
    body['score'] = 100
    assert client.post('/scan/pdf', json=body).status_code == 200
    assert observed == [50] and count(account, 'pdf') == 1
    exporter.render = AsyncMock(side_effect=RuntimeError('browser unavailable'))
    assert client.get('/scans/owned/export/pdf').status_code == 500
    assert count(account, 'pdf') == 1
