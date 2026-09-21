"""Tests for remediation/dockerfile.py -- the non-root USER fixer and the
floating :latest tag fixer."""
from __future__ import annotations

import base64
import json

from models import Finding, Severity, Status
from remediation.dockerfile import (
    DockerLatestTagFixer,
    DockerRootUserFixer,
    _detect_family,
    docker_hub_repo,
    resolve_latest_digest,
    rewrite_from_line,
)
from remediation.source import FileSource


def _contents_response(sha: str, content: str) -> tuple[int, dict, str]:
    body = json.dumps({
        "sha": sha,
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    })
    return (200, {"content-type": "application/json"}, body)


def _finding(**overrides) -> Finding:
    defaults = dict(
        id="docker-root-user-Dockerfile",
        title="Dockerfile never switches away from root",
        category="Configuration",
        severity=Severity.MEDIUM,
        status=Status.WARN,
        file_path="Dockerfile",
        line=1,
    )
    defaults.update(overrides)
    return Finding(**defaults)


# --- _detect_family: pure, offline ------------------------------------------


def test_detect_family_alpine():
    assert _detect_family("FROM python:3.12-alpine\n") == "alpine"


def test_detect_family_defaults_to_debian():
    assert _detect_family("FROM python:3.12-slim\n") == "debian"


def test_detect_family_uses_last_from_in_multistage_build():
    text = "FROM node:20 AS build\nRUN npm ci\nFROM python:3.12-alpine\nCOPY --from=build /app /app\n"
    assert _detect_family(text) == "alpine"


# --- DockerRootUserFixer.handles --------------------------------------------


def test_handles_only_docker_root_user_ids():
    fixer = DockerRootUserFixer()
    assert fixer.handles(_finding())
    assert not fixer.handles(_finding(id="docker-latest-tag-Dockerfile-L1"))


# --- DockerRootUserFixer.plan -----------------------------------------------


async def test_plan_inserts_user_before_last_cmd(mock_site):
    dockerfile = "FROM python:3.12-slim\nCOPY . /app\nCMD [\"python\", \"app.py\"]\n"
    routes = {"/repos/octo/demo/contents/Dockerfile": _contents_response("sha1", dockerfile)}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerRootUserFixer().plan(_finding(), files)
    await client.aclose()

    assert plan is not None
    assert plan.tier == 2
    patch = plan.patches[0]
    assert patch.path == "Dockerfile"
    assert patch.action == "modify"
    lines = patch.new_content.splitlines()
    cmd_index = next(i for i, l in enumerate(lines) if l.startswith("CMD"))
    user_index = next(i for i, l in enumerate(lines) if l.startswith("USER"))
    assert user_index < cmd_index


async def test_plan_appends_user_when_no_cmd_or_entrypoint(mock_site):
    dockerfile = "FROM python:3.12-slim\nCOPY . /app\n"
    routes = {"/repos/octo/demo/contents/Dockerfile": _contents_response("sha1", dockerfile)}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerRootUserFixer().plan(_finding(), files)
    await client.aclose()
    assert plan is not None
    assert "USER" in plan.patches[0].new_content


