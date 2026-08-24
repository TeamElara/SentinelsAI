"""The repo Dependencies agent -- parses a repo's manifest files for pinned
package versions and batch-checks them against OSV.dev, a free public
vulnerability database (https://osv.dev). Reading a public database is a GET
of public data, same "passive" bar as every other agent in this codebase --
nothing here installs, imports, or runs a single line of the repo's
dependencies (CLAUDE.md's repo-side non-negotiable).

Follows `ai/client.py`'s graceful-degradation contract: OSV unreachable is
not an agent crash. `BaseRepoAgent.run()` already catches genuine bugs, but
"the network is down" is an *expected* condition here, so it's handled
explicitly -- the scan still completes, with the found dependencies reported
as unverified instead of silently vanishing.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from typing import Callable

import httpx

from agents.repo.base import BaseRepoAgent, RepoContext, RepoFile
from models import EvidenceKind, Finding, Severity, Status

OWASP_VULNERABLE_COMPONENTS = "A06:2021 - Vulnerable and Outdated Components"

_OSV_URL = "https://api.osv.dev/v1/querybatch"
_MAX_QUERIES = 500   # safety valve for a repo with an enormous lockfile
_MAX_FINDINGS = 100


@dataclass
class Dependency:
    name: str
    version: str
    ecosystem: str        # OSV's vocabulary: "PyPI" | "npm"
    source_file: str


_REQ_LINE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*==\s*([A-Za-z0-9_.+\-]+)")
_PEP508_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*==\s*([A-Za-z0-9_.+\-]+)")
_SIMPLE_VERSION_RE = re.compile(r"^\d[\w.\-+]*$")

# Checked longest-prefix-first where it matters (">=" before ">", "<=" before
# "<") so an ambiguous range like ">=1.2.3" doesn't get misread as "> =1.2.3".
_VERSION_PREFIXES = ("^", "~", ">=", "<=", ">", "<", "=")


def split_version_prefix(raw: str) -> tuple[str, str]:
    """Split a semver-range string into its prefix (if any) and the bare
    value that follows -- `("^", "1.2.3")` for `"^1.2.3"`, `("", "1.2.3")`
    for a bare pin. Shared with `remediation/dependencies.py` (PLAN-v5 Stage
    F), which needs the prefix preserved exactly when bumping a pin -- losing
    a "^" while rewriting the version underneath it would silently change
    what the range means, not just which version it starts from.
    """
    v = raw.strip()
    for prefix in _VERSION_PREFIXES:
        if v.startswith(prefix):
            return prefix, v[len(prefix):].strip()
    return "", v


def _resolve_pinned_version(raw: str) -> str | None:
    """Strip a semver-range prefix (^1.2.3, ~1.2.3, >=1.2.3) down to a bare
    version. Returns None for anything that isn't a single resolvable version
    -- a range like "1.0 || 2.0", a git/file/workspace URL, "*" -- since OSV
    needs one exact version to check, not a range.
    """
    _, v = split_version_prefix(raw)
    if not v or " " in v or "||" in v or "*" in v:
        return None
    if v.startswith(("git", "file:", "workspace:", "link:", "http")):
        return None
    return v if _SIMPLE_VERSION_RE.match(v) else None


def _parse_requirements_txt(text: str, source_file: str) -> list[Dependency]:
    deps = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQ_LINE_RE.match(line)
        if not match:
            continue  # unpinned (>=, ~=) or extras ([foo]) -- can't resolve one version
        deps.append(Dependency(match.group(1), match.group(2), "PyPI", source_file))
    return deps


def _parse_package_json(text: str, source_file: str) -> list[Dependency]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    deps = []
    for section in ("dependencies", "devDependencies"):
        for name, raw_version in (data.get(section) or {}).items():
            if not isinstance(raw_version, str):
                continue
            version = _resolve_pinned_version(raw_version)
            if version:
                deps.append(Dependency(name, version, "npm", source_file))
    return deps


def _parse_package_lock_json(text: str, source_file: str) -> list[Dependency]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    deps: list[Dependency] = []

    packages = data.get("packages")
    if isinstance(packages, dict):
        # npm lockfile v2/v3 shape: {"node_modules/foo": {"version": "1.2.3"}, ...}
        for pkg_path, info in packages.items():
            if not pkg_path or not isinstance(info, dict):
                continue
            version = info.get("version")
            if not version:
                continue
            name = pkg_path.rsplit("node_modules/", 1)[-1]
            deps.append(Dependency(name, version, "npm", source_file))
        return deps

    # v1 fallback: nested {"dependencies": {"foo": {"version": ..., "dependencies": {...}}}}
    def _walk(node: dict | None) -> None:
        for name, info in (node or {}).items():
            if not isinstance(info, dict):
                continue
            version = info.get("version")
            if version:
                deps.append(Dependency(name, version, "npm", source_file))
            _walk(info.get("dependencies"))

    _walk(data.get("dependencies"))
    return deps


def _parse_pyproject_toml(text: str, source_file: str) -> list[Dependency]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    deps = []

    # PEP 621: project.dependencies = ["requests==2.6.0", "flask>=2.0", ...]
    for raw in data.get("project", {}).get("dependencies") or []:
        match = _PEP508_PIN_RE.match(raw)
        if match:
            deps.append(Dependency(match.group(1), match.group(2), "PyPI", source_file))

    # Poetry: [tool.poetry.dependencies] name = "^1.2.3" | name = {version = "^1.2.3"}
    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies") or {}
    for name, spec in poetry_deps.items():
        if name.lower() == "python":
            continue
        raw_version = spec if isinstance(spec, str) else (spec or {}).get("version")
        if not isinstance(raw_version, str):
            continue
        version = _resolve_pinned_version(raw_version)
        if version:
            deps.append(Dependency(name, version, "PyPI", source_file))

    return deps


_MANIFEST_PARSERS = {
    "requirements.txt": _parse_requirements_txt,
    "package.json": _parse_package_json,
    "pyproject.toml": _parse_pyproject_toml,
}


def manifest_parser_for(path: str) -> Callable[[str, str], list[Dependency]] | None:
    """The parser for `path`'s manifest kind, or `None` if it isn't one of
    the direct-pin manifests this module knows how to read -- notably
    `package-lock.json`, whose entries are resolved, transitive versions
    with no line of their own to point at. Shared with
    `remediation/dependencies.py` (PLAN-v5 Stage F) so the Fixer's notion of
    "a manifest I can safely touch" can never drift from the agent's own:
    a manifest this returns `None` for was never a *direct* pin to begin
    with, and bumping it is a job for a real package-manager resolve, not a
    text edit.
    """
    return _MANIFEST_PARSERS.get(path.rsplit("/", 1)[-1])


def dependency_finding_id(dep: Dependency) -> str:
    """The exact `Finding.id` a vulnerable `dep` produces. Shared with
    `remediation/dependencies.py` so a Fixer can find the same entry in a
    freshly re-parsed manifest by id, rather than duplicating this slug
    scheme somewhere else and risking the two drifting apart.
    """
    file_slug = dep.source_file.replace("/", "-")
    name_slug = dep.name.replace("/", "-").lower()
    version_slug = dep.version.replace("/", "-")
    return f"dependency-{dep.ecosystem.lower()}-{name_slug}-{version_slug}-{file_slug}"


class DependenciesAgent(BaseRepoAgent):
    name = "repo-dependencies"
    display_name = "Dependencies"
    purpose = "Checks manifest-pinned dependency versions against the OSV.dev vulnerability database."
    checks = [
        "requirements.txt / pyproject.toml -- known-vulnerable PyPI package versions",
        "package.json / package-lock.json -- known-vulnerable npm package versions",
    ]
    category = "Dependencies"

    async def scan(self, context: RepoContext) -> list[Finding]:
        deps = self._collect_dependencies(context)[:_MAX_QUERIES]
        if not deps:
            return []

        if context.client is None:
            return [self._unverified_finding(deps)]

        vuln_map = await self._query_osv(context.client, deps)
        if vuln_map is None:
            return [self._unverified_finding(deps)]

        findings = [
            self._vuln_finding(dep, vuln_map[key])
            for dep in deps
            if (key := (dep.ecosystem, dep.name, dep.version)) in vuln_map
        ]
        return findings[:_MAX_FINDINGS]

    def _collect_dependencies(self, context: RepoContext) -> list[Dependency]:
        deps: list[Dependency] = []
        seen: set[tuple[str, str]] = set()

        # Lockfiles first: a resolved, exact version beats a manifest's range
        # for the *same* package name, so later manifest hits on a name
        # already seen here are skipped rather than adding a second,
        # less-precise entry for it.
        for repo_file in context.files:
            if repo_file.path.rsplit("/", 1)[-1] != "package-lock.json":
                continue
            text = self._read(repo_file)
            if text is None:
                continue
            for dep in _parse_package_lock_json(text, repo_file.path):
                key = (dep.ecosystem, dep.name)
                if key not in seen:
                    seen.add(key)
                    deps.append(dep)

        for repo_file in context.files:
            basename = repo_file.path.rsplit("/", 1)[-1]
            parser = _MANIFEST_PARSERS.get(basename)
            if parser is None:
                continue
            text = self._read(repo_file)
            if text is None:
                continue
            for dep in parser(text, repo_file.path):
                key = (dep.ecosystem, dep.name)
                if key in seen:
                    continue
                seen.add(key)
                deps.append(dep)

        return deps

    @staticmethod
    def _read(repo_file: RepoFile) -> str | None:
        try:
            return repo_file.abs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    async def _query_osv(
        self, client: httpx.AsyncClient, deps: list[Dependency]
    ) -> dict[tuple[str, str, str], list[str]] | None:
        """Returns None on any failure (offline, rate-limited, bad response)
        -- the caller treats that as "couldn't verify", never a crash."""
        queries = [
            {"package": {"name": dep.name, "ecosystem": dep.ecosystem}, "version": dep.version}
            for dep in deps
        ]
        try:
            response = await client.post(_OSV_URL, json={"queries": queries}, timeout=15.0)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None

        vuln_map: dict[tuple[str, str, str], list[str]] = {}
        for dep, result in zip(deps, data.get("results", [])):
            ids = [v["id"] for v in (result.get("vulns") or []) if "id" in v]
            if ids:
                vuln_map[(dep.ecosystem, dep.name, dep.version)] = ids
        return vuln_map

    def _vuln_finding(self, dep: Dependency, vuln_ids: list[str]) -> Finding:
        ids_text = ", ".join(vuln_ids[:5])
        evidence_text = f"{dep.ecosystem} {dep.name}@{dep.version} ({dep.source_file}) -- {ids_text}"
        return Finding(
            id=dependency_finding_id(dep),
            title=f"Known-vulnerable dependency: {dep.name}@{dep.version}",
            category="Dependencies",
            severity=Severity.HIGH,
            status=Status.FAIL,
            owasp=OWASP_VULNERABLE_COMPONENTS,
            file_path=dep.source_file,
            evidence=evidence_text,
            description=(
                f"{dep.name} version {dep.version}, pinned in {dep.source_file}, "
                f"has {len(vuln_ids)} known vulnerability record(s) in the "
                f"OSV.dev public database: {ids_text}."
            ),
            remediation=(
                f"Upgrade {dep.name} past the version(s) affected by "
                f"{ids_text}, then re-check with `pip-audit`/`npm audit` "
                "or https://osv.dev directly."
            ),
            evidence_items=[
                self.evidence(EvidenceKind.DEPENDENCY, f"{dep.name}@{dep.version}", evidence_text)
            ],
        )

    def _unverified_finding(self, deps: list[Dependency]) -> Finding:
        evidence_text = (
            f"{len(deps)} pinned dependency version(s) found, but OSV.dev "
            "could not be reached to check them."
        )
        return Finding(
            id="dependency-osv-unreachable",
            title="Dependency versions could not be checked against OSV.dev",
            category="Dependencies",
            severity=Severity.INFO,
            status=Status.WARN,
            owasp=OWASP_VULNERABLE_COMPONENTS,
            evidence=evidence_text,
            description=(
                "OSV.dev, the public vulnerability database this check "
                "queries, was unreachable during this scan. The dependency "
                "versions found were not verified against known CVEs -- "
                "re-run the scan later to check them."
            ),
        )
