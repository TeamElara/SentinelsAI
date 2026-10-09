"""Durable UTC daily budgets. Every increment is conditional and atomic.

Reservations charge at start and make retries/refunds idempotent. Counters
survive process restarts and use the database primary, never a local replica.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import uuid

from fastapi import HTTPException
from db import get_connection

logger = logging.getLogger(__name__)

WORKER_ID = str(uuid.uuid4())
DEFAULT_LIMITS = {"url_scan": 10, "repo_scan": 10, "verify": 10, "pdf": 10, "ai_fix": 20, "chat": 20}
STALE_SECONDS = 180


def _now():
    return datetime.now(timezone.utc)


def _env_int(name, default):
    """An integer setting. A value that isn't one falls back to the default.

    Raising instead would surface as a 400 ("invalid literal for int()") on
    the scan routes, blaming the user for the operator's typo.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not a whole number; using the default %s", name, raw, default)
        return default


def _limit(kind):
    if kind not in DEFAULT_LIMITS:
        raise ValueError("Unknown quota kind")
    value = _env_int("SENTINELS_DAILY_" + kind.upper(), DEFAULT_LIMITS[kind])
    if value < 0:
        raise ValueError("Daily limits cannot be negative")
    return value


def _exceeded(kind, limit):
    now = _now()
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return HTTPException(status_code=429, detail=f"Daily {kind.replace('_', ' ')} limit reached ({limit}). Resets at 00:00 UTC.",
                         headers={"Retry-After": str(max(1, int((reset - now).total_seconds())))})


@dataclass(frozen=True)
class Reservation:
    id: str
    new: bool

    def complete(self):
        conn = get_connection()
        try:
            conn.execute("UPDATE usage_reservations SET state='completed' WHERE id=? AND state='active'", (self.id,))
            conn.commit()
        finally:
            conn.close()

    def refund(self):
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _refund(conn, self.id)
            conn.commit()
        finally:
            conn.close()


def _refund(conn, reservation_id):
    row = conn.execute("SELECT * FROM usage_reservations WHERE id=?", (reservation_id,)).fetchone()
    if row is None or row["state"] != "active":
        return
    changed = conn.execute("UPDATE usage_reservations SET state='refunded' WHERE id=? AND state='active'", (reservation_id,)).rowcount
    if changed:
        conn.execute("UPDATE usage SET count=MAX(0,count-1) WHERE user_id=? AND day=? AND kind=?",
                     (row["user_id"], row["day"], row["kind"]))
        conn.execute("UPDATE scan_jobs SET status='failed', error='Scanner restarted before this job completed. Its quota was refunded.' WHERE id=? AND status='running'", (reservation_id,))


def _recover_stale(conn, now):
    cutoff = (now - timedelta(seconds=STALE_SECONDS)).isoformat()
    rows = conn.execute("SELECT id FROM usage_reservations WHERE state='active' AND worker != ? AND created_at < ?", (WORKER_ID, cutoff)).fetchall()
    for row in rows:
        _refund(conn, row["id"])


def has_reservation(user_id, kind, request_key):
    """Whether this user already has a reservation for this request id.

    Used to tell a reconnect from a new start, so a reconnect is not logged
    as a second permission statement.
    """
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM usage_reservations WHERE user_id=? AND kind=? AND request_key=?",
            (user_id, kind, request_key),
        ).fetchone() is not None
    finally:
        conn.close()


def _seed_from_carryover(conn, user_id, day):
    """Restore today's counts a previous account with this GitHub id left behind.

    Deleting an account cascades its `usage` rows away; `carry_over_for_deletion`
    parked today's counts under the GitHub id first, so a person who deletes
    and signs in again starts the day where they left off. Existing rows win.
    """
    conn.execute(
        "INSERT INTO usage(user_id,day,kind,count) "
        "SELECT ?, c.day, c.kind, c.count FROM usage_carryover c "
        "JOIN users u ON u.github_id = c.github_id WHERE u.id = ? AND c.day = ? "
        "ON CONFLICT DO NOTHING",
        (user_id, user_id, day),
    )


