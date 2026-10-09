"""The repo Hygiene agent -- checks for basic project-hygiene signals: is
there a README/LICENSE, is the lockfile committed alongside the manifest,
are there any tests, is CI configured, is an .env example provided, and are
there any suspiciously large files sitting in the repo.

R2 shipped this class with just the README/LICENSE pair, deliberately, as
"one trivial agent to prove the repo-agent wiring end to end" with a real,
useful check rather than a no-op placeholder. R8 (Phase R-B) extends that
same class with the rest of PLAN-v3's hygiene checks instead of creating a
second file -- these are all "does this repo have X" checks of the same
shape, just more of them.
"""
from __future__ import annotations
from scan_coverage import record

from agents.repo.base import BaseRepoAgent, RepoContext, RepoFile
from models import Finding, Severity, Status

_README_NAMES = {"readme", "readme.md", "readme.rst", "readme.txt"}
_LICENSE_NAMES = {"license", "license.md", "license.txt", "copying", "copying.md"}
_ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", ".env.dist"}

_NPM_MANIFEST_NAMES = {"package.json"}
_NPM_LOCKFILE_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
_PY_MANIFEST_NAMES = {"pyproject.toml", "pipfile"}
_PY_LOCKFILE_NAMES = {"poetry.lock", "pipfile.lock"}

_CI_CONFIG_PATHS = {".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml", "jenkinsfile", ".travis.yml"}

_TEST_PATH_MARKERS = ("/test/", "/tests/", "/__tests__/")
_TEST_NAME_PREFIXES = ("test_",)
_TEST_NAME_SUFFIXES = ("_test.py", ".test.js", ".test.ts", ".test.jsx", ".test.tsx", ".spec.js", ".spec.ts")

_LARGE_FILE_THRESHOLD_BYTES = 1_000_000  # 1 MB -- well under fetch.py's 5 MB hard per-file cap
_MAX_LARGE_FILE_FINDINGS = 20


def _root_file_names(context: RepoContext) -> set[str]:
    """Lowercased names of files that sit directly in the repo root
    (no "/" in their relative path)."""
    return {f.path.lower() for f in context.files if "/" not in f.path}


def _all_file_names(context: RepoContext) -> set[str]:
    """Lowercased basenames of every file anywhere in the repo tree."""
    return {f.path.rsplit("/", 1)[-1].lower() for f in context.files}


