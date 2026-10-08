"""The contract every repo-scanner agent must satisfy -- the repo-side
sibling of `agents/base.py`.

Three pieces:

- `RepoFile` / `RepoContext` -- everything a repo agent needs, built once per
  scan and passed to every agent. The file tree is walked exactly once here,
  the same instinct as `ScanContext` sharing one `httpx.AsyncClient` instead
  of every agent opening its own connection.
- `BaseRepoAgent` -- an abstract base class. Deliberately a standalone class,
  not a shared/generic subclass of `BaseAgent`: the `context` parameter's
  type genuinely differs (`RepoContext` vs `ScanContext`), and this
  codebase's own convention -- five near-identical per-check agent files
  rather than one parametrized mega-agent -- already favors this kind of
  small duplication over a premature `Generic[T]` base. `run()` copies
  `BaseAgent.run()`'s crash-proofing logic verbatim: every agent must never
  crash the scan (CLAUDE.md), repo agents included.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from models import AgentResult, EvidenceItem, EvidenceKind, Finding
from repo.fetch import SKIP_DIRS
import scan_coverage


@dataclass
class RepoFile:
    """One file inside the extracted repo tree."""

    path: str        # forward-slash path, relative to the repo root
    abs_path: Path    # where to actually read its bytes from on disk
    size: int


@dataclass
class RepoContext:
    """Read-only shared state for one repo scan, handed to every agent."""

    repo_url: str
    owner: str
    repo: str
    ref: str
    root: Path
    files: list[RepoFile] = field(default_factory=list)  # walked ONCE, shared by all agents
    client: httpx.AsyncClient | None = None


def list_repo_files(root: Path) -> list[RepoFile]:
    """Walk `root` once and return every file in it.

    `fetch_repo` (R1) already skips `SKIP_DIRS` and binaries while
    extracting, so almost nothing here needs filtering again -- the
    `SKIP_DIRS` check is just defense in depth in case `root` was ever
    populated some other way.
    """
    files: list[RepoFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        files.append(
            RepoFile(
                path=path.relative_to(root).as_posix(),
                abs_path=path,
                size=path.stat().st_size,
            )
        )
    return files


class BaseRepoAgent(ABC):
    """Base class for all repo-scanner agents.

    Subclasses must set `name` and implement `scan()`. They must NOT
    override `run()` -- that method is what makes the crash-proofing and
    timing apply uniformly, with no chance for one agent to forget it.
    """

    name: str = "base"
    display_name: str = "Base"
    purpose: str = ""
    checks: list[str] = []
    category: str = ""

    @abstractmethod
    async def scan(self, context: RepoContext) -> list[Finding]:
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
        and the current time -- identical helper to `BaseAgent.evidence`."""
        return EvidenceItem(
            kind=kind,
            label=label,
            content=content,
            content_type=content_type,
            collected_at=datetime.now(timezone.utc).isoformat(),
            agent=self.name,
        )

    async def run(self, context: RepoContext) -> AgentResult:
        """Call `scan()`, time it, and never let an exception escape.

        Identical contract to `BaseAgent.run()`: the one place "an agent
        must never crash the whole scan" is enforced, so individual repo
        agents don't each need their own try/except.
        """
        start = time.perf_counter()
        error: str | None = None
        findings: list[Finding] = []
        coverage_token, coverage_records = scan_coverage.begin()
        try:
            findings = await self.scan(context)
            for finding in findings:
                finding.agent = self.name
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see BaseAgent.run
            error = f"{type(exc).__name__}: {exc}"
        finally:
            scan_coverage.end(coverage_token)
        duration_ms = int((time.perf_counter() - start) * 1000)
        return AgentResult(
            agent=self.name,
            findings=findings,
            duration_ms=duration_ms,
            error=error,
            coverage=scan_coverage.finish(self.checks, findings, error, coverage_records),
        )
