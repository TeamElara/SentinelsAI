"""AI fix suggestion generator with DB caching.

The cache means the first POST to /scans/{id}/findings/{key}/fix is a live
LLM call (slow); the second is a DB lookup (instant). `?regenerate=true`
bypasses and overwrites. With no API key: returns None, endpoint returns a
clean "unavailable" response.
"""
from __future__ import annotations

import json
import asyncio
from weakref import WeakValueDictionary
from datetime import datetime, timezone

from pydantic import ValidationError

from ai.client import call_groq, get_api_key, DEFAULT_MODEL
from ai.prompts import build_fix_messages, PROMPT_VERSION
from db import get_connection
from models import Finding, FixSuggestion
from storage.fixes import get_cached_fix, get_finding_db_id, save_fix
from usage import reserve

_locks = WeakValueDictionary()


async def get_or_generate_fix(
    scan_id: str,
    finding_key: str,
    finding: Finding,
    *,
    regenerate: bool = False,
    user_id: int | None = None,
) -> FixSuggestion | None:
    key = (scan_id, finding_key)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    async with lock:
        return await _generate(scan_id, finding_key, finding, regenerate=regenerate, user_id=user_id)


async def _generate(scan_id, finding_key, finding, *, regenerate, user_id):
    """Return a cached fix or generate a new one.

    Returns None when no GROQ_API_KEY is set — callers return a clean
    "unavailable" JSON response rather than a 500.
    """
    conn = get_connection()
    reservation = None
    succeeded = False
    try:
        finding_id = get_finding_db_id(conn, scan_id, finding_key)
        if finding_id is None:
            return None

        if not regenerate:
            cached = get_cached_fix(conn, finding_id, PROMPT_VERSION)
            if cached is not None:
                return cached

        if not get_api_key():
            return None
        if user_id is not None:
            reservation = await asyncio.to_thread(reserve, user_id, 'ai_fix')

        # Cache miss (or regenerate=True) — call the LLM.
        messages = build_fix_messages(finding)
        # More tokens than the analyst summary — fix responses have 6 fields.
        raw = await call_groq(messages, max_tokens=1200, reasoning_effort="low", interactive=True)
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # The model sometimes wraps JSON in a markdown fence — strip and retry.
            stripped = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                return None

        if not isinstance(data, dict):
            return None
        now = datetime.now(timezone.utc).isoformat()
        try:
            suggestion = FixSuggestion(
                **{key: data[key] for key in (
                    "why_it_exists", "security_impact", "exploitation",
                    "recommended_fix", "best_practices", "framework_examples",
                )},
                generated_at=now,
                model=DEFAULT_MODEL,
            )
        except (KeyError, TypeError, ValidationError):
            return None
        save_fix(conn, finding_id, PROMPT_VERSION, DEFAULT_MODEL, suggestion)
        succeeded = True
        return suggestion
    finally:
        conn.close()
        if reservation is not None:
            await asyncio.shield(asyncio.to_thread(reservation.complete if succeeded else reservation.refund))
