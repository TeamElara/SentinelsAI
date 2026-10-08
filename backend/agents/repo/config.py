"""The repo Config agent -- checks three places projects commonly
misconfigure without realizing it: `.gitignore` completeness, Dockerfile
smells, and risky GitHub Actions CI workflow settings.

Everything here is a text/line scan, the same style as `agents/repo/secrets.py`
-- no YAML/Dockerfile parser dependency needed, since the shapes being
checked for (a trigger keyword, a `FROM`/`USER` line, an `@ref` on a `uses:`
line) are simple enough to catch reliably with a regex, and this agent reads
untrusted repo content (CLAUDE.md's "never execute anything from a scanned
repository" applies to parsers too -- a full YAML loader is more attack
surface than a handful of regexes).
"""
from __future__ import annotations

import fnmatch
import re
from scan_coverage import record

from agents.repo.base import BaseRepoAgent, RepoContext, RepoFile
from models import EvidenceKind, Finding, Severity, Status

OWASP_MISCONFIG = "A05:2021 - Security Misconfiguration"

_SECRET_WORD_RE = re.compile(r"key|secret|token|password|pwd|credential|auth", re.IGNORECASE)


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


class ConfigAgent(BaseRepoAgent):
    name = "repo-config"
    display_name = "Repo Config"
    purpose = "Checks .gitignore completeness, Dockerfile smells, and risky CI workflow settings."
    checks = [
        ".gitignore -- is .env/private-key/node_modules covered?",
        "Dockerfile -- root user, floating :latest tag, secrets baked into ENV/ARG",
        "GitHub Actions -- pull_request_target trigger, unpinned third-party actions",
    ]
    category = "Configuration"

    async def scan(self, context: RepoContext) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._check_gitignore(context))

        for repo_file in context.files:
            basename = repo_file.path.rsplit("/", 1)[-1]
            if basename == "Dockerfile" or basename.startswith("Dockerfile."):
                findings.extend(self._check_dockerfile(repo_file))
            elif repo_file.path.startswith(".github/workflows/") and basename.endswith((".yml", ".yaml")):
                findings.extend(self._check_workflow(repo_file))

        return findings

    # ---- .gitignore ----------------------------------------------------

    def _check_gitignore(self, context: RepoContext) -> list[Finding]:
        gitignore = next((f for f in context.files if f.path == ".gitignore"), None)
        if gitignore is None:
            return [Finding(
                id="gitignore-present",
                title="No .gitignore file found",
                category="Configuration",
                severity=Severity.MEDIUM,
                status=Status.FAIL,
                owasp=OWASP_MISCONFIG,
                evidence="No .gitignore file at the repo root.",
                description=(
                    "Without a .gitignore, it's easy to accidentally commit "
                    ".env files, dependency folders, or key files -- there's "
                    "nothing telling git to leave them alone."
                ),
                remediation="Add a .gitignore covering at minimum .env, key/cert files, and dependency folders.",
            )]

        try:
            text = gitignore.abs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            record(gitignore.path, "unavailable", "Ignore configuration could not be read.")
            return []
        patterns = [
            line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        has_js_manifest = any(
            f.path.rsplit("/", 1)[-1] in ("package.json", "package-lock.json") for f in context.files
        )

        checks = [
            ("gitignore-env", ".env", [".env", ".env.local"], Severity.HIGH),
            ("gitignore-private-keys", "private key files (*.pem/*.key)", ["test.pem", "id_rsa.key"], Severity.HIGH),
        ]
        if has_js_manifest:
            checks.append(("gitignore-node-modules", "node_modules", ["node_modules"], Severity.LOW))

        findings = []
        for check_id, label, sample_names, severity in checks:
            covered = any(
                fnmatch.fnmatch(name, pattern.rstrip("/")) for pattern in patterns for name in sample_names
            )
            if covered:
                findings.append(Finding(
                    id=check_id,
                    title=f"{label} is covered by .gitignore",
                    category="Configuration",
                    severity=Severity.INFO,
                    status=Status.PASS,
                    owasp=OWASP_MISCONFIG,
                    file_path=".gitignore",
                    evidence=f".gitignore has a pattern matching {label}.",
                ))
            else:
                findings.append(Finding(
                    id=check_id,
                    title=f"{label} is not covered by .gitignore",
                    category="Configuration",
                    severity=severity,
                    status=Status.FAIL,
                    owasp=OWASP_MISCONFIG,
                    file_path=".gitignore",
                    evidence=f".gitignore has no pattern matching {label}.",
                    description=f"Nothing stops {label} from being accidentally committed.",
                    remediation=f"Add a pattern covering {label} to .gitignore.",
                ))
        return findings

    # ---- Dockerfile ------------------------------------------------------

    def _check_dockerfile(self, repo_file: RepoFile) -> list[Finding]:
        try:
            text = repo_file.abs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            record(repo_file.path, "unavailable", "Configuration file could not be read.")
            return []

        file_slug = repo_file.path.replace("/", "-")
        findings: list[Finding] = []
        has_user = False

        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            match = re.match(r"^(FROM|USER|ENV|ARG)\s+(.+)$", line, re.IGNORECASE)
            if not match:
                continue
            instruction, rest = match.group(1).upper(), match.group(2).strip()

            if instruction == "USER":
                has_user = True

            elif instruction == "FROM":
                image = rest.split()[0]  # drop a trailing "AS stage" alias
                tag = image.rsplit(":", 1)[1] if ":" in image else "latest"
                if tag == "latest":
                    findings.append(Finding(
                        id=f"docker-latest-tag-{file_slug}-L{line_no}",
                        title=f"Dockerfile uses the floating :latest tag ({image})",
                        category="Configuration",
                        severity=Severity.LOW,
                        status=Status.WARN,
                        owasp=OWASP_MISCONFIG,
                        file_path=repo_file.path,
                        line=line_no,
                        evidence=f"{repo_file.path}:{line_no} -> FROM {rest}",
                        description=(
                            ":latest (or no tag) points at whatever the "
                            "image publisher pushes next -- a build today "
                            "and a build tomorrow can silently pull "
                            "different, unreviewed code."
                        ),
                        remediation=f"Pin {image.split(':')[0]} to a specific version tag or digest.",
                        evidence_items=[
                            self.evidence(EvidenceKind.FILE_SNIPPET, "FROM instruction", f"{repo_file.path}:{line_no} -> FROM {rest}")
                        ],
                    ))

            elif instruction in ("ENV", "ARG"):
                name_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", rest)
                if name_match and _SECRET_WORD_RE.search(name_match.group(1)):
                    value_match = re.match(r"^[A-Za-z_][A-Za-z0-9_]*[=\s]+(.+)$", rest)
                    masked = _mask(value_match.group(1).strip('"\'')) if value_match else "(no value)"
                    findings.append(Finding(
                        id=f"docker-secret-env-{file_slug}-L{line_no}",
                        title=f"Possible secret baked into image: {name_match.group(1)}",
                        category="Configuration",
                        severity=Severity.HIGH,
                        status=Status.FAIL,
                        owasp=OWASP_MISCONFIG,
                        file_path=repo_file.path,
                        line=line_no,
                        evidence=f"{repo_file.path}:{line_no} -> {instruction} {name_match.group(1)} = {masked}",
                        description=(
                            f"{instruction} values are baked permanently "
                            "into the image's layer history -- anyone who "
                            "can pull or inspect the image can read them, "
                            "even if a later layer removes the value."
                        ),
                        remediation="Pass secrets at runtime (e.g. a mounted secret or env var), never as a build-time ENV/ARG.",
                        evidence_items=[
                            self.evidence(
                                EvidenceKind.FILE_SNIPPET, f"{instruction} instruction",
                                f"{repo_file.path}:{line_no} -> {instruction} {name_match.group(1)} = {masked}",
                            )
                        ],
                    ))

        if not has_user:
            findings.append(Finding(
                id=f"docker-root-user-{file_slug}",
                title=f"Dockerfile never switches away from root ({repo_file.path})",
                category="Configuration",
                severity=Severity.MEDIUM,
                status=Status.WARN,
                owasp=OWASP_MISCONFIG,
                file_path=repo_file.path,
                line=1,
                evidence=f"No USER instruction found in {repo_file.path}.",
                description=(
                    "Without a USER instruction, the container runs as root "
                    "by default -- a process compromise inside the "
                    "container gets root inside it for free."
                ),
                remediation="Add a USER instruction switching to a non-root user before CMD/ENTRYPOINT.",
                evidence_items=[
                    self.evidence(EvidenceKind.FILE_SNIPPET, "Dockerfile", f"No USER instruction found in {repo_file.path}.")
                ],
            ))

        return findings

    # ---- GitHub Actions workflows ----------------------------------------

    def _check_workflow(self, repo_file: RepoFile) -> list[Finding]:
        try:
            text = repo_file.abs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            record(repo_file.path, "unavailable", "Configuration file could not be read.")
            return []

        file_slug = repo_file.path.replace("/", "-")
        findings: list[Finding] = []

        if re.search(r"\bpull_request_target\b", text):
            findings.append(Finding(
                id=f"ci-pull-request-target-{file_slug}",
                title=f"Workflow triggers on pull_request_target ({repo_file.path})",
                category="Configuration",
                severity=Severity.HIGH,
                status=Status.WARN,
                owasp=OWASP_MISCONFIG,
                file_path=repo_file.path,
                line=1,
                evidence=f"{repo_file.path} contains a pull_request_target trigger.",
                description=(
                    "pull_request_target runs with write-level permissions "
                    "and repo secrets, but can be triggered by a pull "
                    "request from any fork -- a common way CI pipelines "
                    "get their secrets stolen if the workflow also checks "
                    "out and runs the fork's code."
                ),
                remediation=(
                    "Prefer the plain pull_request trigger, or if "
                    "pull_request_target is required, never check out or "
                    "run untrusted code from the incoming PR inside it."
                ),
                evidence_items=[
                    self.evidence(EvidenceKind.FILE_SNIPPET, "Workflow trigger", f"{repo_file.path} contains a pull_request_target trigger.")
                ],
            ))

        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            match = re.search(r"uses:\s*([\w.\-]+/[\w.\-]+)@([\w.\-]+)", raw_line)
            if not match:
                continue
            owner, ref = match.group(1).split("/")[0], match.group(2)
            if owner in ("actions", "github"):
                continue  # first-party actions, not third-party supply chain risk
            if re.fullmatch(r"[0-9a-f]{40}", ref):
                continue  # already pinned to an immutable commit SHA
            findings.append(Finding(
                id=f"ci-unpinned-action-{file_slug}-L{line_no}",
                title=f"Third-party action not pinned to a commit SHA: {match.group(1)}@{ref}",
                category="Configuration",
                severity=Severity.MEDIUM,
                status=Status.WARN,
                owasp=OWASP_MISCONFIG,
                file_path=repo_file.path,
                line=line_no,
                evidence=f"{repo_file.path}:{line_no} -> uses: {match.group(1)}@{ref}",
                description=(
                    "A tag or branch name can be moved to point at "
                    "different code at any time by whoever controls that "
                    "repository -- pinning to a commit SHA is the only way "
                    "to guarantee the action that runs is the one you reviewed."
                ),
                remediation=f"Pin this action to a full commit SHA instead of {ref!r}.",
                evidence_items=[
                    self.evidence(EvidenceKind.FILE_SNIPPET, "uses: line", f"{repo_file.path}:{line_no} -> uses: {match.group(1)}@{ref}")
                ],
            ))

        return findings
