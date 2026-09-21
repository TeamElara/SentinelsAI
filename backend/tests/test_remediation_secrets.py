"""Tests for remediation/secrets.py -- the committed-.env removal fixer."""
from __future__ import annotations

import base64
import json

from models import Finding, Severity, Status
from remediation.secrets import SecretEnvCommittedFixer
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
        id="secret-env-committed-dot-env",
        title="Committed .env-shaped file: .env",
        category="Secrets",
        severity=Severity.CRITICAL,
        status=Status.FAIL,
        file_path=".env",
        line=1,
    )
    defaults.update(overrides)
    return Finding(**defaults)


def test_handles_only_secret_env_committed_ids():
    fixer = SecretEnvCommittedFixer()
    assert fixer.handles(_finding())
    assert not fixer.handles(_finding(id="docker-root-user-Dockerfile"))


async def test_plan_deletes_an_allowlisted_env_file(mock_site):
    routes = {"/repos/octo/demo/contents/.env": _contents_response("sha1", "API_KEY=abc123\n")}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await SecretEnvCommittedFixer().plan(_finding(), files)
    await client.aclose()

    assert plan is not None
    assert plan.tier == 2
    patch = plan.patches[0]
    assert patch.path == ".env"
    assert patch.action == "delete"
    assert patch.new_content is None
    assert patch.original_sha == "sha1"


async def test_plan_returns_none_for_a_nested_path_not_on_the_allowlist(mock_site):
    """A static allowlist can only ever hold exact, developer-reviewed
    strings -- a nested .env can never match one, so this declines rather
    than deleting something that was never explicitly reviewed."""
    routes = {"/repos/octo/demo/contents/backend/.env": _contents_response("sha1", "API_KEY=abc123\n")}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    finding = _finding(id="secret-env-committed-backend-.env", file_path="backend/.env")
    plan = await SecretEnvCommittedFixer().plan(finding, files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_when_file_is_already_gone(mock_site):
    routes: dict = {}
    client = mock_site(routes, base_url="https://api.github.com")
    files = FileSource(client=client, owner="octo", repo="demo", ref="main")
    plan = await SecretEnvCommittedFixer().plan(_finding(), files)
    await client.aclose()
    assert plan is None


async def test_plan_returns_none_without_file_path():
    files = FileSource(client=None, owner="o", repo="r", ref="main")  # type: ignore[arg-type]
    plan = await SecretEnvCommittedFixer().plan(_finding(file_path=None), files)
    assert plan is None