def carry_over_for_deletion(conn, user_id):
    """Park today's counts for a user about to be deleted. Caller commits.

    Keeps the larger count if the same GitHub id deletes more than once in a
    day, and drops carried rows from earlier days.
    """
    day = _now().date().isoformat()
    conn.execute("DELETE FROM usage_carryover WHERE day < ?", (day,))
    conn.execute(
        "INSERT INTO usage_carryover(github_id,day,kind,count) "
        "SELECT u.github_id, g.day, g.kind, g.count FROM usage g "
        "JOIN users u ON u.id = g.user_id WHERE g.user_id = ? AND g.day = ? AND g.count > 0 "
        "ON CONFLICT(github_id,day,kind) DO UPDATE SET count = MAX(count, excluded.count)",
        (user_id, day),
    )


def reserve(user_id, kind, *, request_key=None, target=None):
    limit = _limit(kind)
    now = _now()
    day = now.date().isoformat()
    key = request_key or str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _recover_stale(conn, now)
        existing = conn.execute("SELECT id,state FROM usage_reservations WHERE user_id=? AND kind=? AND request_key=?", (user_id, kind, key)).fetchone()
        if existing is not None:
            if target is not None:
                job = conn.execute("SELECT target FROM scan_jobs WHERE id=?", (existing["id"],)).fetchone()
                if job is None or job["target"] != target:
                    raise HTTPException(status_code=409, detail="This scan request ID belongs to a different target.")
            conn.commit()
            return Reservation(existing["id"], False)
        _seed_from_carryover(conn, user_id, day)
        conn.execute("INSERT INTO usage(user_id,day,kind,count) VALUES (?,?,?,0) ON CONFLICT DO NOTHING", (user_id, day, kind))
        changed = conn.execute("UPDATE usage SET count=count+1 WHERE user_id=? AND day=? AND kind=? AND count < ?", (user_id, day, kind, limit)).rowcount
        if changed != 1:
            raise _exceeded(kind, limit)
        reservation_id = str(uuid.uuid4())
        conn.execute("INSERT INTO usage_reservations(id,user_id,day,kind,request_key,worker,created_at) VALUES (?,?,?,?,?,?,?)",
                     (reservation_id, user_id, day, kind, key, WORKER_ID, now.isoformat()))
        if target is not None:
            conn.execute("INSERT INTO scan_jobs(id,user_id,target,kind) VALUES (?,?,?,?)", (reservation_id, user_id, target, kind))
        conn.commit()
        return Reservation(reservation_id, True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reserve_global_ai():
    limit = _env_int("SENTINELS_DAILY_GLOBAL_AI", 100)
    if limit < 0:
        raise ValueError("Global AI limit cannot be negative")
    day = _now().date().isoformat()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO global_usage(day,kind,count) VALUES (?,'ai',0) ON CONFLICT DO NOTHING", (day,))
        if conn.execute("UPDATE global_usage SET count=count+1 WHERE day=? AND kind='ai' AND count < ?", (day, limit)).rowcount != 1:
            raise _exceeded("AI service", limit)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def usage_for(user_id):
    conn = get_connection()
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        _recover_stale(conn, now)
        _seed_from_carryover(conn, user_id, now.date().isoformat())
        counts = {r["kind"]: r["count"] for r in conn.execute("SELECT kind,count FROM usage WHERE user_id=? AND day=?", (user_id, now.date().isoformat())).fetchall()}
        conn.commit()
        return {"day": now.date().isoformat(), "resets_at": (now + timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0).isoformat(),
                "budgets": {kind: {"limit": _limit(kind), "used": counts.get(kind, 0), "remaining": max(0, _limit(kind)-counts.get(kind, 0))} for kind in DEFAULT_LIMITS}}
    finally:
        conn.close()
