"""Tests for remediation/dependencies.py -- the dependency version fixer
(PLAN-v5 Stage F).

The pure rewrite/parsing helpers are tested with no network at all, same
precedent `test_remediation_workflows.py` sets for `rewrite_uses_line`.
`DependencyVersionFixer.plan` is tested against a mocked transport serving
both a fake GitHub content endpoint and a fake OSV.dev -- `mock_site`
matches on path only, so one fixture can stand in for both hosts at once.
"""
from __future__ import annotations

import base64
import json

from agents.repo.dependencies import Dependency, dependency_finding_id
from models import Finding, Severity, Status
from remediation.dependencies import (
    DependencyVersionFixer,
    _fixed_versions,
    _parse_plain_version,
    _rewrite_package_json_pin,
    _rewrite_pyproject_pin,
    _rewrite_requirements_pin,
)
from remediation.source import FileSource


def _dep(**overrides) -> Dependency:
    defaults = dict(name="requests", version="2.6.0", ecosystem="PyPI", source_file="requirements.txt")
    defaults.update(overrides)
    return Dependency(**defaults)


def _finding(dep: Dependency | None = None, **overrides) -> Finding:
    dep = dep or _dep()
    defaults = dict(
        id=dependency_finding_id(dep),
        title=f"Known-vulnerable dependency: {dep.name}@{dep.version}",
        category="Dependencies",
        severity=Severity.HIGH,
        status=Status.FAIL,
        file_path=dep.source_file,
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _contents_response(sha: str, content: str) -> tuple[int, dict, str]:
    body = json.dumps({
        "sha": sha,
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    })
    return (200, {"content-type": "application/json"}, body)


def _query_response(vuln_ids: list[str]) -> tuple[int, dict, str]:
    body = json.dumps({"vulns": [{"id": vid} for vid in vuln_ids]})
    return (200, {"content-type": "application/json"}, body)


def _vuln_response(vuln_id: str, ecosystem: str, name: str, fixed: list[str] | None) -> tuple[int, dict, str]:
    events = [{"introduced": "0"}]
    if fixed is not None:
        events.extend({"fixed": v} for v in fixed)
    body = json.dumps({
        "id": vuln_id,
        "affected": [{"package": {"ecosystem": ecosystem, "name": name}, "ranges": [{"events": events}]}],
    })
    return (200, {"content-type": "application/json"}, body)


# --- pure helpers: no network -----------------------------------------------


def test_rewrite_requirements_pin_replaces_only_the_version():
    content = "requests==2.6.0  # pinned for compat\nflask==1.0.0\n"
    result = _rewrite_requirements_pin(content, "requests", "2.6.0", "2.6.1")
    assert result == "requests==2.6.1  # pinned for compat\nflask==1.0.0\n"


def test_rewrite_requirements_pin_returns_none_when_pin_not_found():
    assert _rewrite_requirements_pin("flask==1.0.0\n", "requests", "2.6.0", "2.6.1") is None


def test_rewrite_package_json_pin_preserves_range_prefix():
    content = json.dumps({"dependencies": {"lodash": "^4.17.0"}}, indent=2) + "\n"
    result = _rewrite_package_json_pin(content, "lodash", "4.17.0", "4.17.21")
    assert json.loads(result)["dependencies"]["lodash"] == "^4.17.21"


def test_rewrite_package_json_pin_checks_devdependencies_too():
    content = json.dumps({"devDependencies": {"lodash": "4.17.0"}}, indent=2) + "\n"
    result = _rewrite_package_json_pin(content, "lodash", "4.17.0", "4.17.21")
    assert json.loads(result)["devDependencies"]["lodash"] == "4.17.21"


def test_rewrite_package_json_pin_returns_none_on_malformed_json():
    assert _rewrite_package_json_pin("{not json", "lodash", "4.17.0", "4.17.21") is None


def test_rewrite_pyproject_pin_handles_pep621_list_item():
    content = 'dependencies = [\n  "requests==2.6.0",\n]\n'
    result = _rewrite_pyproject_pin(content, "requests", "2.6.0", "2.6.1")
    assert '"requests==2.6.1"' in result
    assert '"requests==2.6.0"' not in result


def test_rewrite_pyproject_pin_handles_poetry_bare_string():
    content = '[tool.poetry.dependencies]\nrequests = "^2.6.0"\n'
    result = _rewrite_pyproject_pin(content, "requests", "2.6.0", "2.6.1")
    assert result == '[tool.poetry.dependencies]\nrequests = "^2.6.1"\n'


def test_rewrite_pyproject_pin_declines_poetry_inline_table():
    content = '[tool.poetry.dependencies]\nrequests = {version = "^2.6.0", extras = ["security"]}\n'
    assert _rewrite_pyproject_pin(content, "requests", "2.6.0", "2.6.1") is None


def test_parse_plain_version_accepts_numeric_dotted():
    assert _parse_plain_version("2.6.1") == (2, 6, 1)


def test_parse_plain_version_rejects_prerelease_suffix():
    assert _parse_plain_version("2.0.0rc1") is None


def test_fixed_versions_returns_none_when_a_range_never_closes():
    vuln = {"affected": [{
        "package": {"ecosystem": "PyPI", "name": "requests"},
        "ranges": [{"events": [{"introduced": "0"}]}],
    }]}
    assert _fixed_versions(vuln, "PyPI", "requests") is None


def test_fixed_versions_returns_none_when_package_not_named_in_record():
    vuln = {"affected": [{
        "package": {"ecosystem": "PyPI", "name": "some-other-package"},
        "ranges": [{"events": [{"fixed": "2.6.1"}]}],
    }]}
    assert _fixed_versions(vuln, "PyPI", "requests") is None


def test_fixed_versions_collects_every_fixed_event():
    vuln = {"affected": [{
        "package": {"ecosystem": "PyPI", "name": "requests"},
        "ranges": [{"events": [{"fixed": "2.6.1"}, {"fixed": "2.7.0"}]}],
    }]}
    assert _fixed_versions(vuln, "PyPI", "requests") == ["2.6.1", "2.7.0"]


# --- DependencyVersionFixer.handles -----------------------------------------


def test_handles_dependency_findings_with_a_file_path():
    assert DependencyVersionFixer().handles(_finding())


def test_handles_excludes_the_osv_unreachable_finding():
    unreachable = Finding(
        id="dependency-osv-unreachable",
        title="Dependency versions could not be checked",
        category="Dependencies",
        severity=Severity.INFO,
        status=Status.WARN,
        file_path=None,
    )
    assert not DependencyVersionFixer().handles(unreachable)


def test_handles_excludes_unrelated_findings():
    assert not DependencyVersionFixer().handles(_finding(id="docker-root-user-Dockerfile"))


# --- DependencyVersionFixer.plan: mocked network ----------------------------


async def test_plan_bumps_a_vulnerable_requirements_pin(mock_site):
    dep = _dep()
    finding = _finding(dep)
    routes = {
        "/repos/octo/demo/contents/requirements.txt": _contents_response("blobsha", "requests==2.6.0\n"),
        "/v1/query": _query_response(["GHSA-xxxx"]),
        "/v1/vulns/GHSA-xxxx": _vuln_response("GHSA-xxxx", "PyPI", "requests", ["2.6.1"]),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()

    assert plan is not None
    assert plan.tier == 2
    assert len(plan.patches) == 1
    patch = plan.patches[0]
    assert patch.path == "requirements.txt"
    assert patch.action == "modify"
    assert patch.original_sha == "blobsha"
    assert "requests==2.6.1" in patch.new_content
    assert "2.6.1" in plan.summary


async def test_plan_takes_the_highest_fixed_version_across_all_vulns(mock_site):
    dep = _dep()
    finding = _finding(dep)
    routes = {
        "/repos/octo/demo/contents/requirements.txt": _contents_response("blobsha", "requests==2.6.0\n"),
        "/v1/query": _query_response(["GHSA-aaaa", "GHSA-bbbb"]),
        "/v1/vulns/GHSA-aaaa": _vuln_response("GHSA-aaaa", "PyPI", "requests", ["2.6.1"]),
        "/v1/vulns/GHSA-bbbb": _vuln_response("GHSA-bbbb", "PyPI", "requests", ["2.7.5"]),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()

    assert plan is not None
    assert "requests==2.7.5" in plan.patches[0].new_content


async def test_plan_returns_none_when_already_fixed(mock_site):
    dep = _dep()
    finding = _finding(dep)
    routes = {
        "/repos/octo/demo/contents/requirements.txt": _contents_response("blobsha", "requests==2.6.0\n"),
        "/v1/query": _query_response([]),  # OSV says the current pin is clean
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_osv_query_unreachable(mock_site):
    dep = _dep()
    finding = _finding(dep)
    routes = {
        "/repos/octo/demo/contents/requirements.txt": _contents_response("blobsha", "requests==2.6.0\n"),
        # no /v1/query route -- resolves to a 404
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_a_vuln_range_never_closes(mock_site):
    dep = _dep()
    finding = _finding(dep)
    routes = {
        "/repos/octo/demo/contents/requirements.txt": _contents_response("blobsha", "requests==2.6.0\n"),
        "/v1/query": _query_response(["GHSA-xxxx"]),
        "/v1/vulns/GHSA-xxxx": _vuln_response("GHSA-xxxx", "PyPI", "requests", None),  # no fixed event
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_fixed_version_is_not_plain_numeric(mock_site):
    dep = _dep()
    finding = _finding(dep)
    routes = {
        "/repos/octo/demo/contents/requirements.txt": _contents_response("blobsha", "requests==2.6.0\n"),
        "/v1/query": _query_response(["GHSA-xxxx"]),
        "/v1/vulns/GHSA-xxxx": _vuln_response("GHSA-xxxx", "PyPI", "requests", ["2.7.0rc1"]),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_for_a_lockfile_only_finding(mock_site):
    dep = _dep(source_file="package-lock.json", name="lodash", ecosystem="npm", version="4.17.0")
    finding = _finding(dep)
    client = mock_site({}, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_manifest_is_gone(mock_site):
    finding = _finding()
    client = mock_site({}, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_pin_no_longer_present(mock_site):
    dep = _dep()
    finding = _finding(dep)
    routes = {
        # requests has been removed from the manifest since the scan ran
        "/repos/octo/demo/contents/requirements.txt": _contents_response("blobsha", "flask==1.0.0\n"),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DependencyVersionFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_without_file_path():
    files = FileSource(client=None, owner="o", repo="r", ref="main")  # type: ignore[arg-type]
    finding = _finding(file_path=None)
    plan = await DependencyVersionFixer().plan(finding, files)
    assert plan is None
