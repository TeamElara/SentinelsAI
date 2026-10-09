"""AI analyst — turns a scan's findings into a short plain-English summary.

M12 refactored this to use ai/client.py and ai/prompts.py. R10 (PLAN-v3)
added the `target_type` parameter below so the same function serves both
scan types — every existing caller that omits it keeps getting the original
URL-flavoured summary, unchanged. Graceful-degradation contract is
unchanged: "" when no key or any failure.
"""
from __future__ import annotations

from ai.client import call_groq
from ai.prompts import build_analyst_messages, build_repo_analyst_messages
from models import Finding


async def summarize(
    url: str, score: int, grade: str, findings: list[Finding], target_type: str = "url",
    *, coverage_notice: str = "",
) -> str:
    """Return a short plain-English summary, or "" if the AI layer can't run."""
    messages = (
        build_repo_analyst_messages(url, score, grade, findings)
        if target_type == "repo"
        else build_analyst_messages(url, score, grade, findings)
    )
    if coverage_notice:
        messages[0]["content"] += " Coverage is incomplete. Do not claim every check passed or the target is deployment-ready. The score and grade are provisional."
        messages.append({"role": "user", "content": coverage_notice})
    # Reasoning model with low effort — we measured that 200 max_tokens caused
    # reasoning to consume the entire budget leaving empty content (see note 12).
    result = await call_groq(messages, max_tokens=800, reasoning_effort="low")
    return result or ""
