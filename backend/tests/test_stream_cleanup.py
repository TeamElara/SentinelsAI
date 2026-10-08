"""A scan stream is closed the moment its response ends, so a closed browser tab
stops the scan's agents at once instead of whenever the garbage collector runs
(Launch Plan, B4 hand-off to Track A)."""
from __future__ import annotations

import asyncio

import anyio
import pytest

from tests.test_main_audit import SECRET, _signed_in_cookie

ROUTES = [
    ("/scan/stream", "run_scan_stream", "https://example.com"),
    ("/repo/stream", "run_repo_scan_stream", "https://github.com/octo/demo"),
]


class Recorder:
    """A fake scan generator that records whether its cleanup ran."""

    def __init__(self):
        self.started = False
        self.cleaned_up = False

    async def stream(self, url, user_id=None):
        from models import AgentResult

        self.started = True
        try:
            for name in ("headers", "tls", "dns"):
                yield ("agent", AgentResult(agent=name, findings=[], duration_ms=1))
                await asyncio.sleep(0)
        finally:
            # What the real orchestrator does here: cancel agents, close sockets.
            self.cleaned_up = True


@pytest.fixture
def app_module(temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", SECRET)
    import main

    return main


def _user():
    from storage.users import user_for_token_hash  # noqa: F401 - import side effects only

    user, _ = _signed_in_cookie(1, "alice")
    return user


class _Request:
    headers: dict = {}


async def _disconnect_after_first_event(response) -> list[dict]:
    """Drive the real ASGI response the way a server does: the client takes one
    event, then its socket stalls (`send` blocks) and it disconnects."""
    sent: list[dict] = []
    first_event_sent = anyio.Event()

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("more_body"):
            first_event_sent.set()
            await anyio.sleep(60)            # a stalled socket

    async def receive():
        await first_event_sent.wait()
        return {"type": "http.disconnect"}

    # asgi spec 2.3: Starlette listens for the disconnect message and cancels
    # the response's task group when it arrives.
    await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
    return sent


def _handler(app_module, path):
    handler = app_module.scan_stream if path == "/scan/stream" else app_module.repo_scan_stream
    kwargs = {"permission_confirmed": True} if "permission_confirmed" in handler.__code__.co_varnames else {}
    return handler, kwargs


@pytest.mark.parametrize("path,attr,target", ROUTES)
async def test_a_disconnecting_browser_cleans_up_the_scan(app_module, monkeypatch, path, attr, target):
    recorder = Recorder()
    monkeypatch.setattr(app_module, attr, recorder.stream)
    monkeypatch.setattr(app_module, "enforce_scan_rate_limit", lambda user_id: None)
    handler, kwargs = _handler(app_module, path)
    response = await handler(target, _Request(), user=_user(), **kwargs)

    sent = await _disconnect_after_first_event(response)

    assert recorder.started
    assert any(m["type"] == "http.response.body" for m in sent)
    assert recorder.cleaned_up, "the scan generator was left running after the client left"


@pytest.mark.parametrize("path,attr,target", ROUTES)
async def test_cleanup_that_itself_awaits_is_not_cut_short_by_the_disconnect(
    app_module, monkeypatch, path, attr, target
):
    """The disconnect cancels the response's task group, and that cancellation
    is level-triggered: every later await in the task is cancelled too. The
    generator's cleanup (cancel agents, close sockets) awaits, so it has to run
    shielded."""
    finished = {"value": False}

    async def stream(url, user_id=None):
        from models import AgentResult

        try:
            yield ("agent", AgentResult(agent="headers", findings=[], duration_ms=1))
            yield ("agent", AgentResult(agent="tls", findings=[], duration_ms=1))
        finally:
            await anyio.sleep(0.01)          # e.g. awaiting cancelled agent tasks
            finished["value"] = True

    monkeypatch.setattr(app_module, attr, stream)
    monkeypatch.setattr(app_module, "enforce_scan_rate_limit", lambda user_id: None)
    handler, kwargs = _handler(app_module, path)
    response = await handler(target, _Request(), user=_user(), **kwargs)

    await _disconnect_after_first_event(response)

    assert finished["value"], "the generator's cleanup was cut off by the disconnect"


@pytest.mark.parametrize("path,attr,target", ROUTES)
async def test_a_stream_that_finishes_normally_is_closed_too(app_module, monkeypatch, path, attr, target):
    recorder = Recorder()
    monkeypatch.setattr(app_module, attr, recorder.stream)
    monkeypatch.setattr(app_module, "enforce_scan_rate_limit", lambda user_id: None)
    handler = app_module.scan_stream if path == "/scan/stream" else app_module.repo_scan_stream
    kwargs = {"permission_confirmed": True} if "permission_confirmed" in handler.__code__.co_varnames else {}
    response = await handler(target, _Request(), user=_user(), **kwargs)

    chunks = [chunk async for chunk in response.body_iterator]

    assert len(chunks) == 3 and recorder.cleaned_up


@pytest.mark.parametrize("path,attr,target", ROUTES)
async def test_a_bad_url_is_still_reported_in_the_stream(app_module, monkeypatch, path, attr, target):
    async def refuses(url, user_id=None):
        raise ValueError("That URL could not be parsed.")
        yield  # pragma: no cover

    monkeypatch.setattr(app_module, attr, refuses)
    monkeypatch.setattr(app_module, "enforce_scan_rate_limit", lambda user_id: None)
    handler = app_module.scan_stream if path == "/scan/stream" else app_module.repo_scan_stream
    kwargs = {"permission_confirmed": True} if "permission_confirmed" in handler.__code__.co_varnames else {}
    response = await handler(target, _Request(), user=_user(), **kwargs)

    chunks = [chunk async for chunk in response.body_iterator]

    assert chunks == ['event: failed\ndata: {"detail": "That URL could not be parsed."}\n\n']


# --- "a scan is already running" is a 429, not a 400 --------------------------


@pytest.fixture
def client(app_module):
    from fastapi.testclient import TestClient

    return TestClient(app_module.app)


@pytest.mark.parametrize("path,attr", [("/scan", "run_scan"), ("/repo/scan", "run_repo_scan")])
def test_a_busy_scanner_answers_429_with_its_own_message(client, app_module, monkeypatch, path, attr):
    from scan_limits import ScanBusy

    async def busy(url, user_id=None):
        raise ScanBusy("You already have a scan running. Wait for it to finish.")

    monkeypatch.setattr(app_module, attr, busy)
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)

    res = client.post(path, json={"url": "https://example.com", "permission_confirmed": True})

    assert res.status_code == 429
    assert res.json()["detail"] == "You already have a scan running. Wait for it to finish."


