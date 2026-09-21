"""Four creates-only fixers for agents/repo/hygiene.py findings that have no
`file_path` at all -- they're "is X present anywhere" checks, not
line-level ones.

`ReadmeFixer` (`repo-readme-present`) -- adds a starter `README.md` when
none of hygiene.py's own recognized README names exist.

`EnvExampleFixer` (`repo-env-example-present`) -- adds `.env.example` with
every key from a committed `.env`, values blanked. Deliberately refuses to
plan anything when no `.env` exists to read keys from: inventing plausible
env-var names would be a guess dressed up as a fact, exactly what
CLAUDE.md's confidence rule forbids. (A committed `.env` is *also* a
`secret-env-committed-*` finding, fixed separately by `remediation/secrets.py`
-- so a repo that trips this path already has a separate warning about the
file this fixer is reading from.)

`LicenseFixer` (`repo-license-present`) -- adds a `LICENSE` file, but *only*
when the repo's own manifest (`package.json`'s `"license"`, or
`pyproject.toml`'s PEP 621 `project.license` / Poetry's `tool.poetry.license`)
already names an SPDX id this fixer has canonical text for. Choosing a
license is a legal decision that belongs to the repo's maintainer, not to
Sentinels -- PLAN-v5.md says so explicitly ("`LICENSE` is deliberately not
auto-generated"). This fixer never makes that choice; it only ever
materializes a choice already on record elsewhere in the repo, which is why
it's tier 1 rather than tier 2 despite writing a whole new file.

`CiScaffoldFixer` (`repo-ci-configured`) -- adds a minimal
`.github/workflows/ci.yml`, reusing `remediation/stack.py`'s existing (and
deliberately narrow) stack detection rather than building a second one.
Declines for anything that isn't a recognized Vercel/Next.js-shaped npm
project, the same "no recognized stack -> decline rather than guess"
precedent `headers_fix.py` already sets -- and even then, only wires in the
`lint`/`test`/`build` npm scripts the repo's own `package.json` already
defines, never an invented one.
"""
from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone

from models import Finding, FixPlan
from remediation.base import Fixer
from remediation.patch import make_patch
from remediation.source import FileSource, SourceFile
from remediation.stack import detect_stack

_README_CANDIDATES = ["README.md", "readme.md", "README", "README.rst", "README.txt"]

_ENV_KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _extract_env_keys(text: str) -> list[str]:
    """Pure line parse: `KEY=value` -> `"KEY"`, skipping blank lines,
    comments, and anything that doesn't look like an assignment. Order is
    preserved and duplicates are dropped."""
    keys: dict[str, None] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_KEY_RE.match(line)
        if match:
            keys.setdefault(match.group(1), None)
    return list(keys)


class ReadmeFixer(Fixer):
    slug = "repo-readme-present"
    display_name = "Add a starter README"

    def handles(self, finding: Finding) -> bool:
        return finding.id == "repo-readme-present"

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        for name in _README_CANDIDATES:
            if await files.get(name) is not None:
                return None  # a README already exists under some recognized name

        content = (
            f"# {files.repo}\n\n"
            "_This README was scaffolded by Sentinels -- replace this with a real "
            "description of what the project does and how to run it._\n"
        )
        patch = make_patch("README.md", "create", None, content)
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=1,
            summary="Add a starter README.md.",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )


class EnvExampleFixer(Fixer):
    slug = "repo-env-example-present"
    display_name = "Add a .env.example"

    def handles(self, finding: Finding) -> bool:
        return finding.id == "repo-env-example-present"

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        env_file = await files.get(".env")
        if env_file is None:
            return None  # nothing to derive variable names from -- refuse to guess

        keys = _extract_env_keys(env_file.content)
        if not keys:
            return None

        content = "\n".join(f"{key}=" for key in keys) + "\n"
        patch = make_patch(".env.example", "create", None, content)
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=1,
            summary=f"Add .env.example listing {len(keys)} variable name(s), values blanked.",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )


# ---- LicenseFixer -----------------------------------------------------------

_LICENSE_CANDIDATES = [
    "LICENSE", "LICENSE.md", "LICENSE.txt",
    "COPYING", "COPYING.md",
    "license", "license.md", "license.txt",
    "copying", "copying.md",
]

# Only SPDX ids this fixer has verbatim canonical text for. Anything else
# declines (`_license_text` returns None) -- a license with the wrong words
# in it is worse than no license file at all, and paraphrasing a legal
# document is exactly the kind of "confident-sounding guess" CLAUDE.md's
# confidence rule forbids. Deliberately short: these four are the shortest,
# least ambiguous OSI templates, the ones this fixer can reproduce exactly.
_SPDX_ALIASES: dict[str, str] = {
    "mit": "MIT",
    "isc": "ISC",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
}


def _mit_license(holder: str, year: int) -> str:
    return (
        f"MIT License\n\n"
        f"Copyright (c) {year} {holder}\n\n"
        'Permission is hereby granted, free of charge, to any person obtaining a copy\n'
        'of this software and associated documentation files (the "Software"), to deal\n'
        "in the Software without restriction, including without limitation the rights\n"
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
        "copies of the Software, and to permit persons to whom the Software is\n"
        "furnished to do so, subject to the following conditions:\n\n"
        "The above copyright notice and this permission notice shall be included in all\n"
        "copies or substantial portions of the Software.\n\n"
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
        "SOFTWARE.\n"
    )


def _isc_license(holder: str, year: int) -> str:
    return (
        f"ISC License\n\n"
        f"Copyright (c) {year} {holder}\n\n"
        "Permission to use, copy, modify, and/or distribute this software for any\n"
        "purpose with or without fee is hereby granted, provided that the above\n"
        "copyright notice and this permission notice appear in all copies.\n\n"
        'THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH\n'
        "REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY\n"
        "AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,\n"
        "INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM\n"
        "LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR\n"
        "OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR\n"
        "PERFORMANCE OF THIS SOFTWARE.\n"
    )


def _bsd_license(holder: str, year: int, *, clauses: int) -> str:
    header = f"BSD {clauses}-Clause License\n\nCopyright (c) {year}, {holder}\n"
    body = (
        "\nRedistribution and use in source and binary forms, with or without\n"
        "modification, are permitted provided that the following conditions are met:\n\n"
        "1. Redistributions of source code must retain the above copyright notice, this\n"
        "   list of conditions and the following disclaimer.\n\n"
        "2. Redistributions in binary form must reproduce the above copyright notice,\n"
        "   this list of conditions and the following disclaimer in the documentation\n"
        "   and/or other materials provided with the distribution.\n"
    )
    if clauses == 3:
        body += (
            "\n3. Neither the name of the copyright holder nor the names of its\n"
            "   contributors may be used to endorse or promote products derived from\n"
            "   this software without specific prior written permission.\n"
        )
    disclaimer = (
        '\nTHIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"\n'
        "AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE\n"
        "IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE\n"
        "DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE\n"
        "FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL\n"
        "DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR\n"
        "SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER\n"
        "CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,\n"
        "OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE\n"
        "OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.\n"
    )
    return header + body + disclaimer


_LICENSE_TEMPLATES = {
    "MIT": _mit_license,
    "ISC": _isc_license,
    "BSD-2-Clause": lambda holder, year: _bsd_license(holder, year, clauses=2),
    "BSD-3-Clause": lambda holder, year: _bsd_license(holder, year, clauses=3),
}


def _license_text(spdx_id: str, holder: str, year: int) -> str | None:
    normalized = _SPDX_ALIASES.get(spdx_id.strip().lower())
    if normalized is None:
        return None
    return _LICENSE_TEMPLATES[normalized](holder, year)


