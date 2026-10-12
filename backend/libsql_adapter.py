"""Small compatibility layer for the maintained libsql Python SDK.

The SDK returns tuples and ValueError for SQLite failures, and its
executescript currently suppresses errors. Preserve the storage layer's named
rows, constraint exceptions and transaction behavior explicitly.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
import libsql

_BUSY_RETRY_SECONDS = 5.0

# One write transaction at a time per process, on a remote database.
#
# Turso serves a write transaction from a server-side stream. If a second
# connection starts one while the first is open, the server makes the second
# wait, and the driver does that wait inside a native call that keeps Python's
# GIL: the first transaction's thread can't run to commit. Measured against
# Turso (PR notes): the second BEGIN stalled
# the whole process for ~10 s, then the server rolled the first transaction back
# as "idle for too long". Every concurrent quota reservation failed that way.
#
# Waiting on a Python lock releases the GIL, so the thread that owns the
# transaction finishes it and the next one starts when it's done. The service
# runs one worker (see DEPLOY.md), so a process-wide lock covers every writer.
# Reentrant, so a thread that already holds it and opens a second connection is
# no worse off than before; bounded, so a stuck holder surfaces as an error
# rather than a hang.
_WRITE_LOCK = threading.RLock()
_WRITE_LOCK_WAIT_SECONDS = 30.0

_READ_ONLY_STATEMENT = re.compile(r"\s*(select|pragma|explain)\b", re.IGNORECASE)
_PLAIN_BEGIN = re.compile(r"\s*begin(\s+deferred)?(\s+transaction)?\s*;?\s*$", re.IGNORECASE)


class NamedRow(tuple):
    def __new__(cls, values, columns):
        row = super().__new__(cls, values)
        row.columns = tuple(columns)
        return row

    def keys(self):
        return list(self.columns)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                key = [c.casefold() for c in self.columns].index(key.casefold())
            except ValueError:
                raise IndexError("No item with that key") from None
        return super().__getitem__(key)


def _call(operation, *args, retry_locked=True):
    deadline = time.monotonic() + _BUSY_RETRY_SECONDS
    delay = .005
    while True:
        try:
            return operation(*args)
        except (ValueError, libsql.Error) as exc:
            message = str(exc)
            # Connections disable native busy waits; handle contention here.
            # Retry only explicit SQLite lock failures (the statement did not
            # succeed), never ambiguous network errors or constraint failures.
            # Sleeping in Python releases the GIL so the owning thread can
            # commit; a native busy wait can starve that same thread.
            remaining = deadline - time.monotonic()
            if retry_locked and message.lower() in {"database is locked", "database table is locked"} and remaining > 0:
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, .05)
                continue
            if "constraint failed" in message.lower():
                raise sqlite3.IntegrityError(message) from exc
            # Avoid including credentials or a remote service's response in errors.
            if any(part in message.lower() for part in ("http", "authorization", "token", "remote")):
                message = "libSQL operation failed; verify database connectivity and configuration."
            raise sqlite3.OperationalError(message) from exc


class Cursor:
    def __init__(self, raw):
        self.raw = raw

    @property
    def description(self):
        return self.raw.description

    @property
    def lastrowid(self):
        return self.raw.lastrowid

    @property
    def rowcount(self):
        return self.raw.rowcount

    def _row(self, row):
        return None if row is None else NamedRow(row, [c[0] for c in self.description])

    def fetchone(self):
        return self._row(_call(self.raw.fetchone))

    def fetchall(self):
        return [self._row(row) for row in _call(self.raw.fetchall)]

    def fetchmany(self, size=1):
        return [self._row(row) for row in _call(self.raw.fetchmany, size)]

    def close(self):
        return self.raw.close()

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class Connection:
    def __init__(self, raw, *, serialize_writes=False):
        self.raw = raw
        self._serialize_writes = serialize_writes
        self._holds_write_lock = False

    def _acquire_write_lock(self, sql):
        """Take the process-wide write lock before the first statement that can
        write, and keep it until this connection commits, rolls back or closes."""
        if not self._serialize_writes or self._holds_write_lock:
            return
        if _READ_ONLY_STATEMENT.match(sql) or _PLAIN_BEGIN.match(sql):
            return
        if not _WRITE_LOCK.acquire(timeout=_WRITE_LOCK_WAIT_SECONDS):
            raise sqlite3.OperationalError("database is locked")
        self._holds_write_lock = True

    def _release_write_lock(self):
        if self._holds_write_lock:
            self._holds_write_lock = False
            _WRITE_LOCK.release()

    @property
    def in_transaction(self):
        return self.raw.in_transaction

    def execute(self, sql, parameters=()):
        self._acquire_write_lock(sql)
        try:
            return Cursor(_call(self.raw.execute, sql, tuple(parameters)))
        except BaseException:
            # A failed statement outside a transaction leaves nothing to commit
            # or roll back, so nothing would release the lock.
            if self._holds_write_lock and not self.raw.in_transaction:
                self._release_write_lock()
            raise

    def executemany(self, sql, parameters):
        # A batch can have partially run before failing. Its caller must roll
        # back the transaction; never replay the whole batch automatically.
        self._acquire_write_lock(sql)
        try:
            return Cursor(_call(self.raw.executemany, sql, [tuple(p) for p in parameters], retry_locked=False))
        except BaseException:
            if self._holds_write_lock and not self.raw.in_transaction:
                self._release_write_lock()
            raise

    def executescript(self, script):
        # Use SQLite's statement parser rather than split(';'): literals and
        # comments can contain semicolons. Never use the SDK's error-swallowing
        # executescript method. Transaction ownership remains with the caller.
        statement = ""
        for character in script:
            statement += character
            if character == ";" and sqlite3.complete_statement(statement):
                self.execute(statement)
                statement = ""
        if statement.strip():
            self.execute(statement)

    def commit(self):
        try:
            return _call(self.raw.commit)
        finally:
            self._release_write_lock()

    def rollback(self):
        try:
            return _call(self.raw.rollback)
        finally:
            self._release_write_lock()

    def close(self):
        # Match sqlite3: closing an uncommitted connection discards its writes.
        try:
            self.rollback()
        finally:
            self._release_write_lock()
            self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.rollback() if exc_type else self.commit()
        return False