async def test_plan_returns_none_when_user_already_present(mock_site):
    dockerfile = "FROM python:3.12-slim\nUSER appuser\nCMD [\"python\", \"app.py\"]\n"
    routes = {"/repos/octo/demo/contents/Dockerfile": _contents_response("sha1", dockerfile)}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerRootUserFixer().plan(_finding(), files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_file_is_gone(mock_site):
    routes: dict = {}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerRootUserFixer().plan(_finding(), files)
    await client.aclose()
    assert plan is None


# --- docker_hub_repo: pure, offline ------------------------------------------


def test_docker_hub_repo_official_image_gets_library_prefix():
    assert docker_hub_repo("node") == "library/node"


def test_docker_hub_repo_user_image_is_unchanged():
    assert docker_hub_repo("bitnami/node") == "bitnami/node"


def test_docker_hub_repo_normalizes_explicit_docker_io_host():
    assert docker_hub_repo("docker.io/library/node") == "library/node"
    assert docker_hub_repo("docker.io/node") == "library/node"


def test_docker_hub_repo_declines_other_registries():
    assert docker_hub_repo("ghcr.io/foo/bar") is None
    assert docker_hub_repo("myregistry.example.com:5000/foo") is None
    assert docker_hub_repo("localhost:5000/foo") is None


# --- rewrite_from_line: pure, offline -----------------------------------------


def test_rewrite_from_line_pins_a_tagged_reference():
    line = "FROM node:latest\n"
    result = rewrite_from_line(line, "node:latest", "sha256:" + "a" * 64)
    assert result == f"FROM node:latest@sha256:{'a' * 64}\n"


def test_rewrite_from_line_pins_an_untagged_reference():
    line = "FROM node\n"
    result = rewrite_from_line(line, "node", "sha256:" + "a" * 64)
    assert result == f"FROM node:latest@sha256:{'a' * 64}\n"


def test_rewrite_from_line_preserves_stage_alias():
    line = "FROM node:latest AS build\n"
    result = rewrite_from_line(line, "node:latest", "sha256:" + "a" * 64)
    assert result == f"FROM node:latest@sha256:{'a' * 64} AS build\n"


def test_rewrite_from_line_returns_none_when_token_not_present():
    assert rewrite_from_line("FROM python:latest\n", "node:latest", "sha256:" + "a" * 64) is None


def test_rewrite_from_line_returns_none_when_already_pinned():
    line = "FROM node@sha256:" + "b" * 64 + "\n"
    assert rewrite_from_line(line, "node@sha256:" + "b" * 64, "sha256:" + "a" * 64) is None


# --- resolve_latest_digest: mocked network ------------------------------------


async def test_resolve_latest_digest_reads_the_docker_content_digest_header(mock_site):
    digest = "sha256:" + "c" * 64
    routes = {
        "/token": (200, {"content-type": "application/json"}, json.dumps({"token": "tok"})),
        "/v2/library/node/manifests/latest": (200, {"Docker-Content-Digest": digest}, ""),
    }
    client = mock_site(routes, base_url="https://auth.docker.io")
    result = await resolve_latest_digest(client, "library/node")
    await client.aclose()
    assert result == digest


async def test_resolve_latest_digest_returns_none_when_repo_missing(mock_site):
    routes = {
        "/token": (200, {"content-type": "application/json"}, json.dumps({"token": "tok"})),
        # no manifest route -- resolves to a 404
    }
    client = mock_site(routes, base_url="https://auth.docker.io")
    result = await resolve_latest_digest(client, "library/nope")
    await client.aclose()
    assert result is None


async def test_resolve_latest_digest_returns_none_when_token_request_fails(mock_site):
    routes: dict = {}  # /token 404s
    client = mock_site(routes, base_url="https://auth.docker.io")
    result = await resolve_latest_digest(client, "library/node")
    await client.aclose()
    assert result is None


# --- DockerLatestTagFixer.handles --------------------------------------------


def test_latest_tag_handles_only_docker_latest_tag_ids():
    fixer = DockerLatestTagFixer()
    assert fixer.handles(Finding(
        id="docker-latest-tag-Dockerfile-L1", title="t", category="c",
        severity=Severity.LOW, status=Status.WARN, file_path="Dockerfile", line=1,
    ))
    assert not fixer.handles(_finding())


# --- DockerLatestTagFixer.plan: mocked network --------------------------------


def _latest_finding(**overrides) -> Finding:
    defaults = dict(
        id="docker-latest-tag-Dockerfile-L1",
        title="Dockerfile uses the floating :latest tag",
        category="Configuration",
        severity=Severity.LOW,
        status=Status.WARN,
        file_path="Dockerfile",
        line=1,
    )
    defaults.update(overrides)
    return Finding(**defaults)


async def test_latest_tag_plan_pins_a_docker_hub_image(mock_site):
    digest = "sha256:" + "d" * 64
    dockerfile = "FROM node:latest\nCMD [\"node\", \"app.js\"]\n"
    routes = {
        "/repos/octo/demo/contents/Dockerfile": _contents_response("sha1", dockerfile),
        "/token": (200, {"content-type": "application/json"}, json.dumps({"token": "tok"})),
        "/v2/library/node/manifests/latest": (200, {"Docker-Content-Digest": digest}, ""),
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerLatestTagFixer().plan(_latest_finding(), files)
    await client.aclose()

    assert plan is not None
    assert plan.tier == 2
    patch = plan.patches[0]
    assert patch.path == "Dockerfile"
    assert patch.action == "modify"
    assert f"FROM node:latest@{digest}\n" in patch.new_content


async def test_latest_tag_plan_returns_none_for_a_non_docker_hub_registry(mock_site):
    dockerfile = "FROM ghcr.io/foo/bar:latest\n"
    routes = {"/repos/octo/demo/contents/Dockerfile": _contents_response("sha1", dockerfile)}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerLatestTagFixer().plan(_latest_finding(), files)
    await client.aclose()
    assert plan is None


async def test_latest_tag_plan_returns_none_when_no_longer_latest(mock_site):
    dockerfile = "FROM node:20\n"
    routes = {"/repos/octo/demo/contents/Dockerfile": _contents_response("sha1", dockerfile)}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerLatestTagFixer().plan(_latest_finding(), files)
    await client.aclose()
    assert plan is None


async def test_latest_tag_plan_returns_none_when_digest_cannot_be_resolved(mock_site):
    dockerfile = "FROM node:latest\n"
    routes = {
        "/repos/octo/demo/contents/Dockerfile": _contents_response("sha1", dockerfile),
        # no /token route -- resolution fails
    }
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerLatestTagFixer().plan(_latest_finding(), files)
    await client.aclose()
    assert plan is None


async def test_latest_tag_plan_returns_none_when_file_is_gone(mock_site):
    routes: dict = {}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await DockerLatestTagFixer().plan(_latest_finding(), files)
    await client.aclose()
    assert plan is None
