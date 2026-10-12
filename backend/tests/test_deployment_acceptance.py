"""Acceptance commands must isolate local data and never pass unavailable hosts."""
import json
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if not key.startswith('TURSO_') and key not in ('RENDER', 'SENTINELS_ACCEPTANCE_LOCAL_PATH')}


@pytest.mark.parametrize('driver', ['sqlite', 'libsql'])
def test_local_acceptance_ignores_remote_credentials_and_restores_in_new_process(driver, tmp_path):
    env = clean_environment()
    env.update(TURSO_DATABASE_URL='libsql://must-never-be-contacted.invalid',
               TURSO_AUTH_TOKEN='must-not-appear', TURSO_RESTORE_DATABASE_URL='libsql://also-invalid.invalid',
               RENDER='true')
    output = tmp_path / 'report.json'
    completed = subprocess.run([sys.executable, str(SCRIPTS / 'check_database_acceptance.py'),
                                '--local-driver', driver, '--output', str(output)],
                               env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout
    report = json.loads(output.read_text())
    assert report['status'] == 'passed'
    assert report['checks']['new_process_source_readback'] == 'passed'
    assert report['checks']['backup_restore']['schema_rows_sequences_equal'] is True
    assert report['checks']['quota']['accepted'] == 5
    assert 'must-not-appear' not in completed.stdout + completed.stderr


def test_remote_acceptance_without_credentials_is_pending():
    completed = subprocess.run([sys.executable, str(SCRIPTS / 'check_database_acceptance.py')],
                               env=clean_environment(), capture_output=True, text=True, timeout=10)
    assert completed.returncode == 2
    assert json.loads(completed.stdout)['status'] == 'pending'


def test_disposable_worker_refuses_existing_database_before_migrations(tmp_path):
    database = tmp_path / 'existing.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE sentinel(value TEXT)')
        conn.execute("INSERT INTO sentinel VALUES ('keep')")
    env = clean_environment()
    env.update(SENTINELS_ACCEPTANCE_LOCAL_PATH=str(database), SENTINELS_DB_DRIVER='sqlite')
    completed = subprocess.run([sys.executable, str(SCRIPTS / 'check_database_acceptance.py'), '--worker', 'exercise'],
                               env=env, capture_output=True, text=True, timeout=10)
    assert completed.returncode != 0
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT * FROM sentinel').fetchall() == [('keep',)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('sentinel',)]


@pytest.mark.parametrize('response_kind,expected', [('timeout', 'pending'), ('sso', 'pending'), ('missing_headers', 'failed')])
async def test_frontend_checker_records_unavailable_and_insecure_responses(monkeypatch, response_kind, expected):
    check = runpy.run_path(str(SCRIPTS / 'check_frontend_headers.py'))['check']

    def handler(request):
        if response_kind == 'timeout':
            raise httpx.ReadTimeout('private network detail must not be exported', request=request)
        if response_kind == 'sso':
            return httpx.Response(302, headers={'location': 'https://vercel.com/sso-api'})
        return httpx.Response(200, text='app without headers')

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    result = await check(SimpleNamespace(url='https://fixture.invalid', local_fixtures=False))
    assert result['status'] == expected
    assert result['reason']
    assert 'private network detail' not in json.dumps(result)


@pytest.mark.parametrize('stale_backend', [False, True])
async def test_deployment_contract_requires_auth_quota_and_disabled_pdf(monkeypatch, stale_backend):
    check = runpy.run_path(str(SCRIPTS / 'check_deployment_contract.py'))['check']

    def handler(request):
        if request.url.path.endswith('/health'):
            return httpx.Response(200, json={'status': 'ok'})
        if request.url.path.endswith('/export/formats'):
            formats = ['json', 'markdown'] + (['pdf'] if stale_backend else [])
            return httpx.Response(200, json=[{'format_id': value} for value in formats])
        return httpx.Response(404 if stale_backend else 401)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    result = await check('https://backend.invalid', 'https://frontend.invalid')
    assert result['status'] == ('failed' if stale_backend else 'passed')
    assert len(result['checks']) == 8
    if stale_backend:
        assert all(item['status'] == 'failed' for item in result['checks'] if item['url'].endswith('/usage'))
