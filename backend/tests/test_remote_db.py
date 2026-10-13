import asyncio
from concurrent.futures import ThreadPoolExecutor
import ctypes
import os
import sqlite3
import time

from fastapi import HTTPException
import pytest

import db
import remote_db


@pytest.fixture(autouse=True)
def clean_process_pools():
    remote_db.shutdown()
    yield
    remote_db.shutdown()


def process_connection(path):
    conn = remote_db.connect(str(path), '')
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def local_connection_from_token(url, token):
    import libsql
    return libsql.connect(token, timeout=0)


def test_configured_remote_path_never_calls_native_driver_in_parent(tmp_path, monkeypatch):
    import libsql

    def forbidden(*args, **kwargs):
        raise AssertionError('Native connect must run outside the web process')

    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://offline-fixture.invalid')
    monkeypatch.setenv('TURSO_AUTH_TOKEN', str(tmp_path / 'routed.db'))
    monkeypatch.setenv('SENTINELS_DB_DRIVER', 'libsql')
    monkeypatch.setattr(remote_db, '_native_connect', local_connection_from_token)
    monkeypatch.setattr(libsql, 'connect', forbidden)
    conn = db.get_connection()
    try:
        assert conn.raw.worker.process.pid != os.getpid()
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute('SELECT 1').fetchone()[0] == 1
    finally:
        conn.close()


def test_transactions_constraints_cursor_rows_and_connection_reuse(tmp_path):
    path = tmp_path / 'contracts.db'
    first = process_connection(path)
    first.executescript('CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT UNIQUE);')
    first.commit()
    cursor = first.execute('INSERT INTO t(v) VALUES (?)', ('a;b',))
    assert cursor.lastrowid == 1
    assert cursor.rowcount == 1
    first.commit()
    first.execute('BEGIN IMMEDIATE')
    first.execute('INSERT INTO t(v) VALUES (?)', ('discard',))
    assert first.in_transaction
    raw = first.raw
    pid = raw.worker.process.pid
    first.close()  # Rolls back before reuse.
    first.close()
    second = process_connection(path)
    assert second.raw.worker.process.pid == pid
    row = second.execute('SELECT v AS Name FROM t').fetchone()
    assert row['name'] == row[0] == 'a;b'
    assert dict(row) == {'Name': 'a;b'}
    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchone()  # A returned lease cannot read a later user's data.
    with pytest.raises(sqlite3.IntegrityError):
        second.execute('INSERT INTO t(v) VALUES (?)', ('a;b',))
    second.rollback()
    assert second.execute('SELECT COUNT(*) FROM t').fetchone()[0] == 1
    second.close()


def test_quota_concurrency_refund_replay_and_backup(tmp_path, monkeypatch):
    import database_backup
    import storage.scans
    import storage.users
    import usage
    from models import ScanReport

    source = tmp_path / 'source.db'
    for module in (db, database_backup, storage.scans, storage.users, usage):
        monkeypatch.setattr(module, 'get_connection', lambda: process_connection(source))
    db.init_db()
    user = storage.users.sign_in(123, 'fixture', None, 'session-hash', '2099-01-01T00:00:00+00:00')
    storage.scans.save_scan(ScanReport(id='process-report', url='https://example.com',
                                    scanned_at='now', duration_ms=1, score=65, grade='D'), user_id=user.id)
    monkeypatch.setenv('SENTINELS_DAILY_URL_SCAN', '5')

    def reserve(_):
        try:
            return usage.reserve(user.id, 'url_scan')
        except HTTPException as exc:
            assert exc.status_code == 429
            return None

    with ThreadPoolExecutor(max_workers=12) as executor:
        reservations = [r for r in executor.map(reserve, range(12)) if r]
    assert len(reservations) == 5
    reservations[0].refund()
    reservations[0].refund()
    assert usage.usage_for(user.id)['budgets']['url_scan']['used'] == 4
    replay = usage.reserve(user.id, 'chat', request_key='same-job')
    assert usage.reserve(user.id, 'chat', request_key='same-job').id == replay.id
    snapshot = database_backup.export_backup(tmp_path / 'snapshot.db')
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == len(db.MIGRATIONS)
        assert conn.execute('SELECT COUNT(*) FROM scans').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 1
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert len(next(iter(remote_db._pools.values())).workers) <= remote_db.POOL_SIZE


