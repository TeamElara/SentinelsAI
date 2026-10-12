"""Write transactions on a remote libSQL database run one at a time per process.

Against Turso, a second `BEGIN IMMEDIATE` made while another connection's
write transaction was open stalled the whole process for ~10 s (the driver
waits inside a call that holds the GIL) and the server then rolled the first
transaction back. These tests use a fake driver connection that behaves the
same way: starting a write transaction while another is open raises, so a test
only passes if the adapter never lets two overlap.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import libsql_adapter
from libsql_adapter import Connection


class FakeServer:
    """The one write slot a remote database has."""

    def __init__(self):
        self.open_writers = 0
        self.max_open_writers = 0
        self.guard = threading.Lock()


class FakeRaw:
    def __init__(self, server, *, fail_on=None):
        self.server = server
        self.in_transaction = False
        self.fail_on = fail_on
        self.closed = False

    def execute(self, sql, parameters=()):
        if self.fail_on and self.fail_on in sql:
            raise ValueError("boom")
        verb = sql.strip().split()[0].lower()
        writes = verb in ("insert", "update", "delete", "create") or sql.lower().startswith("begin immediate")
        if writes and not self.in_transaction:
            with self.server.guard:
                self.server.open_writers += 1
                self.server.max_open_writers = max(self.server.max_open_writers, self.server.open_writers)
                overlapped = self.server.open_writers > 1
            self.in_transaction = True
            if overlapped:
                raise ValueError("stream error: transaction rolled back")
        time.sleep(0.01)  # a round trip
        return self

    executemany = execute

    def _end(self):
        if self.in_transaction:
            with self.server.guard:
                self.server.open_writers -= 1
            self.in_transaction = False

    def commit(self):
        self._end()

    def rollback(self):
        self._end()

    def close(self):
        self.closed = True


def _connection(server, **kw):
    return Connection(FakeRaw(server, **kw), serialize_writes=True)


def _write_transaction(server):
    conn = _connection(server)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t VALUES (1)")
        time.sleep(0.02)
        conn.commit()
    finally:
        conn.close()


def test_concurrent_write_transactions_never_overlap():
    server = FakeServer()
    errors = []

    def run():
        try:
            _write_transaction(server)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert server.max_open_writers == 1


def test_without_the_lock_the_same_workload_overlaps():
    """Guards the test above: the fake really does reject overlapping writers."""
    server = FakeServer()
    errors = []

    def run():
        conn = Connection(FakeRaw(server), serialize_writes=False)
        try:
            conn.execute("BEGIN IMMEDIATE")
            time.sleep(0.02)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=run) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors


def test_reads_do_not_take_the_lock():
    server = FakeServer()
    writer = _connection(server)
    writer.execute("BEGIN IMMEDIATE")  # holds the lock

    reads = []

    def read():
        conn = _connection(server)
        conn.execute("SELECT 1")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        reads.append(True)
        conn.close()

    thread = threading.Thread(target=read)
    thread.start()
    thread.join(timeout=2)
    assert reads == [True]
    writer.commit()
    writer.close()


@pytest.mark.parametrize("end", ["commit", "rollback", "close"])
def test_every_way_a_transaction_ends_releases_the_lock(end):
    server = FakeServer()
    first = _connection(server)
    first.execute("BEGIN IMMEDIATE")
    getattr(first, end)()

    done = []
    thread = threading.Thread(target=lambda: (_write_transaction(server), done.append(True)))
    thread.start()
    thread.join(timeout=2)
    assert done == [True]
    first.close()


def test_a_failed_statement_outside_a_transaction_does_not_keep_the_lock():
    server = FakeServer()
    conn = _connection(server, fail_on="BROKEN")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO BROKEN VALUES (1)")

    done = []
    thread = threading.Thread(target=lambda: (_write_transaction(server), done.append(True)))
    thread.start()
    thread.join(timeout=2)
    assert done == [True]
    conn.close()


def test_the_same_thread_can_hold_two_connections_without_deadlock():
    server = FakeServer()
    a = _connection(server)
    a.execute("CREATE TABLE x(y)")  # a holds the lock, in a transaction
    b = Connection(FakeRaw(server), serialize_writes=True)
    b.execute("SELECT 1")  # not a write: fine
    a.commit()
    a.close()
    b.close()


def test_a_stuck_holder_becomes_an_error_not_a_hang(monkeypatch):
    monkeypatch.setattr(libsql_adapter, "_WRITE_LOCK_WAIT_SECONDS", 0.2)
    server = FakeServer()
    holder = _connection(server)
    holder.execute("BEGIN IMMEDIATE")

    outcome = []

    def waiter():
        conn = _connection(server)
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            outcome.append(str(exc))
        finally:
            conn.close()

    thread = threading.Thread(target=waiter)
    thread.start()
    thread.join(timeout=3)
    assert outcome == ["database is locked"]
    holder.commit()
    holder.close()


def test_a_local_file_connection_is_not_serialized():
    conn = Connection(FakeRaw(FakeServer()))
    conn.execute("BEGIN IMMEDIATE")
    assert conn._holds_write_lock is False
    conn.close()
