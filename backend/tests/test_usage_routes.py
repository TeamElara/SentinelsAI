"""Daily budgets on real integrated routes, with ownership and switches intact."""
from uuid import uuid4
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from models import ScanReport
from storage.scans import save_scan
from tests.test_main_audit import SECRET, _signed_in_cookie
from usage import usage_for


@pytest.fixture
def routes(temp_db, monkeypatch):
    monkeypatch.setenv('SENTINELS_SESSION_SECRET', SECRET)
    import main
    import budgeted_operations as ops
    monkeypatch.setattr(ops, '_jobs', {})
    monkeypatch.setattr(ops, 'enforce_scan_rate_limit', lambda uid: None)
    user, cookie = _signed_in_cookie(1, 'alice')
    with TestClient(main.app, raise_server_exceptions=False) as client:
        client.cookies.set('sentinels_session', cookie)
        yield main, client, user


def used(user, kind):
    return usage_for(user.id)['budgets'][kind]['used']


def report(user, target='https://example.com'):
    result = ScanReport(id=str(uuid4()), url=target, scanned_at='now', duration_ms=1, score=100, grade='A')
    save_scan(result, user_id=user.id)
    return result


@pytest.mark.parametrize('path,rest_name,stream_path,stream_name,kind', [
    ('/scan','run_scan','/scan/stream','run_scan_stream','url_scan'),
    ('/repo/scan','run_repo_scan','/repo/stream','run_repo_scan_stream','repo_scan'),
])
def test_rest_stream_and_reconnect_share_one_daily_budget(routes, monkeypatch, path, rest_name, stream_path, stream_name, kind):
    module, client, user = routes
    monkeypatch.setenv('SENTINELS_DAILY_' + kind.upper(), '2')
    async def rest(url, user_id=None):
        return report(user, url)
    async def stream(url, user_id=None):
        yield 'done', report(user, url)
    monkeypatch.setattr(module, rest_name, rest)
    monkeypatch.setattr(module, stream_name, stream)
    assert client.post(path, json={'url':'https://example.com','permission_confirmed':True}).status_code == 200
    params = {'url':'https://example.com','permission_confirmed':'true','request_id':str(uuid4())}
    first = client.get(stream_path, params=params)
    assert first.status_code == 200 and 'event: done' in first.text
    assert client.get(stream_path, params=params).text == first.text
    exhausted = client.post(path, json={'url':'https://example.com','permission_confirmed':True})
    assert exhausted.status_code == 429 and exhausted.headers['retry-after']
    assert used(user, kind) == 2
    assert client.get('/usage').json()['budgets'][kind]['remaining'] == 0


@pytest.mark.parametrize('reason', ['You already have a scan running.', 'Server busy.'])
def test_real_busy_exception_refunds_both_route_types(routes, monkeypatch, reason):
    from scan_limits import ScanBusy
    module, client, user = routes
    async def busy(*args, **kwargs):
        raise ScanBusy(reason)
    async def stream(*args, **kwargs):
        await busy()
        yield
    monkeypatch.setattr(module, 'run_scan', busy)
    monkeypatch.setattr(module, 'run_scan_stream', stream)
    response = client.post('/scan', json={'url':'site','permission_confirmed':True})
    assert response.status_code == 429 and response.headers['retry-after'] == '5'
    response = client.get('/scan/stream', params={'url':'site','permission_confirmed':'true','request_id':str(uuid4())})
    assert 'event: failed' in response.text and reason in response.text
    assert used(user, 'url_scan') == 0


