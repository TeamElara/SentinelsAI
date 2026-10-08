"""The repo Code Patterns agent -- scans committed source for constructs
that are *indicative* of a security problem, not proof of one: `eval`,
`shell=True`, string-built SQL, `dangerouslySetInnerHTML`, `verify=False`,
`DEBUG=True`, wildcard CORS, `pickle.loads`. Every one of these has a
legitimate use somewhere, which is exactly why every finding here is
`Status.WARN`, never `FAIL` -- the checklist's existing "inferred" tier
(weak passive signal, not conclusive) is the right home for this whole
agent, the same way R7 is described in `docs/PLAN-v3.md`.

Like `agents/repo/config.py`, this is plain regex/line scanning, not a real
parser -- these patterns don't need one, and the input is untrusted repo
content.
"""
from __future__ import annotations

import re
from scan_coverage import record
from dataclasses import dataclass
from typing import Callable

from agents.repo.base import BaseRepoAgent, RepoContext
from models import EvidenceKind, Finding, Severity, Status

OWASP_INJECTION = "A03:2021 - Injection"
OWASP_CRYPTO_FAILURE = "A02:2021 - Cryptographic Failures"
OWASP_MISCONFIG = "A05:2021 - Security Misconfiguration"
OWASP_INTEGRITY_FAILURE = "A08:2021 - Software and Data Integrity Failures"

_MAX_FINDINGS = 100

_SQL_KEYWORD_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def _looks_like_sql_concat(line: str) -> bool:
    """A SQL keyword sharing a line with an f-string interpolation or a `+`
    next to a quoted string -- not proof of injection (the value glued in
    could be a constant), but exactly the shape a parameterized query
    (`cursor.execute("...%s...", (value,))`) never has."""
    if not _SQL_KEYWORD_RE.search(line):
        return False
    has_fstring_interpolation = bool(re.search(r'f["\']', line)) and "{" in line
    has_string_concat = "+" in line and bool(re.search(r'["\']', line))
    return has_fstring_interpolation or has_string_concat


@dataclass
class PatternCheck:
    slug: str
    label: str
    severity: Severity
    owasp: str
    description: str
    remediation: str
    matches: Callable[[str], bool]


def _regex_check(pattern: re.Pattern[str]) -> Callable[[str], bool]:
    return lambda line: pattern.search(line) is not None


