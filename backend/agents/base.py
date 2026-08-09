"""The contract every scanner agent must satisfy.

Two pieces:

- `ScanContext` — everything an agent needs to do its job, built once per scan
  and passed to all agents. Keeps agents from each opening their own
  connections or re-deriving the target URL.
- `BaseAgent` — an abstract base class. Subclasses implement `scan()` only;
  `run()` is inherited as-is and gives every agent, for free, the guarantee
  from CLAUDE.md: "agents must never crash the scan."
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from agents.probe import ResponseCache
from models import AgentResult, EvidenceItem, EvidenceKind, Finding


@dataclass
class ScanContext:
    """Read-only shared state for one scan, handed to every agent."""

    url: str                    # normalized target, e.g. "https://example.com"
    client: httpx.AsyncClient   # one shared connection pool for every agent
    # Both default so every pre-existing agent and call site is untouched —
    # only agents that opt in (V4-V6) ever look at these.
    cache: ResponseCache = field(default_factory=ResponseCache)  # dedupes fetches across agents
    shared: dict = field(default_factory=dict)                   # e.g. subdomain agent -> orchestrator


class BaseAgent(ABC):
    """Base class for all scanner agents.

    Subclasses must set `name` and implement `scan()`. They must NOT override
    `run()` — that method is what makes the crash-proofing and timing apply
    uniformly, with no chance for one agent to forget it.
    """

    name: str = "base"
    display_name: str = "Base"
    purpose: str = ""
    checks: list[str] = []
    category: str = ""

    @abstractmethod
    async def scan(self, context: ScanContext) -> list[Finding]:
        """Do the actual check. Return findings, or raise on genuine failure."""
        raise NotImplementedError

    def evidence(
        self,
        kind: EvidenceKind,
        label: str,
        content: str,
        content_type: str = "text/plain",
    ) -> EvidenceItem:
        """Build one structured EvidenceItem, stamped with this agent's slug
        and the current time. A small helper so subclasses don't each have
        to repeat the timestamp/agent boilerplate by hand."""
        return EvidenceItem(
            kind=kind,
            label=label,
            content=content,
            content_type=content_type,
            collected_at=datetime.now(timezone.utc).isoformat(),
            agent=self.name,
        )

    async def run(self, context: ScanContext) -> AgentResult:
        """Call `scan()`, time it, and never let an exception escape.

        This is the one place the "an agent must never crash the whole scan"
        rule is enforced. Individual agents don't each need their own
        try/except — they get it by inheriting this method unchanged.
        """
        start = time.perf_counter()
        error: str | None = None
        findings: list[Finding] = []
        try:
            findings = await self.scan(context)
            for finding in findings:
                finding.agent = self.name
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see note below
            error = f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.perf_counter() - start) * 1000)
        return AgentResult(
            agent=self.name,
            findings=findings,
            duration_ms=duration_ms,
            error=error,
        )
