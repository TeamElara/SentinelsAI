"""How Sentinels reads GitHub as an anonymous reader: the optional token, and a
failed read turned into a message instead of a bare 500.

Seen live (14 Oct): the fix preview answered "FIX PLAN UNAVAILABLE (500)" because
its reads of the repository were anonymous, GitHub's 60-an-hour limit for a shared
IP was spent, and the resulting `HTTPStatusError` was caught by nothing.
"""
from __future__ import annotations

import httpx
import pytest

from github_api import GitHubUnavailable, check_response, public_api_headers
from remediation.source import get_file, resolve_default_ref, resolve_ref_sha


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _limited(reset="1760000000"):
    return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}, json={"message": "API rate limit exceeded"})


def test_the_token_is_sent_when_set_and_blank_means_unset(monkeypatch):
    monkeypatch.delenv("SENTINELS_GITHUB_API_TOKEN", raising=False)
    assert public_api_headers() == {}
    monkeypatch.setenv("SENTINELS_GITHUB_API_TOKEN", "   ")
    assert public_api_headers() == {}
    monkeypatch.setenv("SENTINELS_GITHUB_API_TOKEN", " tok ")
    assert public_api_headers() == {"Authorization": "Bearer tok"}


@pytest.mark.parametrize("call", ["default_ref", "file", "sha"])
async def test_every_preview_read_carries_the_token(monkeypatch, call):
    monkeypatch.setenv("SENTINELS_GITHUB_API_TOKEN", "tok")
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        if request.url.path.endswith("/contents/README.md"):
            return httpx.Response(200, json={"encoding": "base64", "content": "aGk=", "sha": "abc"})
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": "f" * 40})
        return httpx.Response(200, json={"default_branch": "main"})

    async with _client(handler) as client:
        if call == "default_ref":
            await resolve_default_ref(client, "octo", "demo")
        elif call == "file":
            await get_file(client, "octo", "demo", "README.md", "main")
        else:
            await resolve_ref_sha(client, "foo", "bar", "v2")

    assert seen == ["Bearer tok"]


async def test_a_rate_limit_becomes_a_message_with_the_reset_time():
    async with _client(lambda request: _limited("1760000000")) as client:
        with pytest.raises(GitHubUnavailable) as caught:
            await resolve_default_ref(client, "octo", "demo")
    assert caught.value.status == 503
    assert "limiting how often" in caught.value.message
    assert "resets at" in caught.value.message


async def test_other_github_failures_are_a_502_with_the_status():
    async with _client(lambda request: httpx.Response(500)) as client:
        with pytest.raises(GitHubUnavailable) as caught:
            await get_file(client, "octo", "demo", "README.md", "main")
    assert caught.value.status == 502
    assert "HTTP 500" in caught.value.message


async def test_a_missing_file_is_still_none_and_a_missing_repo_says_so():
    async with _client(lambda request: httpx.Response(404)) as client:
        assert await get_file(client, "octo", "demo", "missing", "main") is None
        assert await resolve_ref_sha(client, "foo", "bar", "nope") is None
        with pytest.raises(GitHubUnavailable) as caught:
            await resolve_default_ref(client, "octo", "nowhere")
    assert caught.value.status == 404


def test_a_good_response_passes():
    check_response(httpx.Response(200, json={}))


# --- The two preview routes answer with that message, not a 500 -------------


@pytest.fixture
def client(temp_db, monkeypatch):
    from tests.test_main_audit import SECRET

    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def test_the_preview_routes_answer_a_github_limit_with_a_503_and_the_message(client, monkeypatch):
    import main
    from tests.test_main_audit import _save_scan, _signed_in_cookie

    user, cookie = _signed_in_cookie(1, "alice")
    _save_scan("scan1", user.id)
    client.cookies.set("sentinels_session", cookie)

    async def limited(*args, **kwargs):
        raise GitHubUnavailable("GitHub is limiting how often Sentinels can read octo/demo right now. Try again then.")

    monkeypatch.setattr(main, "preview_plan", limited)
    monkeypatch.setattr(main, "plan_and_save", limited)

    one = client.get("/scans/scan1/findings/missing-readme/fix/plan")
    many = client.post("/scans/scan1/fix/plan", json={"finding_keys": ["missing-readme"]})

    for res in (one, many):
        assert res.status_code == 503
        assert "limiting how often" in res.json()["detail"]
