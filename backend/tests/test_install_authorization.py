"""The install callback must prove the installation belongs to the signed-in
person before saving it, and an installation can't be taken over (Launch Plan
A3). Also covers the per-repository write check before a fix PR is opened.

GitHub is never called: the three auth helpers the callback uses are replaced
with fakes, and the callback's own App-side lookup is mocked.
"""
from __future__ import annotations

import pytest

from auth.github_oauth import GitHubIdentity
from tests.test_main_audit import SECRET, _signed_in_cookie

STATE = "state-123"


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app, follow_redirects=False)


@pytest.fixture
def github(monkeypatch):
    """Fake GitHub for the callback. Tests change `fake` to steer it."""
    import main

    fake = {
        "token": "user-token",
        "identity": None,            # set per test: a GitHubIdentity
        "accessible": {500},
        "metadata": {
            "account": {"login": "octo"},
            "repository_selection": "selected",
            "permissions": {"contents": "write"},
        },
        "exchanged": [],
    }

    async def exchange(code, redirect_uri=None):
        fake["exchanged"].append(code)
        return fake["token"] if code != "bad-code" else None

    async def identity(token):
        return fake["identity"]

    async def accessible(token):
        return fake["accessible"]

    async def metadata(client, installation_id):
        return fake["metadata"]

    monkeypatch.setattr(main, "exchange_code", exchange)
    monkeypatch.setattr(main, "fetch_identity", identity)
    monkeypatch.setattr(main, "list_user_installation_ids", accessible)
    monkeypatch.setattr(main, "fetch_installation", metadata)
    return fake


def _callback(client, cookie, *, installation_id=500, code="good-code", state=STATE):
    client.cookies.set("sentinels_session", cookie)
    client.cookies.set("sentinels_install_state", STATE)
    query = f"installation_id={installation_id}&setup_action=install&state={state}"
    if code:
        query += f"&code={code}"
    return client.get(f"/auth/github/install/callback?{query}")


def _error(res) -> str | None:
    location = res.headers["location"]
    return location.split("install_error=")[1] if "install_error=" in location else None


def _identity_of(user) -> GitHubIdentity:
    return GitHubIdentity(github_id=user.github_id, login=user.github_login, avatar_url=None)


def test_a_proven_installation_is_saved(client, github):
    from storage.installations import list_installations

    user, cookie = _signed_in_cookie(1, "alice")
    github["identity"] = _identity_of(user)

    res = _callback(client, cookie)

    assert "installed=octo" in res.headers["location"]
    assert [i.installation_id for i in list_installations(user.id)] == [500]


def test_a_callback_without_a_code_saves_nothing(client, github):
    from storage.installations import list_installations

    user, cookie = _signed_in_cookie(1, "alice")
    github["identity"] = _identity_of(user)

    res = _callback(client, cookie, code="")

    assert _error(res) == "authorization_required"
    assert list_installations(user.id) == []
    assert github["exchanged"] == []


def test_a_code_github_rejects_saves_nothing(client, github):
    from storage.installations import list_installations

    user, cookie = _signed_in_cookie(1, "alice")
    github["identity"] = _identity_of(user)

    res = _callback(client, cookie, code="bad-code")

    assert _error(res) == "authorization_failed"
    assert list_installations(user.id) == []


def test_an_authorization_for_a_different_github_account_saves_nothing(client, github):
    from storage.installations import list_installations

    user, cookie = _signed_in_cookie(1, "alice")
    github["identity"] = GitHubIdentity(github_id=999, login="mallory", avatar_url=None)

    res = _callback(client, cookie)

    assert _error(res) == "identity_mismatch"
    assert list_installations(user.id) == []


def test_a_forged_installation_id_saves_nothing(client, github):
    from storage.installations import list_installations

    user, cookie = _signed_in_cookie(1, "alice")
    github["identity"] = _identity_of(user)
    github["accessible"] = {500}                # alice can reach 500, not 777

    res = _callback(client, cookie, installation_id=777)

    assert _error(res) == "installation_not_yours"
    assert list_installations(user.id) == []


def test_github_being_unable_to_list_installations_saves_nothing(client, github):
    from storage.installations import list_installations

    user, cookie = _signed_in_cookie(1, "alice")
    github["identity"] = _identity_of(user)
    github["accessible"] = None

    res = _callback(client, cookie)

    assert _error(res) == "installation_lookup_failed"
    assert list_installations(user.id) == []


def test_a_second_user_cannot_take_over_a_live_installation(client, github):
    from storage.installations import active_installation_for, list_installations

    alice, alice_cookie = _signed_in_cookie(1, "alice")
    bob, bob_cookie = _signed_in_cookie(2, "bob")
    github["identity"] = _identity_of(alice)
    _callback(client, alice_cookie)

    # Bob can genuinely reach installation 500 (say, an org member).
    github["identity"] = _identity_of(bob)
    res = _callback(client, bob_cookie)

    assert _error(res) == "installation_taken"
    assert list_installations(bob.id) == []
    assert active_installation_for(alice.id, "octo") is not None


