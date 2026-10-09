"""The repo Secrets agent -- scans committed file contents for credentials
that should never have been checked in: well-known provider key shapes,
committed `.env`-shaped files, and generic high-entropy secret-looking
assignments.

CLAUDE.md's repo-side non-negotiable applies hardest here: never echo a
discovered secret. Every finding masks the matched value with `_mask()`
before it ever reaches `Finding.evidence`/`description` or an
`EvidenceItem` -- verified by asserting the real secret string is absent
from `AgentResult.model_dump_json()`, not by eyeballing finding text.
"""
from __future__ import annotations

import math
import re
from scan_coverage import record
from collections import Counter

from agents.repo.base import BaseRepoAgent, RepoContext, RepoFile
from models import EvidenceKind, Finding, Severity, Status

OWASP_SECRETS = "A02:2021 - Cryptographic Failures"

_MAX_FINDINGS = 100
_ENTROPY_THRESHOLD = 4.3
_MIN_SECRET_VALUE_LEN = 20

# Published, well-known token *shapes* -- the same public signatures
# gitleaks/trufflehog use -- not secrets themselves. (slug, human label, pattern)
_PROVIDER_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("aws-key", "AWS Access Key ID", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("github-token", "GitHub token",
     re.compile(r"gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}")),
    ("groq-key", "Groq API key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("openai-key", "OpenAI API key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("stripe-key", "Stripe live key", re.compile(r"(?:sk|rk)_live_[A-Za-z0-9]{24,}")),
    ("google-api-key", "Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("slack-token", "Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private-key", "Private key block",
     re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")),
]

# .env-shaped filenames that are placeholder/template files, not real
# secrets -- excluded from ALL scanning (not just the committed-.env rule),
# since AWS's own docs use a real placeholder key that would otherwise
# false-positive against the AWS pattern above.
_EXCLUDED_ENV_FILES = {".env.example", ".env.sample", ".env.template", ".env.dist"}
_ENV_SHAPED_RE = re.compile(r"^\.env(\.\w+)?$")

# Excluded from the generic entropy check only -- provider-pattern matches
# inside these still fire. npm's "integrity": "sha512-..." is exactly the
# high-entropy-quoted-value shape the generic check looks for.
_LOCKFILE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "Pipfile.lock", "composer.lock",
}

_SECRET_WORD_RE = re.compile(r"key|secret|token|password|pwd|credential|auth", re.IGNORECASE)
# Anchored to line start (indentation allowed): NAME <: or => "quoted value".
_ASSIGNMENT_RE = re.compile(
    r'^\s*["\']?([A-Za-z_][A-Za-z0-9_]*)["\']?\s*[:=]\s*["\']([^"\']{%d,})["\']'
    % _MIN_SECRET_VALUE_LEN
)


def _mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    length = len(s)
    counts = Counter(s)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


class SecretsAgent(BaseRepoAgent):
    name = "repo-secrets"
    display_name = "Secrets"
    purpose = "Scans committed files for credentials that should never have been checked in."
    checks = [
        "Provider key shapes -- AWS, GitHub, Groq, OpenAI, Stripe, Google, Slack, private keys",
        "Committed .env-shaped files",
        "Generic high-entropy secret-named assignments",
    ]
    category = "Secrets"

    async def scan(self, context: RepoContext) -> list[Finding]:
        findings: list[Finding] = []
        for repo_file in context.files:
            if len(findings) >= _MAX_FINDINGS:
                record("Secrets findings budget", "partial", "Finding limit reached before all files were checked.")
                break

            basename = repo_file.path.rsplit("/", 1)[-1]
            if basename in _EXCLUDED_ENV_FILES:
                continue

            try:
                text = repo_file.abs_path.read_text(encoding="utf-8", errors="ignore")
            except (UnicodeDecodeError, OSError):
                record(repo_file.path, "unavailable", "Source file could not be read.")
                continue

            findings.extend(self._provider_matches(repo_file, text))
            if _ENV_SHAPED_RE.match(basename):
                findings.append(self._env_file_finding(repo_file))
            if basename not in _LOCKFILE_NAMES:
                findings.extend(self._generic_entropy_matches(repo_file, text))

        if len(findings) > _MAX_FINDINGS:
            record("Secrets findings budget", "partial", "Finding limit truncated the output.")
        return findings[:_MAX_FINDINGS]

    def _provider_matches(self, repo_file: RepoFile, text: str) -> list[Finding]:
        file_slug = repo_file.path.replace("/", "-")
        findings = []
        for slug, label, pattern in _PROVIDER_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                masked = _mask(match.group(0))
                evidence_text = f"{label}-shaped match in {repo_file.path}:{line} -> {masked}"
                findings.append(Finding(
                    id=f"secret-{slug}-{file_slug}-L{line}",
                    title=f"{label} found in repository",
                    category="Secrets",
                    severity=Severity.CRITICAL,
                    status=Status.FAIL,
                    owasp=OWASP_SECRETS,
                    file_path=repo_file.path,
                    line=line,
                    evidence=evidence_text,
                    description=(
                        f"A string matching the known shape of a {label} is "
                        "committed in this repository. If it's real, anyone "
                        "with read access -- for a public repo, anyone on "
                        "the internet -- can use it."
                    ),
                    remediation=(
                        "Revoke/rotate this credential immediately, then "
                        "remove it from the file and load it from an "
                        "environment variable or secret manager instead. "
                        "Rotating is required even after deleting the line "
                        "-- it still exists in git history."
                    ),
                    evidence_items=[
                        self.evidence(EvidenceKind.FILE_SNIPPET, f"{label} match", evidence_text)
                    ],
                ))
        return findings

    def _env_file_finding(self, repo_file: RepoFile) -> Finding:
        file_slug = repo_file.path.replace("/", "-")
        evidence_text = (
            f"{repo_file.path} matches a .env-shaped filename and is "
            "committed to the repository."
        )
        return Finding(
            id=f"secret-env-committed-{file_slug}",
            title=f"Committed .env-shaped file: {repo_file.path}",
            category="Secrets",
            severity=Severity.CRITICAL,
            status=Status.FAIL,
            owasp=OWASP_SECRETS,
            file_path=repo_file.path,
            line=1,
            evidence=evidence_text,
            description=(
                "Files named .env or .env.<something> conventionally hold "
                "real secrets (API keys, database passwords, tokens) and "
                "are meant to stay local and gitignored, never committed."
            ),
            remediation=(
                "Remove this file from the repository (git rm --cached), "
                "add it to .gitignore, and rotate every credential it "
                "contained -- it remains in git history even after deletion."
            ),
            evidence_items=[
                self.evidence(EvidenceKind.FILE_SNIPPET, "Committed .env file", evidence_text)
            ],
        )

    def _generic_entropy_matches(self, repo_file: RepoFile, text: str) -> list[Finding]:
        file_slug = repo_file.path.replace("/", "-")
        findings = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = _ASSIGNMENT_RE.match(line)
            if not match:
                continue
            identifier, value = match.group(1), match.group(2)
            if not _SECRET_WORD_RE.search(identifier):
                continue
            if _shannon_entropy(value) < _ENTROPY_THRESHOLD:
                continue

            masked = _mask(value)
            evidence_text = f"{repo_file.path}:{line_no} -> {identifier} = {masked}"
            findings.append(Finding(
                id=f"secret-generic-{file_slug}-L{line_no}",
                title=f"Possible hardcoded secret: {identifier}",
                category="Secrets",
                severity=Severity.HIGH,
                status=Status.FAIL,
                owasp=OWASP_SECRETS,
                file_path=repo_file.path,
                line=line_no,
                evidence=evidence_text,
                description=(
                    f"'{identifier}' looks like a secret-holding name, and "
                    "its value has the high randomness real keys/tokens "
                    "have -- not a definitive match, but worth a manual check."
                ),
                remediation=(
                    "If this is a real credential, move it out of the "
                    "source file and into an environment variable or "
                    "secret manager, then rotate it."
                ),
                evidence_items=[
                    self.evidence(
                        EvidenceKind.FILE_SNIPPET, "Possible secret assignment", evidence_text
                    )
                ],
            ))
        return findings
