"""Bounded, persistent remote libSQL connections outside the web process GIL.

Each spawned worker owns one native connection. A lease owns that worker until
close, including its transaction and cursors. Only private OS pipes cross the
process boundary; credentials never appear in command lines. Ambiguous errors
discard the connection and are never retried, especially after writes.
"""
from __future__ import annotations

import atexit
import multiprocessing
import os
import sqlite3
import threading
import time

from libsql_adapter import Connection

POOL_SIZE = 2
TIMEOUT = 30
_pools = {}
_pools_lock = threading.Lock()


def _native_connect(url, token):
    import libsql
    return libsql.connect(url, auth_token=token, timeout=0)


def _serve(pipe, url, token, connector):
    from libsql_adapter import _call

    conn = None
    cursors = {}
    next_cursor = 0
    try:
        conn = _call(lambda: connector(url, token))
        pipe.send(('ok', None))
        while True:
            operation, args = pipe.recv()
            try:
                if operation in ('execute', 'executemany'):
                    cursor = _call(getattr(conn, operation), *args,
                                   retry_locked=operation != 'executemany')
                    next_cursor += 1
                    cursors[next_cursor] = cursor
                    value = (next_cursor, cursor.description, cursor.lastrowid, cursor.rowcount)
                elif operation in ('fetchone', 'fetchmany', 'fetchall'):
                    value = _call(getattr(cursors[args[0]], operation), *args[1:])
                elif operation == 'cursor_close':
                    cursor = cursors.pop(args[0], None)
                    if cursor is not None:
                        cursor.close()
                    value = None
                elif operation == 'in_transaction':
                    value = conn.in_transaction
                elif operation == 'reset':
                    _call(conn.rollback)
                    for cursor in cursors.values():
                        cursor.close()
                    cursors.clear()
                    value = None
                elif operation in ('commit', 'rollback'):
                    value = _call(getattr(conn, operation))
                else:
                    raise sqlite3.ProgrammingError('Unknown database operation.')
                pipe.send(('ok', value))
            except sqlite3.IntegrityError:
                pipe.send(('integrity', 'Database constraint failed.'))
            except Exception:
                # Never send native errors, SQL, parameters or credentials.
                pipe.send(('error', 'Remote database operation failed.'))
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception:
        try:
            pipe.send(('error', 'Remote database connection failed.'))
        except (BrokenPipeError, OSError):
            pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        pipe.close()


class _Worker:
    def __init__(self, url, token):
        context = multiprocessing.get_context('spawn')
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=_serve, args=(child, url, token, _native_connect), daemon=True)
        try:
            self.process.start()
        except BaseException:
            self.pipe.close()
            child.close()
            raise
        child.close()
        try:
            self._receive()
        except BaseException:
            self.close()
            raise

    def _receive(self):
        if not self.pipe.poll(TIMEOUT):
            raise sqlite3.OperationalError('Remote database operation timed out; outcome may be unknown.')
        status, value = self.pipe.recv()
        if status == 'integrity':
            raise sqlite3.IntegrityError(value)
        if status != 'ok':
            raise sqlite3.OperationalError(value)
        return value

    def call(self, operation, *args):
        self.pipe.send((operation, args))
        return self._receive()

    def close(self):
        self.pipe.close()
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=2)


class _Pool:
    def __init__(self, url, token):
        self.url, self.token = url, token
        self.condition = threading.Condition()
        self.idle = []
        self.workers = set()
        self.creating = 0
        self.closed = False

    def acquire(self):
        deadline = time.monotonic() + TIMEOUT
        with self.condition:
            while True:
                if self.closed:
                    raise sqlite3.OperationalError('Database pool is closed.')
                if self.idle:
                    return self.idle.pop()
                if len(self.workers) + self.creating < POOL_SIZE:
                    self.creating += 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise sqlite3.OperationalError('Database connection capacity timed out.')
                self.condition.wait(remaining)
        worker = None
        try:
            worker = _Worker(self.url, self.token)
            with self.condition:
                if self.closed:
                    raise sqlite3.OperationalError('Database pool is closed.')
                self.workers.add(worker)
            return worker
        except BaseException:
            if worker is not None:
                worker.close()
            raise
        finally:
            with self.condition:
                self.creating -= 1
                self.condition.notify_all()

    def release(self, worker, healthy):
        with self.condition:
            if healthy and not self.closed:
                self.idle.append(worker)
            else:
                self.workers.discard(worker)
                worker.close()
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            for worker in self.workers:
                worker.close()
            self.workers.clear()
            self.idle.clear()
            self.condition.notify_all()


class _Cursor:
    def __init__(self, connection, metadata):
        self.connection = connection
        self.id, self.description, self.lastrowid, self.rowcount = metadata
        self.closed = False

    def _fetch(self, operation, *args):
        if self.closed:
            raise sqlite3.ProgrammingError('Database cursor is closed.')
        return self.connection._call(operation, self.id, *args)

    def fetchone(self):
        return self._fetch('fetchone')

    def fetchmany(self, size=1):
        return self._fetch('fetchmany', size) or []

    def fetchall(self):
        return self._fetch('fetchall') or []

    def close(self):
        if not self.closed:
            self.closed = True
            if not self.connection.closed:
                return self.connection._call('cursor_close', self.id)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class RemoteConnection:
    def __init__(self, pool):
        self.pool = pool
        self.worker = pool.acquire()
        self.closed = False
        self.healthy = True
        self.lock = threading.Lock()

    def _call(self, operation, *args):
        with self.lock:
            if self.closed:
                raise sqlite3.ProgrammingError('Database connection is closed.')
            if not self.healthy:
                raise sqlite3.OperationalError('Database connection failed; operation was not retried.')
            try:
                return self.worker.call(operation, *args)
            except sqlite3.IntegrityError:
                raise
            except Exception:
                self.healthy = False
                raise sqlite3.OperationalError('Remote database operation failed; outcome may be unknown. Operation was not retried.') from None

    @property
    def in_transaction(self):
        return self._call('in_transaction')

    def execute(self, sql, parameters=()):
        return _Cursor(self, self._call('execute', sql, tuple(parameters)))

    def executemany(self, sql, parameters):
        return _Cursor(self, self._call('executemany', sql, list(parameters)))

    def commit(self):
        return self._call('commit')

    def rollback(self):
        return self._call('rollback')

    def close(self):
        with self.lock:
            if self.closed:
                return
            try:
                if self.healthy:
                    self.worker.call('reset')
            except Exception:
                self.healthy = False
            finally:
                self.closed = True
                self.pool.release(self.worker, self.healthy)


class _LeasedConnection(Connection):
    def close(self):
        # Returning the lease rolls back and closes every live cursor once.
        # A failed worker must be discarded without another attempted RPC.
        self.raw.close()


def connect(url, token):
    # Include PID so an inherited pool can never share a pipe after a fork.
    key = (os.getpid(), url, token)
    with _pools_lock:
        pool = _pools.setdefault(key, _Pool(url, token))
    return _LeasedConnection(RemoteConnection(pool))


def shutdown():
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()


atexit.register(shutdown)