def test_leases_never_share_a_transaction(tmp_path):
    path = tmp_path / 'isolated.db'
    first = process_connection(path)
    first.execute('CREATE TABLE t(v INTEGER)')
    first.commit()
    first.execute('BEGIN IMMEDIATE')
    first.execute('INSERT INTO t VALUES (7)')
    second = process_connection(path)
    assert second.raw.worker.process.pid != first.raw.worker.process.pid
    assert second.execute('SELECT COUNT(*) FROM t').fetchone()[0] == 0
    second.close()
    first.commit()
    first.close()
    third = process_connection(path)
    assert third.execute('SELECT v FROM t').fetchone()[0] == 7
    third.close()


def failing_connect(path, token):
    raise ValueError('remote response with private-token-must-not-leak')


def test_failed_connect_is_sanitized_and_releases_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_db, '_native_connect', failing_connect)
    for _ in range(3):
        with pytest.raises(sqlite3.OperationalError) as error:
            process_connection(tmp_path / 'failure.db')
        assert 'private-token-must-not-leak' not in str(error.value)
    pool = next(iter(remote_db._pools.values()))
    assert pool.creating == 0
    assert not pool.workers


def _gil_sleep(milliseconds):
    # PyDLL intentionally retains the GIL, reproducing the native SDK stall.
    if os.name == 'nt':
        ctypes.PyDLL('kernel32').Sleep(milliseconds)
    else:
        ctypes.PyDLL(None).usleep(milliseconds * 1000)


def slow_connect(path, token):
    import libsql
    _gil_sleep(2200)
    return SlowNative(libsql.connect(path, timeout=0))


class SlowNative:
    def __init__(self, raw):
        self.raw = raw

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def execute(self, *args):
        _gil_sleep(300)
        return self.raw.execute(*args)

    def commit(self):
        _gil_sleep(300)
        return self.raw.commit()


@pytest.mark.asyncio
async def test_native_gil_stall_does_not_stop_parent_event_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_db, '_native_connect', slow_connect)
    gaps = []
    running = True

    async def ticker():
        previous = time.perf_counter()
        while running:
            await asyncio.sleep(.01)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    def query():
        for _ in range(12):
            conn = process_connection(tmp_path / 'slow.db')
            try:
                assert conn.execute('SELECT 1').fetchone()[0] == 1
                conn.commit()
            finally:
                conn.close()

    task = asyncio.create_task(ticker())
    try:
        await asyncio.to_thread(query)
    finally:
        running = False
        await task
    assert len(gaps) > 100
    assert max(gaps) < .25
    print(f'Native connect/query/commit isolation: max parent loop gap {max(gaps) * 1000:.2f} ms')


def test_dead_worker_is_not_replayed_and_new_lease_recovers(tmp_path):
    path = tmp_path / 'failure.db'
    conn = process_connection(path)
    conn.execute('CREATE TABLE t(v INTEGER)')
    conn.commit()
    worker = conn.raw.worker
    worker.process.terminate()
    worker.process.join(timeout=2)
    with pytest.raises(sqlite3.OperationalError, match='not retried'):
        conn.execute('INSERT INTO t VALUES (1)')
    conn.close()
    recovered = process_connection(path)
    assert recovered.execute('SELECT COUNT(*) FROM t').fetchone()[0] == 0
    recovered.close()


def test_bounded_pool_and_shutdown(tmp_path, monkeypatch):
    a = process_connection(tmp_path / 'bounded.db')
    b = process_connection(tmp_path / 'bounded.db')
    processes = [a.raw.worker.process, b.raw.worker.process]
    monkeypatch.setattr(remote_db, 'TIMEOUT', .02)
    with pytest.raises(sqlite3.OperationalError, match='capacity'):
        process_connection(tmp_path / 'bounded.db')
    a.close()
    b.close()
    remote_db.shutdown()
    assert all(not process.is_alive() for process in processes)