class HygieneAgent(BaseRepoAgent):
    name = "repo-hygiene"
    display_name = "Repo Hygiene"
    purpose = "Checks for basic project-hygiene signals a public repo should have."
    checks = [
        "README -- is there a README file at the repo root?",
        "LICENSE -- is there a LICENSE file at the repo root?",
        "Lockfile -- if a package manifest exists, is its lockfile committed too?",
        "Tests -- are there any test files in the repo?",
        "CI -- is a CI pipeline configured?",
        ".env.example -- is a template for required environment variables provided?",
        "Large files -- are there any unusually large files committed?",
    ]
    category = "Hygiene"

    async def scan(self, context: RepoContext) -> list[Finding]:
        root_names = _root_file_names(context)
        all_names = _all_file_names(context)

        findings = [
            self._check_readme(root_names),
            self._check_license(root_names),
            self._check_tests(context),
            self._check_ci(context, all_names),
            self._check_env_example(all_names),
        ]
        findings.extend(self._check_lockfiles(all_names))
        findings.extend(self._check_large_files(context))
        return findings

    def _check_readme(self, root_names: set[str]) -> Finding:
        if root_names & _README_NAMES:
            return Finding(
                id="repo-readme-present",
                title="README file present",
                category="Hygiene",
                severity=Severity.INFO,
                status=Status.PASS,
                evidence="A README file exists at the repo root.",
            )
        return Finding(
            id="repo-readme-present",
            title="No README file found",
            category="Hygiene",
            severity=Severity.LOW,
            status=Status.WARN,
            evidence="No README.md/README/README.rst/README.txt found at the repo root.",
            description=(
                "A README is the first thing anyone -- a collaborator, a "
                "future you, or someone auditing this repo -- reads to "
                "understand what the project does and how to run it."
            ),
            remediation="Add a README.md describing what the project is and how to set it up.",
        )

    def _check_license(self, root_names: set[str]) -> Finding:
        if root_names & _LICENSE_NAMES:
            return Finding(
                id="repo-license-present",
                title="LICENSE file present",
                category="Hygiene",
                severity=Severity.INFO,
                status=Status.PASS,
                evidence="A LICENSE file exists at the repo root.",
            )
        return Finding(
            id="repo-license-present",
            title="No LICENSE file found",
            category="Hygiene",
            severity=Severity.LOW,
            status=Status.WARN,
            evidence="No LICENSE/LICENSE.md/LICENSE.txt/COPYING found at the repo root.",
            description=(
                "Without a LICENSE file, the legal default in most "
                "jurisdictions is 'all rights reserved' -- other people "
                "can't safely use, modify, or redistribute this code even "
                "if that's not what was intended."
            ),
            remediation="Add a LICENSE file (e.g. MIT, Apache-2.0) stating how the code may be used.",
        )

    def _check_lockfiles(self, all_names: set[str]) -> list[Finding]:
        findings = []
        if all_names & _NPM_MANIFEST_NAMES:
            has_lockfile = bool(all_names & _NPM_LOCKFILE_NAMES)
            findings.append(self._lockfile_finding("repo-npm-lockfile-present", "npm", has_lockfile))
        if all_names & _PY_MANIFEST_NAMES:
            has_lockfile = bool(all_names & _PY_LOCKFILE_NAMES)
            findings.append(self._lockfile_finding("repo-python-lockfile-present", "Python", has_lockfile))
        return findings

    def _lockfile_finding(self, check_id: str, ecosystem: str, has_lockfile: bool) -> Finding:
        if has_lockfile:
            return Finding(
                id=check_id,
                title=f"{ecosystem} lockfile is committed",
                category="Hygiene",
                severity=Severity.INFO,
                status=Status.PASS,
                evidence=f"A {ecosystem} lockfile exists alongside its manifest.",
            )
        return Finding(
            id=check_id,
            title=f"{ecosystem} lockfile is missing",
            category="Hygiene",
            severity=Severity.LOW,
            status=Status.WARN,
            evidence=f"A {ecosystem} manifest exists, but no matching lockfile was found.",
            description=(
                "Without a committed lockfile, different installs of this "
                "project can silently resolve different dependency "
                "versions -- including a version with a vulnerability that "
                "wasn't present when the code was last tested."
            ),
            remediation=f"Commit the {ecosystem} lockfile so every install uses the exact same dependency versions.",
        )

    def _check_tests(self, context: RepoContext) -> Finding:
        has_tests = any(self._looks_like_test_file(f) for f in context.files)
        if has_tests:
            return Finding(
                id="repo-tests-present",
                title="Test files found",
                category="Hygiene",
                severity=Severity.INFO,
                status=Status.PASS,
                evidence="At least one file matching a common test naming convention was found.",
            )
        return Finding(
            id="repo-tests-present",
            title="No test files found",
            category="Hygiene",
            severity=Severity.LOW,
            status=Status.WARN,
            evidence="No file matched a common test naming convention (test_*.py, *.spec.js, a tests/ directory, ...).",
            description="Without tests, there's nothing catching a regression before it ships.",
            remediation="Add tests, even a small starting set, under a tests/ directory.",
        )

    @staticmethod
    def _looks_like_test_file(repo_file: RepoFile) -> bool:
        path = "/" + repo_file.path.lower()
        if any(marker in path for marker in _TEST_PATH_MARKERS):
            return True
        basename = repo_file.path.rsplit("/", 1)[-1].lower()
        return basename.startswith(_TEST_NAME_PREFIXES) or basename.endswith(_TEST_NAME_SUFFIXES)

    def _check_ci(self, context: RepoContext, all_names: set[str]) -> Finding:
        has_workflow = any(
            f.path.startswith(".github/workflows/") and f.path.rsplit("/", 1)[-1].rsplit(".", 1)[-1] in ("yml", "yaml")
            for f in context.files
        )
        has_other_ci = bool(all_names & _CI_CONFIG_PATHS)
        if has_workflow or has_other_ci:
            return Finding(
                id="repo-ci-configured",
                title="CI is configured",
                category="Hygiene",
                severity=Severity.INFO,
                status=Status.PASS,
                evidence="A CI configuration file was found (GitHub Actions, GitLab CI, CircleCI, etc.).",
            )
        return Finding(
            id="repo-ci-configured",
            title="No CI configuration found",
            category="Hygiene",
            severity=Severity.LOW,
            status=Status.WARN,
            evidence="No .github/workflows/, .gitlab-ci.yml, or other common CI config found.",
            description="Without CI, tests (if any exist) only run when someone remembers to run them locally.",
            remediation="Add a CI workflow that runs the test suite on every push/pull request.",
        )

    def _check_env_example(self, all_names: set[str]) -> Finding:
        if all_names & _ENV_EXAMPLE_NAMES:
            return Finding(
                id="repo-env-example-present",
                title=".env example/template provided",
                category="Hygiene",
                severity=Severity.INFO,
                status=Status.PASS,
                evidence="A .env.example/.env.sample/.env.template/.env.dist file was found.",
            )
        return Finding(
            id="repo-env-example-present",
            title="No .env example/template found",
            category="Hygiene",
            severity=Severity.LOW,
            status=Status.WARN,
            evidence="No .env.example/.env.sample/.env.template/.env.dist file was found.",
            description=(
                "Without a template, anyone setting this project up has to "
                "guess which environment variables it needs -- or dig them "
                "out of the source code."
            ),
            remediation="Commit a .env.example listing every required environment variable name, with placeholder values.",
        )

    def _check_large_files(self, context: RepoContext) -> list[Finding]:
        findings = []
        for repo_file in context.files:
            if repo_file.size <= _LARGE_FILE_THRESHOLD_BYTES:
                continue
            if len(findings) >= _MAX_LARGE_FILE_FINDINGS:
                record("Large file findings limit", "partial", "Additional large-file findings were truncated.")
                break
            file_slug = repo_file.path.replace("/", "-")
            size_mb = repo_file.size / 1_000_000
            findings.append(Finding(
                id=f"repo-large-file-{file_slug}",
                title=f"Large file committed: {repo_file.path} ({size_mb:.1f} MB)",
                category="Hygiene",
                severity=Severity.LOW,
                status=Status.WARN,
                file_path=repo_file.path,
                evidence=f"{repo_file.path} is {size_mb:.1f} MB.",
                description=(
                    "Large files bloat every future clone of this repo "
                    "forever, even after the file is deleted -- git keeps "
                    "every version in history."
                ),
                remediation="Move large assets/data out of the repo (e.g. Git LFS or external storage), and consider purging it from history.",
            ))
        return findings
