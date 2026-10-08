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

# These exercise the *prepared* C7 route patch against a main.py shaped like the
# repo before Track A landed: they append a stand-in for A1's helpers to the
# current main.py. With A1-A8 and B1-B4 merged, main.py already has the real
# helpers (and A5's pause switches, A6's permission body, B's 429), so the seam
# no longer describes anything. Activating C7 for real means applying the patch
# to this main.py by hand and testing the routes themselves; until that happens
# these stay skipped rather than silently passing against the wrong shape.
pytestmark = pytest.mark.skip(reason="C7 route activation is pending; see the note above")


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


def guarded_source():
    source = (Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8')
    source = source.replace('async def scan_stream(url: str, user:', 'async def scan_stream(url: str, request: Request, user:')
    source = source.replace('async def repo_scan_stream(url: str, user:', 'async def repo_scan_stream(url: str, request: Request, user:')
    return source + OWNERSHIP_SEAM


@pytest.fixture
def routes(account, monkeypatch):
    source_path = Path(__file__).resolve().parents[1] / 'main.py'
    source = guarded_source()
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


def test_generator_preserves_permission_statement_and_route_dependencies():
    import ast
    source = guarded_source()
    source = source.replace('async def scan(request: ScanRequest,', 'async def scan(request: UrlScanRequest,')
    source = source.replace('    enforce_scan_rate_limit(user.id)', '    require_permission(request.permission_confirmed, user, request.url)\n    enforce_scan_rate_limit(user.id)', 1)
    source = source.replace('url: str, request: Request, user:', 'url: str, request: Request, permission_confirmed: bool = False, user:')
    source = source.replace('    enforce_scan_rate_limit(user.id)\n\n    async def events():', '    require_permission(permission_confirmed, user, url)\n    enforce_scan_rate_limit(user.id)\n\n    async def events():')
    source = source.replace('@app.post("/scan", response_model=ScanReport)', '@app.post("/scan", response_model=ScanReport, dependencies=[Depends(require_scans_running)])')
    updated = transform(source)
    functions = {node.name:node for node in ast.parse(updated).body if isinstance(node, ast.AsyncFunctionDef)}
    assert functions['scan'].args.args[0].annotation.id == 'UrlScanRequest'
    assert functions['scan'].body[0].value.func.id == 'require_permission'
    assert 'Depends(require_scans_running)' in ast.unparse(functions['scan'].decorator_list[0])
    stream = functions['scan_stream']
    assert [n.arg for n in stream.args.args] == ['url','request','request_id','permission_confirmed','user']
    assert stream.body[0].value.func.id == 'reject_cross_site_scan_start'
    assert stream.body[1].value.func.id == 'require_permission'


def test_generator_does_not_restore_removed_pdf_alias():
    import ast
    source = guarded_source()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'scan_pdf')
    lines = source.splitlines(keepends=True)
    del lines[node.decorator_list[0].lineno-1:node.end_lineno]
    assert 'async def scan_pdf(' not in transform(''.join(lines))


def test_permission_ui_patch_requires_real_checkbox():
    from scripts.prepare_usage_integration import transform_permission_ui
    with pytest.raises(ValueError, match='never invent'):
        transform_permission_ui('stream(url, {\n    });\n  }\n\n  return (')
    source = 'const [permission, setPermission] = useState(false);\nconst needsPermission = true;\nstream(url, {\n    });\n  }\n\n  return ('
    result = transform_permission_ui(source)
    assert '}, permission);' in result and 'permission_confirmed=true' not in result


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
    monkeypatch.setenv('SENTINELS_PDF_ENABLED', 'true')
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
