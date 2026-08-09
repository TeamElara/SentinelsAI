"""Shared data models for Sentinels.

Every scanner agent produces a list of `Finding` objects. Keeping one shared
shape means the orchestrator, scorer, AI analyst and report layer all speak the
same language.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFO = "Info"


class Status(str, Enum):
    FAIL = "fail"      # the check found a real problem
    WARN = "warn"      # not ideal, worth noting
    PASS = "pass"      # the check passed cleanly


# How many points each failed check subtracts from the starting score of 100.
SEVERITY_PENALTY = {
    Severity.CRITICAL: 25,
    Severity.HIGH: 15,
    Severity.MEDIUM: 8,
    Severity.LOW: 3,
    Severity.INFO: 0,
}


class EvidenceKind(str, Enum):
    """What kind of raw material a piece of evidence is, not what it says."""

    REQUEST = "request"
    RESPONSE_HEADERS = "response_headers"
    DNS_RECORD = "dns_record"
    CERTIFICATE = "certificate"
    HTML_SNIPPET = "html_snippet"
    LOG = "log"
    SCREENSHOT = "screenshot"
    FILE_SNIPPET = "file_snippet"      # a line/block read from a scanned repo file
    DEPENDENCY = "dependency"          # a manifest/lockfile entry (name + version)


class EvidenceItem(BaseModel):
    """One structured, labelled piece of proof behind a Finding.

    `evidence` (the plain string on Finding, below) is the legacy flat form
    every finding has always had. This is the newer, richer form: several of
    these can sit under one finding, each tagged with what kind of material
    it is so a future evidence panel can render a request differently from a
    certificate. Additive — nothing that reads the old `evidence` string
    needs to change.
    """

    kind: EvidenceKind
    label: str                           # short caption, e.g. "Response header"
    content: str                         # the actual evidence text
    content_type: str = "text/plain"
    collected_at: str                    # ISO 8601 UTC, when this was captured
    agent: str                           # which agent slug produced it


class Finding(BaseModel):
    """A single security observation from one agent."""

    id: str                              # stable slug, e.g. "missing-hsts"
    title: str                           # short human title
    category: str                        # Headers | TLS | DNS | Exposure | Recon
    severity: Severity
    status: Status
    owasp: Optional[str] = None          # e.g. "A05:2021 - Security Misconfiguration"
    evidence: str = ""                   # raw technical detail (what we saw)
    description: str = ""                # plain-language "what's wrong" (AI can enrich)
    remediation: str = ""                # how to fix it
    agent: str = ""                      # which agent slug produced this finding
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    file_path: Optional[str] = None      # repo-relative path; None for URL-scan findings
    line: Optional[int] = None           # 1-based line number; None for URL-scan findings

    # The exact URL or host this finding is about, when that isn't just "the
    # scanned site". A subdomain finding is meaningless without it ("HSTS
    # missing" — on *what*?), and it's half of the key that stops two agents
    # seeing one problem from costing points twice (see scoring.py).
    affected_url: Optional[str] = None

    # 0.0-1.0. None means "not applicable" — the check either saw the thing or
    # it didn't, so there's nothing to hedge. Set only where the evidence
    # genuinely leaves room for doubt (a dangling DNS record that *might* be a
    # takeover), so a guess can never be presented as a fact.
    confidence: Optional[float] = None


class AgentResult(BaseModel):
    """Everything one agent returns, plus timing for the live progress UI."""

    agent: str
    findings: list[Finding] = Field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None


class ScanRequest(BaseModel):
    """The JSON body a client POSTs to `/scan`."""

    url: str


class ScanReport(BaseModel):
    """The final object the API hands back to the frontend."""

    id: str = ""                         # uuid4, set once the scan is persisted
    url: str                             # a repo scan's "URL" is its GitHub URL -- same field
    target_type: Literal["url", "repo"] = "url"
    scanned_at: str
    duration_ms: int
    score: int                           # 0-100
    grade: str                           # A-F
    summary: str = ""                    # AI-written executive summary
    counts: dict[str, int] = Field(default_factory=dict)  # findings by severity
    findings: list[Finding] = Field(default_factory=list)
    agents: list[AgentResult] = Field(default_factory=list)
    readiness_score: Optional[int] = None        # 0-100, % of auto items passing
    deployment_status: Optional[str] = None      # "ready" | "caution" | "blocked"
    checklist: list["ChecklistItem"] = Field(default_factory=list)


class AgentInfo(BaseModel):
    """Metadata a scanner agent declares about itself, served by GET /agents."""

    name: str           # slug used in API paths, e.g. "headers"
    display_name: str   # human-readable title, e.g. "Security Headers"
    purpose: str        # one sentence describing what the agent checks
    checks: list[str]   # bullet list of individual checks
    category: str       # the Finding.category this agent owns


class ChecklistItem(BaseModel):
    """One row in the deployment readiness checklist.

    tier determines what produced the state:
      auto         — Sentinels observed this from a finding directly
      inferred     — weak passive signal, labelled "not conclusive"
      self_attested — we never test; the developer answers
    """

    item_key: str
    title: str
    tier: str           # "auto" | "inferred" | "self_attested"
    state: str          # "pass" | "warn" | "fail" | "unknown"
    explanation: str
    suggested_fix: str = ""
    agent: Optional[str] = None   # None for self_attested


class ScanSummary(BaseModel):
    """Lightweight scan record for list endpoints — no findings payload."""

    id: str
    url: str
    target_type: Literal["url", "repo"] = "url"
    score: int
    grade: str
    scanned_at: str
    duration_ms: int
    summary: str = ""
    readiness_score: Optional[int] = None
    deployment_status: Optional[str] = None


class FixSuggestion(BaseModel):
    """AI-generated remediation advice for one finding.

    Cached in the DB by (finding_db_id, prompt_version) — regenerating only
    when explicitly requested or when the prompt version changes.
    """

    why_it_exists: str
    security_impact: str
    exploitation: str           # conceptual only — never a working exploit
    recommended_fix: str
    best_practices: list[str]
    framework_examples: dict[str, str] = Field(default_factory=dict)
    generated_at: str           # ISO 8601 UTC
    model: str                  # which model produced this


class ChatMessage(BaseModel):
    """One turn in the per-scan chatbot conversation."""

    role: str           # "user" | "assistant"
    content: str
    created_at: str     # ISO 8601 UTC


class RepoFileEntry(BaseModel):
    """One row of the file-tree browser for a repo scan (R12).

    Backs the `repo_files` table added in this milestone. Nothing writes
    these yet -- landed now so the milestone that first runs a full repo
    scan has somewhere to persist per-file data.
    """

    path: str                    # forward-slash path, relative to repo root
    size: int
    language: Optional[str] = None
    finding_count: int = 0