def _declared_spdx_id(package_json: SourceFile | None, pyproject: SourceFile | None) -> str | None:
    """The SPDX id the repo's own manifest already declares, or `None` if
    neither manifest exists, is unparseable, or names something other than
    a plain string (a `{file: ...}`/`{text: ...}` PEP 621 table points at
    content this function can't turn into an id without guessing)."""
    if package_json is not None:
        try:
            data = json.loads(package_json.content)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            license_field = data.get("license")
            if isinstance(license_field, str) and license_field.strip():
                return license_field.strip()

    if pyproject is not None:
        try:
            data = tomllib.loads(pyproject.content)
        except tomllib.TOMLDecodeError:
            data = None
        if isinstance(data, dict):
            project = data.get("project")
            if isinstance(project, dict) and isinstance(project.get("license"), str):
                if project["license"].strip():
                    return project["license"].strip()
            tool = data.get("tool")
            if isinstance(tool, dict):
                poetry = tool.get("poetry")
                if isinstance(poetry, dict) and isinstance(poetry.get("license"), str):
                    if poetry["license"].strip():
                        return poetry["license"].strip()

    return None


class LicenseFixer(Fixer):
    slug = "repo-license-present"
    display_name = "Add a LICENSE file"

    def handles(self, finding: Finding) -> bool:
        return finding.id == "repo-license-present"

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        for name in _LICENSE_CANDIDATES:
            if await files.get(name) is not None:
                return None  # a LICENSE already exists under some recognized name

        package_json = await files.get("package.json")
        pyproject = await files.get("pyproject.toml")
        spdx_id = _declared_spdx_id(package_json, pyproject)
        if spdx_id is None:
            return None  # no declared license anywhere -- never choose one ourselves

        content = _license_text(spdx_id, files.owner, datetime.now(timezone.utc).year)
        if content is None:
            return None  # a declared id with no canonical text on file -- decline rather than guess

        patch = make_patch("LICENSE", "create", None, content)
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=1,
            summary=f"Add a LICENSE file for the already-declared {spdx_id} license.",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )


# ---- CiScaffoldFixer ---------------------------------------------------------

_CI_WORKFLOW_PATH = ".github/workflows/ci.yml"

# Checked, and wired into the generated workflow, in this order -- only the
# scripts that actually exist in the repo's own package.json are included,
# never an invented one (the same "refuse to guess" discipline
# `EnvExampleFixer` already applies to variable names).
_SCAFFOLD_SCRIPTS = ["lint", "test", "build"]


def _ci_workflow_yaml(scripts: list[str]) -> str:
    steps = [
        "      - uses: actions/checkout@v4",
        "      - uses: actions/setup-node@v4",
        '        with:\n          node-version: "20"',
        "      - run: npm ci",
    ]
    steps.extend(f"      - run: npm run {script}" for script in scripts)
    steps_block = "\n".join(steps)
    return (
        "name: CI\n"
        "\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "  pull_request:\n"
        "\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"{steps_block}\n"
    )


class CiScaffoldFixer(Fixer):
    slug = "repo-ci-configured"
    display_name = "Add a starter CI workflow"

    def handles(self, finding: Finding) -> bool:
        return finding.id == "repo-ci-configured"

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        if await files.get(_CI_WORKFLOW_PATH) is not None:
            return None  # already fixed since the scan ran

        stack = await detect_stack(files)
        if stack is None:
            return None  # no recognized stack -- decline rather than guess

        package_json = await files.get("package.json")
        if package_json is None:
            return None  # no manifest to read scripts from -- refuse to guess
        try:
            data = json.loads(package_json.content)
        except json.JSONDecodeError:
            return None

        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            return None
        wired = [
            name for name in _SCAFFOLD_SCRIPTS
            if isinstance(scripts.get(name), str) and scripts[name].strip()
        ]
        if not wired:
            return None  # nothing real to run -- a bare `npm ci` isn't a CI check

        content = _ci_workflow_yaml(wired)
        patch = make_patch(_CI_WORKFLOW_PATH, "create", None, content)
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=1,
            summary=f"Add a starter CI workflow running: {', '.join(wired)}.",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