def test_a_revoked_installation_can_be_claimed_by_someone_who_can_reach_it(client, github):
    from storage.installations import list_installations, revoke_installation

    alice, alice_cookie = _signed_in_cookie(1, "alice")
    bob, bob_cookie = _signed_in_cookie(2, "bob")
    github["identity"] = _identity_of(alice)
    _callback(client, alice_cookie)
    revoke_installation(alice.id, 500)

    github["identity"] = _identity_of(bob)
    res = _callback(client, bob_cookie)

    assert "installed=octo" in res.headers["location"]
    assert [i.installation_id for i in list_installations(bob.id)] == [500]


def test_the_same_user_can_re_run_the_install(client, github):
    from storage.installations import list_installations

    user, cookie = _signed_in_cookie(1, "alice")
    github["identity"] = _identity_of(user)
    _callback(client, cookie)
    github["metadata"] = {**github["metadata"], "repository_selection": "all"}
    res = _callback(client, cookie)

    assert "installed=octo" in res.headers["location"]
    installs = list_installations(user.id)
    assert len(installs) == 1 and installs[0].repo_selection == "all"


# --- storage rule, without HTTP --------------------------------------------


def test_storage_refuses_to_reassign_a_live_installation(temp_db):
    from storage.installations import InstallationOwnedByAnotherUser, save_installation

    alice, _ = _signed_in_cookie(1, "alice")
    bob, _ = _signed_in_cookie(2, "bob")
    save_installation(alice.id, 500, "octo", "all")

    with pytest.raises(InstallationOwnedByAnotherUser):
        save_installation(bob.id, 500, "octo", "all")


# --- per-repository write access -------------------------------------------


async def _role(mock_api, status, body):
    from remediation.access import require_write_access

    client = mock_api({("GET", "/repos/octo/demo/collaborators/alice/permission"): (status, body)})
    return await require_write_access(client, "tok", "octo", "demo", "alice")


@pytest.mark.parametrize("role", ["admin", "maintain", "write"])
async def test_roles_that_can_push_are_accepted(mock_api, role):
    assert await _role(mock_api, 200, {"role_name": role, "permission": "write"}) == role


@pytest.mark.parametrize("role", ["triage", "read"])
async def test_roles_that_cannot_push_are_refused(mock_api, role):
    from remediation.access import RepoAccessError

    with pytest.raises(RepoAccessError) as exc:
        await _role(mock_api, 200, {"role_name": role, "permission": "read"})
    assert exc.value.status == 403


async def test_the_older_permission_field_is_enough_when_role_name_is_missing(mock_api):
    assert await _role(mock_api, 200, {"permission": "write"}) == "write"


@pytest.mark.parametrize("status", [403, 404, 500])
async def test_anything_github_will_not_confirm_counts_as_no(mock_api, status):
    from remediation.access import RepoAccessError

    with pytest.raises(RepoAccessError):
        await _role(mock_api, status, {"message": "nope"})


async def test_a_read_only_user_cannot_apply_and_nothing_is_written(temp_db, monkeypatch):
    from remediation.apply import ApplyError, apply_fixes
    from tests.test_remediation_apply import (
        _FakeProvider, _happy_routes, _patch_transport, _seed,
    )

    user, report = _seed(temp_db)
    routes = _happy_routes()
    routes[("GET", "/repos/octo/demo/collaborators/octo/permission")] = (
        200, {"role_name": "read", "permission": "read"}
    )
    calls = _patch_transport(monkeypatch, routes)

    with pytest.raises(ApplyError) as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())

    assert exc.value.status == 403
    assert not [c for c in calls if c[0] != "GET"]
    assert ("GET", "/repos/octo/demo/contents/.gitignore") not in calls


# --- the GitHub helpers the callback relies on ------------------------------


def _patch_oauth_transport(monkeypatch, handler):
    import httpx

    from auth import github_oauth

    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(github_oauth.httpx, "AsyncClient", factory)


async def test_user_installation_ids_are_collected_across_pages(monkeypatch):
    import httpx

    from auth.github_oauth import list_user_installation_ids

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(200, json={"installations": [{"id": i} for i in range(100)]})
        return httpx.Response(200, json={"installations": [{"id": 1000}]})

    _patch_oauth_transport(monkeypatch, handler)
    ids = await list_user_installation_ids("tok")
    assert ids == set(range(100)) | {1000}


@pytest.mark.parametrize("response", [
    lambda: __import__("httpx").Response(401, json={}),
    lambda: __import__("httpx").Response(200, json={"unexpected": True}),
    lambda: __import__("httpx").Response(200, text="not json"),
])
async def test_user_installation_ids_are_none_when_github_misbehaves(monkeypatch, response):
    from auth.github_oauth import list_user_installation_ids

    _patch_oauth_transport(monkeypatch, lambda request: response())
    assert await list_user_installation_ids("tok") is None


async def test_the_install_code_is_exchanged_without_a_redirect_uri(monkeypatch):
    import httpx

    from auth.github_oauth import exchange_code

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "tok"})

    _patch_oauth_transport(monkeypatch, handler)
    assert await exchange_code("the-code") == "tok"
    assert "redirect_uri" not in seen["body"]