def test_guards_run_before_reserving_and_starting_work(routes, monkeypatch):
    module, client, user = routes
    scanner = AsyncMock()
    monkeypatch.setattr(module, 'run_scan', scanner)
    assert client.post('/scan', json={'url':'site'}).status_code == 400
    monkeypatch.setenv('SENTINELS_SCANS_PAUSED', 'true')
    assert client.post('/scan', json={'url':'site','permission_confirmed':True}).status_code == 503
    monkeypatch.delenv('SENTINELS_SCANS_PAUSED')
    params = {'url':'site','permission_confirmed':'true','request_id':str(uuid4())}
    assert client.get('/scan/stream', params=params, headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
    assert client.get('/scan/stream', params={**params,'request_id':'bad'}).status_code == 422
    scanner.assert_not_called()
    assert used(user, 'url_scan') == 0


def test_ownership_disabled_pdf_and_renderer_failure_preserve_budget(routes, monkeypatch):
    module, client, user = routes
    stored = report(user)
    monkeypatch.delenv('SENTINELS_PDF_ENABLED', raising=False)
    assert client.get('/scans/stranger/export/pdf').status_code == 404
    assert client.get(f'/scans/{stored.id}/export/pdf').status_code == 404
    assert used(user, 'pdf') == 0
    monkeypatch.setenv('SENTINELS_PDF_ENABLED', 'true')
    import report.pdf as pdf
    render = AsyncMock(return_value=b'%PDF-fixture')
    monkeypatch.setattr(pdf, 'generate_pdf', render)
    assert client.get(f'/scans/{stored.id}/export/pdf').status_code == 200
    assert used(user, 'pdf') == 1
    render.side_effect = RuntimeError('browser unavailable')
    assert client.get(f'/scans/{stored.id}/export/pdf').status_code == 500
    assert used(user, 'pdf') == 1
    assert client.post('/scan/pdf', json=stored.model_dump()).status_code in (404,405)


def test_verify_refunds_server_failure_and_enforces_daily_limit(routes, monkeypatch):
    from remediation.verify import VerifyError
    module, client, user = routes
    stored = report(user)
    verify = AsyncMock(side_effect=VerifyError('upstream unavailable', 503))
    monkeypatch.setattr(module, 'verify_finding', verify)
    path = f'/scans/{stored.id}/findings/missing-csp/verify'
    assert client.post(path).status_code == 503
    assert used(user, 'verify') == 0
    monkeypatch.setenv('SENTINELS_DAILY_VERIFY', '0')
    assert client.post(path).status_code == 429
    assert verify.await_count == 1


def test_new_usage_endpoint_requires_session(routes):
    _, client, _ = routes
    client.cookies.clear()
    assert client.get('/usage').status_code == 401


def test_busy_rejections_refund_the_burst_limit_too(routes, monkeypatch):
    import budgeted_operations as ops
    import rate_limit
    from scan_limits import ScanBusy
    module, client, user = routes
    monkeypatch.setattr(rate_limit, '_scan_limiter', rate_limit.SlidingWindowLimiter(1, 300))
    monkeypatch.setattr(ops, 'enforce_scan_rate_limit', rate_limit.enforce_scan_rate_limit)
    busy = AsyncMock(side_effect=ScanBusy('Server busy.'))
    monkeypatch.setattr(module, 'run_scan', busy)
    for _ in range(3):
        response = client.post('/scan', json={'url':'site','permission_confirmed':True})
        assert response.status_code == 429 and response.json()['detail'] == 'Server busy.'
    assert busy.await_count == 3 and used(user, 'url_scan') == 0
    async def stream(*args, **kwargs):
        raise ScanBusy('Server busy.')
        yield
    monkeypatch.setattr(module, 'run_scan_stream', stream)
    for _ in range(3):
        response = client.get('/scan/stream', params={'url':'site','permission_confirmed':'true','request_id':str(uuid4())})
        assert response.status_code == 200 and 'Server busy.' in response.text
    assert used(user, 'url_scan') == 0


def test_ai_fix_route_charges_misses_and_reads_cache_without_key(routes, monkeypatch):
    import ai.fixes as fixes
    from models import AgentResult
    from tests.test_ai_resilience import fixture_finding
    _, client, user = routes
    finding = fixture_finding()
    stored = ScanReport(id=str(uuid4()), url='https://example.com', scanned_at='now', duration_ms=1,
                        score=85, grade='B', findings=[finding], agents=[AgentResult(agent='headers', findings=[finding])])
    save_scan(stored, user_id=user.id)
    raw = json.dumps(dict(why_it_exists='why', security_impact='impact', exploitation='concept', recommended_fix='fix', best_practices=[], framework_examples={}))
    provider = AsyncMock(return_value=raw)
    monkeypatch.setattr(fixes, 'call_groq', provider)
    monkeypatch.setenv('GROQ_API_KEY', 'fixture')
    path = f'/scans/{stored.id}/findings/{finding.id}/fix'
    first = client.post(path)
    assert first.status_code == 200 and used(user, 'ai_fix') == 1
    monkeypatch.delenv('GROQ_API_KEY')
    assert client.post(path).json() == first.json()
    assert used(user, 'ai_fix') == 1 and provider.await_count == 1
    monkeypatch.setenv('GROQ_API_KEY', 'fixture')
    provider.return_value = None
    assert client.post(path, params={'regenerate':'true'}).status_code == 503
    assert used(user, 'ai_fix') == 1
    assert client.post(path).json() == first.json()


def test_chat_route_refunds_outage_and_stops_at_daily_limit(routes, monkeypatch):
    import ai.chat as chat
    _, client, user = routes
    stored = report(user)
    path = f'/scans/{stored.id}/chat'
    monkeypatch.setenv('GROQ_API_KEY', 'fixture')
    provider = AsyncMock(return_value=None)
    monkeypatch.setattr(chat, 'call_groq', provider)
    assert client.post(path, json={'question':'What should I fix?'}).status_code == 503
    assert used(user, 'chat') == 0
    provider.return_value = 'Check the observed findings.'
    monkeypatch.setenv('SENTINELS_DAILY_CHAT', '1')
    assert client.post(path, json={'question':'What should I fix?'}).status_code == 200
    assert used(user, 'chat') == 1
    assert client.post(path, json={'question':'Again?'}).status_code == 429
    assert provider.await_count == 2
