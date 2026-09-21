"""Tests for remediation/stack.py -- detecting what serves a linked repo's
site (PLAN-v5 Stage D). No network involved: every case builds a FileSource
against `mock_site`'s fake transport.
"""
from __future__ import annotations

import base64
import json

from remediation.source import FileSource
from remediation.stack import StackKind, detect_stack


def _contents_response(sha: str, content: str) -> tuple[int, dict, str]:
    body = json.dumps({
        "sha": sha,
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    })
    return (200, {"content-type": "application/json"}, body)


def _files(routes: dict, mock_site) -> FileSource:
    client = mock_site(routes, base_url="https://api.github.com")
    return FileSource(client=client, owner="octo", repo="demo", ref="main")


async def test_detects_vercel_json_even_when_next_config_also_exists(mock_site):
    routes = {
        "/repos/octo/demo/contents/vercel.json": _contents_response("v1", "{}"),
        "/repos/octo/demo/contents/next.config.ts": _contents_response("n1", "module.exports = {}"),
    }
    result = await detect_stack(_files(routes, mock_site))
    assert result is not None
    assert result.kind == StackKind.VERCEL
    assert result.path == "vercel.json"


async def test_detects_next_config_ts(mock_site):
    routes = {"/repos/octo/demo/contents/next.config.ts": _contents_response("n1", "module.exports = {}")}
    result = await detect_stack(_files(routes, mock_site))
    assert result is not None
    assert result.kind == StackKind.NEXTJS
    assert result.path == "next.config.ts"


async def test_detects_next_config_js_when_ts_absent(mock_site):
    routes = {"/repos/octo/demo/contents/next.config.js": _contents_response("n1", "module.exports = {}")}
    result = await detect_stack(_files(routes, mock_site))
    assert result is not None
    assert result.path == "next.config.js"


async def test_falls_back_to_package_json_next_dependency(mock_site):
    routes = {
        "/repos/octo/demo/contents/package.json": _contents_response(
            "p1", '{"dependencies": {"next": "^14.0.0"}}'
        ),
    }
    result = await detect_stack(_files(routes, mock_site))
    assert result is not None
    assert result.kind == StackKind.NEXTJS
    assert result.path == "next.config.ts"
    assert result.existing is None  # create-only -- no config file exists yet


async def test_returns_none_for_an_unrecognized_stack(mock_site):
    routes = {
        "/repos/octo/demo/contents/package.json": _contents_response("p1", '{"dependencies": {}}'),
    }
    result = await detect_stack(_files(routes, mock_site))
    assert result is None


async def test_returns_none_when_nothing_exists_at_all(mock_site):
    result = await detect_stack(_files({}, mock_site))
    assert result is None


async def test_detects_netlify_and_nginx_ahead_of_next(mock_site):
    from remediation.stack import StackKind, detect_stack
    from tests.test_remediation_headers_fix import _contents_response, _files

    routes = {
        "/repos/octo/demo/contents/netlify.toml": _contents_response("n", "[build]\n"),
        "/repos/octo/demo/contents/next.config.js": _contents_response("x", "module.exports = {}\n"),
    }
    assert (await detect_stack(_files(routes, mock_site))).kind == StackKind.NETLIFY
    routes = {
        "/repos/octo/demo/contents/nginx.conf": _contents_response("g", "server {}\n"),
        "/repos/octo/demo/contents/next.config.js": _contents_response("x", "module.exports = {}\n"),
    }
    assert (await detect_stack(_files(routes, mock_site))).kind == StackKind.NGINX
