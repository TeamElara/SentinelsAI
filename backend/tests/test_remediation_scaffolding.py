"""Tests for remediation/scaffolding.py -- ReadmeFixer, EnvExampleFixer,
LicenseFixer, and CiScaffoldFixer."""
from __future__ import annotations

import base64
import json

from models import Finding, Severity, Status
from remediation.scaffolding import (
    CiScaffoldFixer,
    EnvExampleFixer,
    LicenseFixer,
    ReadmeFixer,
    _declared_spdx_id,
    _extract_env_keys,
    _license_text,
)
from remediation.source import FileSource, SourceFile


def _contents_response(sha: str, content: str) -> tuple[int, dict, str]:
    body = json.dumps({
        "sha": sha,
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    })
    return (200, {"content-type": "application/json"}, body)


def _readme_finding() -> Finding:
    return Finding(id="repo-readme-present", title="No README", category="Hygiene", severity=Severity.LOW, status=Status.WARN)


def _env_finding() -> Finding:
    return Finding(id="repo-env-example-present", title="No .env.example", category="Hygiene", severity=Severity.LOW, status=Status.WARN)


# --- _extract_env_keys: pure, offline --------------------------------------


def test_extract_env_keys_skips_blank_lines_and_comments():
    text = "\n# a comment\nFOO=bar\nBAZ=\nexport QUX=1\nnot a line\n"
    assert _extract_env_keys(text) == ["FOO", "BAZ", "QUX"]


def test_extract_env_keys_deduplicates_preserving_order():
    text = "A=1\nB=2\nA=3\n"
    assert _extract_env_keys(text) == ["A", "B"]


# --- ReadmeFixer -------------------------------------------------------------


def test_readme_handles_only_repo_readme_present():
    fixer = ReadmeFixer()
    assert fixer.handles(_readme_finding())
    assert not fixer.handles(_env_finding())


async def test_readme_plan_creates_when_no_readme_variant_exists(mock_site):
    routes: dict = {}  # every candidate name 404s
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await ReadmeFixer().plan(_readme_finding(), files)
    await client.aclose()

    assert plan is not None
    assert plan.patches[0].path == "README.md"
    assert plan.patches[0].action == "create"
    assert "demo" in plan.patches[0].new_content


async def test_readme_plan_returns_none_when_a_variant_already_exists(mock_site):
    routes = {"/repos/octo/demo/contents/readme.md": _contents_response("s", "hi")}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await ReadmeFixer().plan(_readme_finding(), files)
    await client.aclose()
    assert plan is None


# --- EnvExampleFixer ----------------------------------------------------------


def test_env_example_handles_only_repo_env_example_present():
    fixer = EnvExampleFixer()
    assert fixer.handles(_env_finding())
    assert not fixer.handles(_readme_finding())


