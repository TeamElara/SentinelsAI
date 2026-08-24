"""Fixer for `dependency-*` findings (`agents/repo/dependencies.py`) --
PLAN-v5 Stage F. Bumps a directly-pinned dependency past every vulnerability
OSV.dev currently reports against its *current* pinned version, re-derived
fresh at apply time -- nothing here is trusted from the finding's own
possibly-stale `evidence` text, the same discipline `WorkflowPinFixer`
already applies to a workflow line.

Only handles a pin traceable to one of the three manifest kinds
`agents/repo/dependencies.py` itself parses (`requirements.txt`,
`package.json`, `pyproject.toml`). A finding whose only occurrence is inside
`package-lock.json` -- a resolved, transitive version with no line of its
own -- has no `manifest_parser_for` match and is declined outright: bumping
a transitive dependency safely needs a real `npm`/`poetry` resolve, not a
text edit (PLAN-v5.md Stage F).

Tier 2 (review-required): a version bump can carry breaking API changes a
human needs to weigh, the same reasoning `SecurityHeaderFixer` and
`DockerRootUserFixer` already apply to a generated value.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable

import httpx

from agents.repo.dependencies import (
    Dependency,
    dependency_finding_id,
    manifest_parser_for,
    split_version_prefix,
)
from models import Finding, FixPlan
from remediation.base import Fixer
from remediation.patch import make_patch
from remediation.source import FileSource

_OSV_QUERY_URL = "https://api.osv.dev/v1/query"
_OSV_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"

# Only a plain, fully-numeric dotted version ("1.2.3") is ever treated as
# orderable. A pre-release suffix ("2.0.0rc1"), a non-numeric segment, or
# anything else this comparator can't rank with confidence means declining
# rather than guessing whether it's actually "past" the vulnerable range.
_PLAIN_VERSION_RE = re.compile(r"^\d+(\.\d+)*$")


def _parse_plain_version(v: str) -> tuple[int, ...] | None:
    if not _PLAIN_VERSION_RE.match(v):
        return None
    return tuple(int(part) for part in v.split("."))


async def _current_vuln_ids(client: httpx.AsyncClient, dep: Dependency) -> list[str] | None:
    """Ask OSV.dev fresh whether `dep`'s *current* pinned version is still
    vulnerable -- never the finding's own possibly-stale list. `None` means
    OSV couldn't be reached (decline; mirrors the agent's own
    `_unverified_finding` -- "couldn't check" is never "safe"). `[]` is an
    honest "not vulnerable" -- already fixed since the scan ran.
    """
    try:
        response = await client.post(
            _OSV_QUERY_URL,
            json={"package": {"name": dep.name, "ecosystem": dep.ecosystem}, "version": dep.version},
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    return [v["id"] for v in (data.get("vulns") or []) if "id" in v]


def _fixed_versions(vuln: dict, ecosystem: str, name: str) -> list[str] | None:
    """Every `fixed` version this OSV vulnerability record names for
    (ecosystem, name) specifically. `None` means at least one range affecting
    this exact package never closes (no `fixed` event -- open-ended, or OSV
    only recorded `last_affected`), or the record says nothing about this
    package at all -- either way, the caller must decline rather than guess
    a version past a range that's still open.
    """
    fixed: list[str] = []
    saw_range = False
    for affected in vuln.get("affected") or []:
        package = affected.get("package") or {}
        if package.get("ecosystem") != ecosystem or package.get("name") != name:
            continue
        for rng in affected.get("ranges") or []:
            saw_range = True
            range_fixed = [e["fixed"] for e in (rng.get("events") or []) if "fixed" in e]
            if not range_fixed:
                return None
            fixed.extend(range_fixed)
    return fixed if saw_range else None


async def _target_version(
    client: httpx.AsyncClient, dep: Dependency, vuln_ids: list[str]
) -> str | None:
    """The highest `fixed` version across every vulnerability OSV currently
    reports against `dep` -- one patch clears all of them, not just the one
    the original finding happened to name. `None` on any ambiguity: an
    unreachable vuln record, a still-open range, or a `fixed` value this
    module's plain numeric comparator can't order with confidence.
    """
    best: tuple[int, ...] | None = None
    best_str = ""
    for vuln_id in vuln_ids:
        try:
            response = await client.get(_OSV_VULN_URL.format(id=vuln_id), timeout=15.0)
            response.raise_for_status()
            vuln = response.json()
        except Exception:
            return None

        fixed_versions = _fixed_versions(vuln, dep.ecosystem, dep.name)
        if fixed_versions is None:
            return None

        for raw in fixed_versions:
            parsed = _parse_plain_version(raw)
            if parsed is None:
                return None
            if best is None or parsed > best:
                best, best_str = parsed, raw

    return best_str or None


def _rewrite_requirements_pin(content: str, name: str, old_version: str, new_version: str) -> str | None:
    """Bump one `name==old_version` pin in a requirements.txt-style file,
    touching only the version substring -- indentation, the name's original
    casing, and any trailing `# comment` are left exactly as they were.
    `None` if no line still matches both name and version: the pin moved or
    changed since the scan ran, and guessing which line to touch would be
    exactly the mistake `rewrite_uses_line` already refuses to make.
    """
    pattern = re.compile(
        r"^(?P<indent>[ \t]*)" + re.escape(name) + r"(?P<eq>\s*==\s*)" + re.escape(old_version) + r"(?P<rest>.*)$",
        re.MULTILINE,
    )
    new_content, count = pattern.subn(
        lambda m: f"{m.group('indent')}{name}{m.group('eq')}{new_version}{m.group('rest')}",
        content,
        count=1,
    )
    return new_content if count else None


def _rewrite_package_json_pin(content: str, name: str, old_version: str, new_version: str) -> str | None:
    """Bump one `dependencies`/`devDependencies` entry, preserving whatever
    range prefix ("^", "~", ...) the original value had. Full `json.dumps`
    re-serialization, the same precedent `SecurityHeaderFixer._plan_vercel`
    already sets for a JSON manifest -- `None` on malformed JSON or no
    matching entry.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    for section in ("dependencies", "devDependencies"):
        section_dict = data.get(section)
        if not isinstance(section_dict, dict):
            continue
        raw = section_dict.get(name)
        if not isinstance(raw, str):
            continue
        prefix, bare = split_version_prefix(raw)
        if bare != old_version:
            continue
        section_dict[name] = f"{prefix}{new_version}"
        return json.dumps(data, indent=2) + "\n"

    return None