_PATTERN_CHECKS: list[PatternCheck] = [
    PatternCheck(
        "eval-call", "eval() call", Severity.MEDIUM, OWASP_INJECTION,
        "eval() runs its argument as code. If any part of that string ever "
        "comes from user input, this is arbitrary code execution.",
        "Replace eval() with a safe, specific alternative (e.g. json.loads for data, a lookup dict for dynamic dispatch).",
        _regex_check(re.compile(r"\beval\s*\(")),
    ),
    PatternCheck(
        "exec-call", "exec() call", Severity.MEDIUM, OWASP_INJECTION,
        "exec() runs its argument as Python code -- the same risk as eval(), for statements instead of expressions.",
        "Replace exec() with a safe, specific alternative for what it's actually being used to do.",
        _regex_check(re.compile(r"\bexec\s*\(")),
    ),
    PatternCheck(
        "shell-true", "subprocess call with shell=True", Severity.MEDIUM, OWASP_INJECTION,
        "shell=True runs the command through an actual shell, so any part of it built from user input can inject extra shell commands.",
        "Pass the command as a list (shell=False, the default) so arguments are never re-interpreted by a shell.",
        _regex_check(re.compile(r"shell\s*=\s*True")),
    ),
    PatternCheck(
        "sql-concat", "SQL built by string concatenation/f-string", Severity.HIGH, OWASP_INJECTION,
        "Gluing values directly into a SQL string (instead of passing them as separate parameters) is the classic shape of a SQL injection vulnerability.",
        "Use parameterized queries (e.g. cursor.execute(\"...WHERE id = %s\", (id,))) instead of building the SQL string yourself.",
        _looks_like_sql_concat,
    ),
    PatternCheck(
        "dangerously-set-inner-html", "dangerouslySetInnerHTML", Severity.MEDIUM, OWASP_INJECTION,
        "Rendering raw HTML skips React's normal escaping -- if any part of that HTML comes from user input, this is a stored/reflected XSS vector.",
        "Render as plain text/JSX where possible, or sanitize the HTML (e.g. DOMPurify) before passing it in.",
        _regex_check(re.compile(r"dangerouslySetInnerHTML")),
    ),
    PatternCheck(
        "verify-false", "verify=False (TLS verification disabled)", Severity.HIGH, OWASP_CRYPTO_FAILURE,
        "Disabling certificate verification means this code can't tell a real server from an attacker impersonating one.",
        "Remove verify=False. If a self-signed/internal CA cert is the actual problem, point verify at that CA bundle instead of disabling checks entirely.",
        _regex_check(re.compile(r"verify\s*=\s*False\b")),
    ),
    PatternCheck(
        "debug-true", "DEBUG = True", Severity.MEDIUM, OWASP_MISCONFIG,
        "Debug mode typically exposes stack traces, source snippets, and internal settings to anyone who can trigger an error.",
        "Set DEBUG to False (or read it from an environment variable that defaults to off) before deploying.",
        _regex_check(re.compile(r"\bDEBUG\s*=\s*True\b")),
    ),
    PatternCheck(
        "cors-wildcard", "wildcard CORS origin", Severity.MEDIUM, OWASP_MISCONFIG,
        "A wildcard CORS origin lets any website read this API's responses from a logged-in user's browser.",
        "List the specific origins that should be allowed to call this API instead of \"*\".",
        _regex_check(re.compile(
            r'(?:allow_origins|origins)\s*[:=]\s*(?:\[)?\s*["\']\*["\']', re.IGNORECASE
        )),
    ),
    PatternCheck(
        "pickle-loads", "pickle.load(s)", Severity.HIGH, OWASP_INTEGRITY_FAILURE,
        "Unpickling is not just parsing data -- a crafted pickle can execute arbitrary code the moment it's loaded.",
        "Use a data-only format (JSON, etc.) unless the pickle source is fully trusted and never influenced by outside input.",
        _regex_check(re.compile(r"\bpickle\.loads?\s*\(")),
    ),
]


class PatternsAgent(BaseRepoAgent):
    name = "repo-patterns"
    display_name = "Code Patterns"
    purpose = "Flags risky-looking code constructs -- indicative, not conclusive proof, of a real vulnerability."
    checks = [check.label for check in _PATTERN_CHECKS]
    category = "Code Patterns"

    async def scan(self, context: RepoContext) -> list[Finding]:
        findings: list[Finding] = []
        for repo_file in context.files:
            if len(findings) >= _MAX_FINDINGS:
                record("Pattern findings budget", "partial", "Finding limit reached before all files were checked.")
                break
            try:
                text = repo_file.abs_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                record(repo_file.path, "unavailable", "Source file could not be read.")
                continue

            file_slug = repo_file.path.replace("/", "-")
            for line_no, raw_line in enumerate(text.splitlines(), start=1):
                line = raw_line.strip()
                if not line:
                    continue
                for check in _PATTERN_CHECKS:
                    if not check.matches(line):
                        continue
                    snippet = line if len(line) <= 200 else line[:200] + "..."
                    findings.append(Finding(
                        id=f"pattern-{check.slug}-{file_slug}-L{line_no}",
                        title=f"{check.label} in {repo_file.path}",
                        category="Code Patterns",
                        severity=check.severity,
                        status=Status.WARN,
                        owasp=check.owasp,
                        file_path=repo_file.path,
                        line=line_no,
                        evidence=f"{repo_file.path}:{line_no} -> {snippet}",
                        description=check.description,
                        remediation=check.remediation,
                        evidence_items=[
                            self.evidence(EvidenceKind.FILE_SNIPPET, check.label, f"{repo_file.path}:{line_no} -> {snippet}")
                        ],
                    ))

        if len(findings) > _MAX_FINDINGS:
            record("Pattern findings budget", "partial", "Finding limit truncated the output.")
        return findings[:_MAX_FINDINGS]