async def test_env_example_plan_creates_from_committed_env_file(mock_site):
    routes = {
        "/repos/octo/demo/contents/.env": _contents_response("s", "API_KEY=secret123\nDEBUG=true\n"),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await EnvExampleFixer().plan(_env_finding(), files)
    await client.aclose()

    assert plan is not None
    patch = plan.patches[0]
    assert patch.path == ".env.example"
    assert patch.action == "create"
    assert patch.new_content == "API_KEY=\nDEBUG=\n"
    assert "secret123" not in patch.new_content


async def test_env_example_plan_returns_none_when_no_env_file_exists(mock_site):
    routes: dict = {}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await EnvExampleFixer().plan(_env_finding(), files)
    await client.aclose()
    assert plan is None


# --- _declared_spdx_id / _license_text: pure, offline -------------------------


def _src(content: str) -> SourceFile:
    return SourceFile(path="x", content=content, sha="s")


def test_declared_spdx_id_reads_package_json_license_field():
    package_json = _src(json.dumps({"license": "MIT"}))
    assert _declared_spdx_id(package_json, None) == "MIT"


def test_declared_spdx_id_reads_pep621_project_license():
    pyproject = _src('[project]\nname = "x"\nlicense = "ISC"\n')
    assert _declared_spdx_id(None, pyproject) == "ISC"


def test_declared_spdx_id_reads_poetry_license():
    pyproject = _src('[tool.poetry]\nname = "x"\nlicense = "BSD-3-Clause"\n')
    assert _declared_spdx_id(None, pyproject) == "BSD-3-Clause"


def test_declared_spdx_id_returns_none_when_nothing_declared():
    assert _declared_spdx_id(_src(json.dumps({"name": "x"})), None) is None
    assert _declared_spdx_id(None, None) is None


def test_declared_spdx_id_ignores_a_non_string_license_table():
    pyproject = _src('[project]\nlicense = { file = "LICENSE" }\n')
    assert _declared_spdx_id(None, pyproject) is None


def test_license_text_returns_none_for_an_unrecognized_id():
    assert _license_text("GPL-3.0", "octo", 2026) is None


def test_license_text_mit_includes_holder_and_year():
    text = _license_text("MIT", "octo", 2026)
    assert text is not None
    assert "MIT License" in text
    assert "Copyright (c) 2026 octo" in text


def test_license_text_is_case_insensitive_on_the_spdx_id():
    assert _license_text("mit", "octo", 2026) == _license_text("MIT", "octo", 2026)


# --- LicenseFixer --------------------------------------------------------------


def _license_finding() -> Finding:
    return Finding(id="repo-license-present", title="No LICENSE", category="Hygiene", severity=Severity.LOW, status=Status.WARN)


def test_license_handles_only_repo_license_present():
    fixer = LicenseFixer()
    assert fixer.handles(_license_finding())
    assert not fixer.handles(_readme_finding())


async def test_license_plan_creates_from_declared_package_json_license(mock_site):
    routes = {
        "/repos/octo/demo/contents/package.json": _contents_response("s", json.dumps({"license": "MIT"})),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await LicenseFixer().plan(_license_finding(), files)
    await client.aclose()

    assert plan is not None
    assert plan.tier == 1
    patch = plan.patches[0]
    assert patch.path == "LICENSE"
    assert patch.action == "create"
    assert "MIT License" in patch.new_content
    assert "octo" in patch.new_content


async def test_license_plan_returns_none_when_a_license_already_exists(mock_site):
    routes = {"/repos/octo/demo/contents/LICENSE": _contents_response("s", "MIT License")}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await LicenseFixer().plan(_license_finding(), files)
    await client.aclose()
    assert plan is None


async def test_license_plan_returns_none_when_nothing_is_declared(mock_site):
    routes = {
        "/repos/octo/demo/contents/package.json": _contents_response("s", json.dumps({"name": "demo"})),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await LicenseFixer().plan(_license_finding(), files)
    await client.aclose()
    assert plan is None


async def test_license_plan_returns_none_for_an_unrecognized_declared_id(mock_site):
    routes = {
        "/repos/octo/demo/contents/package.json": _contents_response("s", json.dumps({"license": "WTFPL"})),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await LicenseFixer().plan(_license_finding(), files)
    await client.aclose()
    assert plan is None


# --- CiScaffoldFixer ------------------------------------------------------------


def _ci_finding() -> Finding:
    return Finding(id="repo-ci-configured", title="No CI configured", category="Hygiene", severity=Severity.LOW, status=Status.WARN)


def test_ci_scaffold_handles_only_repo_ci_configured():
    fixer = CiScaffoldFixer()
    assert fixer.handles(_ci_finding())
    assert not fixer.handles(_readme_finding())


async def test_ci_scaffold_plan_creates_workflow_from_recognized_nextjs_stack(mock_site):
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": (404, {}, ""),
        "/repos/octo/demo/contents/vercel.json": (404, {}, ""),
        "/repos/octo/demo/contents/next.config.ts": _contents_response("s", "export default {};\n"),
        "/repos/octo/demo/contents/package.json": _contents_response(
            "s", json.dumps({"scripts": {"lint": "next lint", "test": "jest", "build": "next build", "dev": "next dev"}})
        ),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await CiScaffoldFixer().plan(_ci_finding(), files)
    await client.aclose()

    assert plan is not None
    assert plan.tier == 1
    patch = plan.patches[0]
    assert patch.path == ".github/workflows/ci.yml"
    assert patch.action == "create"
    assert "npm run lint" in patch.new_content
    assert "npm run test" in patch.new_content
    assert "npm run build" in patch.new_content
    assert "npm run dev" not in patch.new_content


async def test_ci_scaffold_plan_returns_none_for_an_unrecognized_stack(mock_site):
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": (404, {}, ""),
        "/repos/octo/demo/contents/vercel.json": (404, {}, ""),
        "/repos/octo/demo/contents/next.config.ts": (404, {}, ""),
        "/repos/octo/demo/contents/next.config.js": (404, {}, ""),
        "/repos/octo/demo/contents/next.config.mjs": (404, {}, ""),
        "/repos/octo/demo/contents/package.json": (404, {}, ""),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await CiScaffoldFixer().plan(_ci_finding(), files)
    await client.aclose()
    assert plan is None


async def test_ci_scaffold_plan_returns_none_when_workflow_already_exists(mock_site):
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": _contents_response("s", "name: CI\n"),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await CiScaffoldFixer().plan(_ci_finding(), files)
    await client.aclose()
    assert plan is None


async def test_ci_scaffold_plan_returns_none_when_no_scripts_match(mock_site):
    routes = {
        "/repos/octo/demo/contents/.github/workflows/ci.yml": (404, {}, ""),
        "/repos/octo/demo/contents/vercel.json": _contents_response("s", json.dumps({})),
        "/repos/octo/demo/contents/package.json": _contents_response(
            "s", json.dumps({"scripts": {"dev": "next dev"}})
        ),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await CiScaffoldFixer().plan(_ci_finding(), files)
    await client.aclose()
    assert plan is None
