"""The GitHub webhook that tells Sentinels an installation was removed
(Launch Plan A8), and apply reacting when GitHub says the installation is gone."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from tests.test_main_audit import SECRET, _signed_in_cookie

WEBHOOK_SECRET = "webhook-secret-for-tests"


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(client, payload, *, event="installation", signature=None, raw: bytes | None = None):
    body = raw if raw is not None else json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": signature if signature is not None else _sign(body),
        "Content-Type": "application/json",
    }
    return client.post("/github/webhook", content=body, headers=headers)


def _installed(user, installation_id=500):
    from storage.installations import save_installation

    save_installation(user.id, installation_id, "octo", "all")


def _live(user):
    from storage.installations import list_installations

    return [i.installation_id for i in list_installations(user.id)]


def test_a_bad_signature_gets_401_and_changes_nothing(client):
    user, _ = _signed_in_cookie(1, "alice")
    _installed(user)

    res = _post(client, {"action": "deleted", "installation": {"id": 500}}, signature="sha256=" + "0" * 64)

    assert res.status_code == 401
    assert _live(user) == [500]


def test_a_signature_made_with_another_secret_is_refused(client):
    user, _ = _signed_in_cookie(1, "alice")
    _installed(user)
    body = json.dumps({"action": "deleted", "installation": {"id": 500}}).encode()

    res = _post(client, None, raw=body, signature=_sign(body, secret="not-the-secret"))

    assert res.status_code == 401
    assert _live(user) == [500]


def test_a_missing_signature_header_is_refused(client):
    user, _ = _signed_in_cookie(1, "alice")
    _installed(user)

    res = client.post(
        "/github/webhook", content=b"{}", headers={"X-GitHub-Event": "installation"}
    )

    assert res.status_code == 401


def test_the_body_is_not_trusted_before_the_signature_is_checked(client):
    res = _post(client, None, raw=b"this is not json", signature="sha256=bad")
    assert res.status_code == 401        # not a 400 from parsing


def test_without_a_webhook_secret_nothing_is_accepted(client, monkeypatch):
    monkeypatch.delenv("GITHUB_APP_WEBHOOK_SECRET")
    body = b"{}"

    res = client.post("/github/webhook", content=body, headers={"X-Hub-Signature-256": _sign(body)})

    assert res.status_code == 503


@pytest.mark.parametrize("action", ["deleted", "suspend"])
def test_a_signed_end_of_installation_event_revokes_it(client, action):
    user, _ = _signed_in_cookie(1, "alice")
    _installed(user)

    res = _post(client, {"action": action, "installation": {"id": 500}})

    assert res.status_code == 200 and res.json()["revoked"] == 1
    assert _live(user) == []


def test_the_installation_disappears_from_the_users_list(client):
    user, cookie = _signed_in_cookie(1, "alice")
    _installed(user)
    client.cookies.set("sentinels_session", cookie)
    assert [i["installation_id"] for i in client.get("/installations").json()] == [500]

    _post(client, {"action": "deleted", "installation": {"id": 500}})

    assert client.get("/installations").json() == []


def test_only_the_named_installation_is_revoked(client):
    user, _ = _signed_in_cookie(1, "alice")
    _installed(user, 500)
    _installed(user, 501)

    _post(client, {"action": "deleted", "installation": {"id": 500}})

    assert _live(user) == [501]


def test_a_repeated_event_is_harmless(client):
    user, _ = _signed_in_cookie(1, "alice")
    _installed(user)
    payload = {"action": "deleted", "installation": {"id": 500}}

    assert _post(client, payload).json()["revoked"] == 1
    again = _post(client, payload)

    assert again.status_code == 200 and again.json()["revoked"] == 0


@pytest.mark.parametrize("action", ["created", "unsuspend", "new_permissions_accepted"])
def test_other_installation_actions_change_nothing(client, action):
    user, _ = _signed_in_cookie(1, "alice")
    _installed(user)

    res = _post(client, {"action": action, "installation": {"id": 500}})

    assert res.status_code == 200
    assert _live(user) == [500]


def test_ping_and_unrelated_events_are_acknowledged(client):
    assert _post(client, {"zen": "x"}, event="ping").json() == {"ok": True}
    assert _post(client, {}, event="push").status_code == 200


def test_a_malformed_installation_event_is_a_400(client):
    assert _post(client, {"action": "deleted"}).status_code == 400
    assert _post(client, None, raw=b"not json").status_code == 400


# --- apply notices an installation GitHub no longer has ---------------------


async def test_apply_turns_a_missing_installation_into_a_clear_message_and_revokes_it(temp_db):
    from remediation.apply import ApplyError, apply_fixes
    from remediation.tokens import InstallationGone, TokenProvider
    from storage.installations import list_installations
    from tests.test_remediation_apply import _seed

    class Gone(TokenProvider):
        async def token_for(self, client, installation_id):
            raise InstallationGone("Installation 500 no longer exists")

    user, report = _seed(temp_db)
    assert [i.installation_id for i in list_installations(user.id)] == [500]

    with pytest.raises(ApplyError) as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=Gone())

    assert exc.value.status == 409
    assert "uninstalled" in str(exc.value)
    assert list_installations(user.id) == []


async def test_a_404_when_minting_a_token_is_reported_as_the_installation_being_gone(monkeypatch):
    import httpx

    from remediation import tokens
    from remediation.tokens import AppTokenProvider, InstallationGone

    monkeypatch.setattr(tokens, "app_jwt", lambda: "jwt")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404, json={"message": "Not Found"}))
    )

    with pytest.raises(InstallationGone):
        await AppTokenProvider().token_for(client, 500)
