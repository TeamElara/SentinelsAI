"""Shared data models for Sentinels.

Every scanner agent produces a list of `Finding` objects. Keeping one shared
shape means the orchestrator, scorer, AI analyst and report layer all speak the
same language.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator


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


class SubdomainEntry(BaseModel):
    """One row of the subdomain inventory (PLAN-v4 §V6) — mirrors
    `RepoFileEntry`'s precedent for a structured, non-Finding list carried
    alongside a `ScanReport`.
    """

    host: str
    record_type: str                        # "A" | "AAAA" | "CNAME"
    record_value: str
    source: str                              # "certificate" | "ct-log" | "common-name"
    http_status: Optional[int] = None
    scheme: Optional[str] = None             # "https" | "http" | None
    tls_valid: Optional[bool] = None
    server: Optional[str] = None
    redirects_to: Optional[str] = None
    issue_count: int = 0


class CheckCoverage(BaseModel):
    check: str
    status: Literal["completed", "partial", "skipped", "unavailable", "failed"]
    reason: str
    required: bool = True


class AgentResult(BaseModel):
    """Everything one agent returns, plus timing for the live progress UI."""

    agent: str
    findings: list[Finding] = Field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None
    coverage: list[CheckCoverage] = Field(default_factory=list)

    @computed_field
    @property
    def coverage_status(self) -> str:
        if self.error:
            return "failed"
        required = [c for c in self.coverage if c.required]
        if not self.coverage:
            return "unavailable"
        if all(c.status == "completed" for c in required):
            return "completed"
        statuses = {c.status for c in required}
        return next(iter(statuses)) if len(statuses) == 1 else "partial"


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
    scorer_version: str = "legacy-v1"
    summary: str = ""                    # AI-written executive summary
    counts: dict[str, int] = Field(default_factory=dict)  # findings by severity
    findings: list[Finding] = Field(default_factory=list)
    agents: list[AgentResult] = Field(default_factory=list)
    readiness_score: Optional[int] = None        # 0-100, % of auto items passing
    deployment_status: Optional[str] = None      # "ready" | "caution" | "blocked"
    checklist: list["ChecklistItem"] = Field(default_factory=list)
    subdomains: list["SubdomainEntry"] = Field(default_factory=list)

    @computed_field
    @property
    def provisional(self) -> bool:
        return not self.agents or any(a.coverage_status != "completed" for a in self.agents)

    @model_validator(mode="after")
    def require_coverage_for_readiness(self):
        if self.provisional and self.deployment_status != "blocked":
            self.deployment_status = "incomplete"
        return self


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
    scorer_version: str = "legacy-v1"
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


class User(BaseModel):
    """One signed-in person, as Sentinels knows them (PLAN-v5 Stage 0).

    Everything here comes from GitHub — Sentinels stores no password, no email,
    and no OAuth token. The access token handed back during sign-in is used
    once to read this identity and then discarded; what persists is only enough
    to say "this scan is yours" and "this installation belongs to you".
    """

    id: int                              # our own row id, what other tables reference
    github_id: int                       # GitHub's numeric id — stable across renames
    github_login: str                    # the @handle — can change, so never a key
    avatar_url: Optional[str] = None


class FilePatch(BaseModel):
    """One file's exact change inside a FixPlan (PLAN-v5 Stage A).

    Carries enough of the before/after content for a preview UI to render
    without re-fetching anything, plus `original_sha` -- the blob SHA the
    file had when this patch was built, which Stage B re-checks immediately
    before writing anything ("Drift aborts", CLAUDE.md's remediation rule 7).
    """

    path: str                                # repo-relative, forward-slash
    action: Literal["create", "modify", "delete"]
    original_sha: Optional[str] = None       # the drift anchor; None for action="create"
    original_content: Optional[str] = None   # None for action="create"
    new_content: Optional[str] = None        # None for action="delete"
    diff: str = ""                           # unified diff text, for the preview UI


class FixPlan(BaseModel):
    """A deterministic, machine-actionable fix for one Finding.

    This is what Stage A produces: plain Python decided every byte of every
    patch here. No model ever sees this shape before it's shown to a user
    (CLAUDE.md's remediation rule 1: "The LLM never generates a security
    patch"). `FixSuggestion` (above) is the AI's English explanation of a
    finding; this is the different, later thing -- an actual diff.
    """

    finding_key: str                         # Finding.id this plan addresses
    fixer_slug: str                          # which Fixer produced it
    tier: int                                # 1 (certain) or 2 (review-required) -- remediation/tiers.py
    summary: str                             # one-line "what this fix does"
    patches: list[FilePatch] = Field(default_factory=list)
    created_at: str                          # ISO 8601 UTC


class FixSummary(BaseModel):
    """How many of a scan's current findings have a deterministic Fixer --
    the number the scan overview page's badge shows, without making anyone
    open an agent page first to find out.

    Deliberately just a count plus one place to send the click: computing
    "which fixer" for every finding and rendering a menu of them is a bigger
    piece of UI than a summary badge needs, and every fixable finding is
    already reachable from its own agent page.
    """

    fixable_count: int
    first_finding_key: Optional[str] = None  # None only when fixable_count == 0
    first_agent: Optional[str] = None        # which agent page to link to


class FixApplicationState(str, Enum):
    """Stage A only ever produces `PLANNED`. Every later value belongs to
    Stage B (opening a PR) and Stage C (verifying it) -- named now because
    the migration that adds `fix_applications` lands in this stage, ahead of
    the code that transitions through most of these."""

    PLANNED = "planned"
    PR_OPEN = "pr_open"
    MERGED = "merged"
    VERIFIED = "verified"
    FAILED = "failed"
    ABANDONED = "abandoned"


class VerificationResult(BaseModel):
    """What re-running one agent actually observed after a fix was merged
    (PLAN-v5 Stage C).

    Every number here comes from the untouched deterministic
    `scoring.calculate_score` over real, freshly observed findings — never
    from a model, and never from assuming that writing a patch worked
    (CLAUDE.md's "verification closes the loop"). `before` is recomputed from
    the stored report rather than read off `ScanReport.score`, so both sides
    of the delta come out of the same function on the same day.
    """

    scan_id: str
    finding_key: str                         # the Finding.id this verified
    agent: str                               # the agent slug that was re-run
    ref: str                                 # the git ref the re-run observed
    verified_at: str                         # ISO 8601 UTC

    before: int                              # score with the stored findings
    after: int                               # score with this agent's fresh findings
    delta: int                               # after - before; positive is an improvement
    scorer_version: str = "legacy-v1"        # both before/after use this scorer
    stored_scorer_version: str = "legacy-v1"
    stored_score: Optional[int] = None        # historical score, never overwritten

    target_fixed: bool                       # the verified finding itself: FAIL -> gone
    fixed: list[str] = Field(default_factory=list)          # ids this agent no longer reports
    still_failing: list[str] = Field(default_factory=list)  # ids it still reports

    application_id: Optional[str] = None     # the fix_applications row, when there is one
    recorded: bool = False                   # whether that row was updated to `verified`


class FixApplication(BaseModel):
    """One row of the remediation audit trail -- a FixPlan someone acted on.

    `plan` is a *frozen copy* of the FixPlan that was actually applied, not a
    pointer to one. `fix_plans` is replaced wholesale on every re-plan, so a
    reference would eventually describe a different patch than the one in the
    pull request -- or vanish entirely (PLAN-v5.md conflict #9). An audit
    record has to survive its subject changing.
    """

    id: str                                  # uuid4
    scan_id: str
    finding_key: str
    fixer_slug: str
    tier: int
    state: FixApplicationState
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    branch: Optional[str] = None
    plan: Optional[FixPlan] = None           # the immutable snapshot
    verification: Optional[VerificationResult] = None  # Stage C, once the fix was re-observed
    created_at: str
    updated_at: str


class GitHubInstallation(BaseModel):
    """One repository-write grant: this user installed the Sentinels GitHub
    App on this GitHub account (PLAN-v5 Stage B).

    Distinct from signing in. Signing in says "GitHub confirms who you are";
    an installation says "you have given Sentinels permission to write to
    repositories under this account". A user can do the first without ever
    doing the second, which is why these are two separate flows.
    """

    id: int                                  # our own row id
    installation_id: int                     # GitHub's id for the installation
    account_login: str                       # the org/user the App is installed on
    repo_selection: str                      # "all" | "selected"
    created_at: str
    revoked_at: Optional[str] = None         # None while live


class ScanRepoLink(BaseModel):
    """A URL scan borrowing a repository's write path (PLAN-v5 Stage D).

    A header finding (`agents/headers.py`) has no `file_path` -- it came from
    observing a live site, not a repository -- so none of the existing
    Fixers have anywhere to write a patch. This is the bridge: one row says
    "this URL scan's site is served by this repository", after which
    `remediation/linking.py`'s `repo_target()` treats the scan exactly like
    a repo scan for the rest of the fix-plan/apply/verify pipeline.
    """

    scan_id: str
    user_id: int
    installation_id: int
    owner: str
    repo: str
    ref: Optional[str] = None                # None = the repository's own default branch
    linked_at: str


class FixApplyPreview(BaseModel):
    """What `POST /scans/{id}/fix/apply` with `dry_run=true` returns: exactly
    what *would* be pushed, with nothing written anywhere.

    The whole point of Stage B's step 7 -- every check has already run by the
    time this is built, so a preview that comes back clean means the live
    call would have succeeded too.
    """

    repo: str                                # "owner/name"
    base_branch: str
    branch: str
    commit_message: str
    pr_title: str
    pr_body: str
    finding_keys: list[str] = Field(default_factory=list)
    patches: list[FilePatch] = Field(default_factory=list)


class FixApplyResult(BaseModel):
    """What a live apply produced: one branch, one pull request, and one audit
    row per finding it covers.

    `already_applied` distinguishes "Sentinels just opened this" from "this
    finding already had an open pull request, here it is again" — the second
    is a successful, deliberately unrepeated write, not a new one.
    """

    repo: str                                # "owner/name"
    branch: str
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    already_applied: bool = False
    applications: list[FixApplication] = Field(default_factory=list)


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


class AuditLogEntry(BaseModel):
    """One row of `audit_log`, read back (PLAN-v5 Stage E) -- the same rows
    `write_audit` has produced since Stage B, now surfaced instead of being
    reachable only by a direct query.

    `scan_url`/`scan_target_type` are carried alongside `scan_id` rather than
    making the frontend resolve the scan itself: the account-wide `/audit`
    view spans every scan a user has ever touched, so each row has to be
    legible on its own. Both are `None` when the scan no longer exists
    (`audit_log.scan_id` is `ON DELETE SET NULL`) -- the row still means
    something, it just has nowhere left to link to.
    """

    id: int
    scan_id: Optional[str] = None
    scan_url: Optional[str] = None
    scan_target_type: Optional[str] = None
    finding_key: Optional[str] = None
    action: str                  # e.g. "pr_opened", "pr_merged", "fix_verified"
    detail: str = ""
    created_at: str
