"""Generic async wrapper around the Groq chat-completions endpoint.

M12 extracted this from analyst.py so that fixes.py and chat.py can call the
same LLM without duplicating the HTTP boilerplate or the graceful-degradation
contract. All three callers follow the same rule: None back = no key or any
failure; the caller degrades gracefully, never raises to the scan.
"""
from __future__ import annotations

import os
import asyncio

import httpx
from fastapi import HTTPException
from usage import reserve_global_ai

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# One model, used by all three AI features. Reasoner model on the free tier;
# low reasoning_effort keeps token use small for short tasks.
DEFAULT_MODEL = "openai/gpt-oss-20b"


def get_api_key() -> str | None:
    """Return the Groq API key from the environment, or None if absent."""
    return os.environ.get("GROQ_API_KEY") or None


async def call_groq(
    messages: list[dict],
    *,
    max_tokens: int = 800,
    reasoning_effort: str = "low",
    model: str = DEFAULT_MODEL,
    interactive: bool = False,
) -> str | None:
    """Call the Groq chat-completions endpoint and return the content string.

    Returns None instead of raising on any failure — missing key, rate limit,
    network error, unexpected response shape. Callers treat None as "AI
    unavailable" and degrade gracefully; they never get an exception from here.
    """
    api_key = get_api_key()
    if not api_key:
        return None

    # Count actual provider attempts across summaries, fixes and chat. Never
    # refund this cost ceiling: even a failed request can consume provider quota.
    try:
        await asyncio.to_thread(reserve_global_ai)
    except HTTPException:
        if interactive:
            raise
        return None

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                _GROQ_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "max_completion_tokens": max_tokens,
                    "reasoning_effort": reasoning_effort,
                    "messages": messages,
                },
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return content.strip() if content else None
    except Exception:
        return None