def _rewrite_pyproject_pin(content: str, name: str, old_version: str, new_version: str) -> str | None:
    """Bump one pin in `pyproject.toml`. Tries PEP 621's quoted
    `"name==old_version"` list item first, then Poetry's bare-string
    `name = "^old_version"` line. Declines (returns `None`) on a Poetry
    inline table (`name = {version = "...", ...}`) -- rewriting a value
    nested inside extra keys and unpredictable formatting is a different,
    harder problem than a single quoted string, and `_parse_pyproject_toml`
    already reads that form for detection only, never for a rewrite target.
    """
    old_pin = f"{name}=={old_version}"
    new_pin = f"{name}=={new_version}"
    for quote in ('"', "'"):
        quoted_old = f"{quote}{old_pin}{quote}"
        if content.count(quoted_old) == 1:
            return content.replace(quoted_old, f"{quote}{new_pin}{quote}", 1)

    pattern = re.compile(
        r'^(?P<indent>[ \t]*)' + re.escape(name) + r'(?P<eq>\s*=\s*)"'
        r'(?P<prefix>[\^~<>=]*)' + re.escape(old_version) + r'"(?P<rest>.*)$',
        re.MULTILINE,
    )
    new_content, count = pattern.subn(
        lambda m: f'{m.group("indent")}{name}{m.group("eq")}"{m.group("prefix")}{new_version}"{m.group("rest")}',
        content,
        count=1,
    )
    return new_content if count else None


_REWRITERS: dict[str, Callable[[str, str, str, str], str | None]] = {
    "requirements.txt": _rewrite_requirements_pin,
    "package.json": _rewrite_package_json_pin,
    "pyproject.toml": _rewrite_pyproject_pin,
}


class DependencyVersionFixer(Fixer):
    slug = "dependency-version"
    display_name = "Upgrade past the known vulnerability"

    def handles(self, finding: Finding) -> bool:
        # `dependency-osv-unreachable` (the agent's own "couldn't check"
        # finding) shares the prefix but never carries a file_path -- this
        # check excludes it without hardcoding its exact id.
        return finding.id.startswith("dependency-") and finding.file_path is not None

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        if not finding.file_path:
            return None

        basename = finding.file_path.rsplit("/", 1)[-1]
        parser = manifest_parser_for(finding.file_path)
        rewriter = _REWRITERS.get(basename)
        if parser is None or rewriter is None:
            return None  # package-lock.json, or a manifest kind this Fixer can't safely rewrite

        source = await files.get(finding.file_path)
        if source is None:
            return None  # the manifest is gone since the scan ran

        deps = parser(source.content, finding.file_path)
        dep = next((d for d in deps if dependency_finding_id(d) == finding.id), None)
        if dep is None:
            return None  # no longer pinned there since the scan ran

        vuln_ids = await _current_vuln_ids(files.client, dep)
        if not vuln_ids:
            return None  # OSV unreachable, or the pin is already clean -- either way, nothing to plan

        target = await _target_version(files.client, dep, vuln_ids)
        if target is None:
            return None  # no confidently-resolvable fixed version -- decline rather than guess

        new_content = rewriter(source.content, dep.name, dep.version, target)
        if new_content is None or new_content == source.content:
            return None

        patch = make_patch(finding.file_path, "modify", source, new_content)
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=2,
            summary=f"Upgrade {dep.name} from {dep.version} to {target} in {finding.file_path}.",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
