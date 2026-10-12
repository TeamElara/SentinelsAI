"""C6/C7 acceptance: isolated local DBs or an explicitly disposable Turso DB.

Never loads .env, exports credentials, or writes fixtures to an existing DB.
Run --help for options. JSON reports contain counts and timings, not row data.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def inventory(conn):
    """Compare schema, every row and AUTOINCREMENT state without logging data."""
    schema = [tuple(row) for row in conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
    ).fetchall()]
    tables = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        quoted = '"' + name.replace('"', '""') + '"'
        rows = [json.dumps(tuple(row), default=lambda value: {'bytes': bytes(value).hex()})
                for row in conn.execute(f'SELECT * FROM {quoted}').fetchall()]
        tables[name] = {'count': len(rows), 'sha256': hashlib.sha256(
            '\n'.join(sorted(rows)).encode()).hexdigest()}
    return {'schema': schema, 'tables': tables}


def inspect():
    import db
    conn = db.get_connection()
    try:
        expected = sorted(version for version, _ in db.MIGRATIONS)
        actual = [row[0] for row in conn.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()]
        require(actual == expected, 'Migration ledger does not match this checkout.')
        require(conn.execute('SELECT version FROM schema_version').fetchone()[0] == max(expected),
                'Schema version does not match the migration ledger.')
        require(conn.execute('PRAGMA foreign_key_check').fetchall() == [], 'Foreign-key violations detected.')
        require(conn.execute('PRAGMA integrity_check').fetchall()[0][0] == 'ok', 'Integrity check failed.')
        index = conn.execute("SELECT sql FROM sqlite_master WHERE name='idx_fix_applications_active'").fetchone()
        require(index and 'WHERE' in index[0].upper(), 'Active application partial index is missing.')
        return {'migration_version': max(expected), 'foreign_keys': 'passed', 'integrity': 'passed',
                'partial_index': 'passed'}
    finally:
        conn.close()


async def responsiveness():
    """Measure event-loop stalls while real driver connections run in a thread."""
    import db
    gaps, samples = [], []
    stopped = asyncio.Event()

    async def ticker():
        previous = time.perf_counter()
        while not stopped.is_set():
            await asyncio.sleep(.01)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    def queries():
        for _ in range(12):
            started = time.perf_counter()
            conn = db.get_connection()
            try:
                require(conn.execute('SELECT 1').fetchone()[0] == 1, 'Database read failed.')
            finally:
                conn.close()
            samples.append((time.perf_counter() - started) * 1000)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    try:
        await asyncio.to_thread(queries)
    finally:
        stopped.set()
        await task
    result = {'samples': len(samples), 'max_connection_query_ms': round(max(samples), 2),
              'max_event_loop_gap_ms': round(max(gaps) * 1000, 2), 'event_loop_limit_ms': 250}
    require(max(gaps) <= .25, 'Database driver stalled the event loop beyond 250 ms.')
    return result


def exercise(directory):
    import db
    from database_backup import export_backup
    from fastapi import HTTPException
    from models import ScanReport
    from storage.scans import save_scan, get_scan
    from storage.users import sign_in, user_for_token_hash
    import usage

    conn = db.get_connection()
    try:
        require(not conn.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall(),
                'Disposable acceptance requires an empty database; nothing was changed.')
    finally:
        conn.close()
    db.init_db()
    db.init_db()
    result = inspect()
    user = sign_in(987654321, 'acceptance-fixture', None, 'synthetic-session-hash', '2099-01-01T00:00:00+00:00')
    save_scan(ScanReport(id='acceptance-fixture', url='https://example.com', scanned_at='now',
                        duration_ms=1, score=65, grade='D'), user_id=user.id)
    require(get_scan('acceptance-fixture').score == 65, 'Report readback failed.')
    require(user_for_token_hash('synthetic-session-hash').id == user.id, 'Session readback failed.')
    conn = db.get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE scans SET score=1 WHERE id='acceptance-fixture'")
        conn.rollback()
        try:
            conn.execute("INSERT INTO sessions VALUES ('invalid-parent',-1,'now','2099')")
        except sqlite3.IntegrityError:
            conn.rollback()
        else:
            conn.rollback()
            raise RuntimeError('Foreign-key enforcement failed.')
    finally:
        conn.close()
    require(get_scan('acceptance-fixture').score == 65, 'Transaction rollback failed.')
    os.environ['SENTINELS_DAILY_URL_SCAN'] = '5'

    def attempt(_):
        try:
            return usage.reserve(user.id, 'url_scan')
        except HTTPException as exc:
            require(exc.status_code == 429 and int(exc.headers['Retry-After']) > 0,
                    'Unexpected quota rejection.')
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        reservations = [item for item in pool.map(attempt, range(12)) if item is not None]
    require(len(reservations) == 5, 'Concurrent reservations exceeded or undershot the limit.')
    first = reservations[0]
    first.refund()
    first.refund()
    require(usage.usage_for(user.id)['budgets']['url_scan']['used'] == 4, 'Refund was not idempotent.')
    replay = usage.reserve(user.id, 'chat', request_key='acceptance-replay')
    require(usage.reserve(user.id, 'chat', request_key='acceptance-replay').id == replay.id,
            'Request replay created a second reservation.')
    replay.complete()
    for reservation in reservations[1:]:
        reservation.complete()
    result['quota'] = {'attempts': 12, 'accepted': 5, 'used_after_double_refund': 4, 'replay': 'passed'}
    result['responsiveness'] = asyncio.run(responsiveness())
    snapshot = export_backup(directory / 'snapshot.db')
    conn = db.get_connection()
    try:
        expected = inventory(conn)
    finally:
        conn.close()
    manifest = directory / 'manifest.json'
    manifest.write_text(json.dumps(expected), encoding='utf-8')
    # A new interpreter checks durable state, rather than reusing module globals.
    run_child('verify', manifest=manifest)
    result['new_process_source_readback'] = 'passed'
    restore_env = os.environ.copy()
    remote_restore = bool(restore_env.get('TURSO_RESTORE_DATABASE_URL'))
    if remote_restore:
        require(bool(restore_env.get('TURSO_RESTORE_AUTH_TOKEN')), 'Restore target token is missing.')
        restore_env['TURSO_DATABASE_URL'] = restore_env['TURSO_RESTORE_DATABASE_URL']
        restore_env['TURSO_AUTH_TOKEN'] = restore_env['TURSO_RESTORE_AUTH_TOKEN']
        restore_env.pop('SENTINELS_ACCEPTANCE_LOCAL_PATH', None)
        restore_env['SENTINELS_DB_DRIVER'] = 'libsql'
    else:
        for name in ('TURSO_DATABASE_URL', 'TURSO_AUTH_TOKEN', 'RENDER'):
            restore_env.pop(name, None)
        restore_env['SENTINELS_ACCEPTANCE_LOCAL_PATH'] = str(directory / 'restored.db')
    run_child('restore', manifest=manifest, snapshot=snapshot, env=restore_env)
    run_child('verify', manifest=manifest, env=restore_env)
    result['backup_restore'] = {'target': 'remote' if remote_restore else 'temporary-local',
                                'schema_rows_sequences_equal': True, 'new_process_readback': 'passed'}
    if os.environ.get('TURSO_DATABASE_URL') and not remote_restore:
        result['remaining'] = ['Restore into a second empty Turso database.', 'Real Render restart/redeploy persistence.']
    return result


def run_child(phase, *, manifest=None, snapshot=None, env=None):
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', phase]
    for flag, value in (('--manifest', manifest), ('--snapshot', snapshot)):
        if value:
            command.extend([flag, str(value)])
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=180)
    require(completed.returncode == 0, 'Acceptance subprocess failed: ' + phase + ' (details suppressed to protect credentials).')
    return json.loads(completed.stdout)


def worker(args):
    import db
    path = os.environ.get('SENTINELS_ACCEPTANCE_LOCAL_PATH')
    if path:
        db.DB_PATH = Path(path)
    if args.worker in ('verify', 'restore'):
        if args.worker == 'restore':
            from database_backup import restore_backup
            restore_backup(args.snapshot)  # Independently refuses a nonempty target.
        expected = json.loads(args.manifest.read_text(encoding='utf-8'))
        conn = db.get_connection()
        try:
            actual = json.loads(json.dumps(inventory(conn)))
            require(actual == expected, 'Schema, rows or sequence state changed across restore/restart.')
        finally:
            conn.close()
        return inspect()
    if args.worker == 'inspect':
        return {**inspect(), 'responsiveness': asyncio.run(responsiveness())}
    with tempfile.TemporaryDirectory(prefix='sentinels-acceptance-') as directory:
        return exercise(Path(directory))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--local-driver', choices=('sqlite', 'libsql'), help='Use a temporary database; ignore all remote settings.')
    mode.add_argument('--disposable', action='store_true', help='Write fixtures ONLY to an empty configured Turso database.')
    parser.add_argument('--output', type=Path, help='Write a safe JSON evidence report.')
    parser.add_argument('--worker', choices=('inspect', 'exercise', 'verify', 'restore'), help=argparse.SUPPRESS)
    parser.add_argument('--manifest', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--snapshot', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args)))
        return 0
    result = {'status': 'pending', 'mode': 'local' if args.local_driver else 'remote',
              'writes': bool(args.local_driver or args.disposable)}
    with tempfile.TemporaryDirectory(prefix='sentinels-db-check-') as directory:
        child_env = os.environ.copy()
        if args.local_driver:
            for name in list(child_env):
                if name.startswith('TURSO_') or name in ('RENDER', 'SENTINELS_ACCEPTANCE_LOCAL_PATH'):
                    child_env.pop(name)
            child_env['SENTINELS_DB_DRIVER'] = args.local_driver
            child_env['SENTINELS_ACCEPTANCE_LOCAL_PATH'] = str(Path(directory) / 'source.db')
            result['driver'] = args.local_driver
        elif not (child_env.get('TURSO_DATABASE_URL') and child_env.get('TURSO_AUTH_TOKEN')):
            result['reason'] = 'Remote acceptance needs TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in this process environment.'
        else:
            child_env.pop('SENTINELS_ACCEPTANCE_LOCAL_PATH', None)
            child_env['SENTINELS_DB_DRIVER'] = 'libsql'
        if args.local_driver or 'reason' not in result:
            try:
                result['checks'] = run_child('exercise' if args.local_driver or args.disposable else 'inspect', env=child_env)
                result['status'] = 'passed'
            except (RuntimeError, subprocess.TimeoutExpired):
                result.update(status='failed', reason='Database acceptance failed or timed out. Check configuration, schema, empty-target requirement and connectivity; no raw database errors are exported.')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return {'passed': 0, 'failed': 1, 'pending': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