@pytest.mark.parametrize("path,attr", [("/scan", "run_scan"), ("/repo/scan", "run_repo_scan")])
def test_other_refused_urls_are_still_a_400(client, app_module, monkeypatch, path, attr):
    async def refuses(url, user_id=None):
        raise ValueError("That URL could not be parsed.")

    monkeypatch.setattr(app_module, attr, refuses)
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)

    res = client.post(path, json={"url": "nonsense", "permission_confirmed": True})

    assert res.status_code == 400


@pytest.mark.parametrize("path,attr,target", ROUTES)
async def test_a_busy_scanner_on_a_stream_is_reported_in_the_stream(app_module, monkeypatch, path, attr, target):
    """EventSource can't read an HTTP status, so a stream keeps telling the
    person why in a `failed` event, with the scanner's own message."""
    from scan_limits import ScanBusy

    async def busy(url, user_id=None):
        raise ScanBusy("The scanner is busy. Try again in a minute.")
        yield  # pragma: no cover

    monkeypatch.setattr(app_module, attr, busy)
    monkeypatch.setattr(app_module, "enforce_scan_rate_limit", lambda user_id: None)
    handler, kwargs = _handler(app_module, path)
    response = await handler(target, _Request(), user=_user(), **kwargs)

    chunks = [chunk async for chunk in response.body_iterator]

    assert chunks == ['event: failed\ndata: {"detail": "The scanner is busy. Try again in a minute."}\n\n']
