"""Tests for the PDF renderer's sandbox and server-side switch (launch plan
task B6).

Chromium is not started here: Playwright is replaced by a fake that records
how the browser was set up, which is what these tests are about. Whether a
real Chromium fits in the host's memory is measured on the host, not here.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import report.pdf as pdf
from models import ScanReport
from report.pdf import PdfDisabled, PdfExporter, _block_network, generate_pdf, pdf_enabled


def _report() -> ScanReport:
    return ScanReport(
        id="scan-1", url="https://example.com", scanned_at="2026-10-08T00:00:00+00:00",
        duration_ms=1, score=90, grade="A", findings=[], checklist=[],
    )


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = FakeRequest(url)
        self.outcome: str | None = None

    async def abort(self) -> None:
        self.outcome = "aborted"

    async def continue_(self) -> None:
        self.outcome = "continued"


class FakePlaywright:
    """Stands in for `async_playwright()`, recording every setup call in order."""

    def __init__(self, *, pdf_delay: float = 0.0) -> None:
        self.calls: list[tuple] = []
        self.pdf_delay = pdf_delay
        self.active = 0
        self.max_active = 0
        self.chromium = self

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def launch(self, **kwargs):
        self.calls.append(("launch", kwargs))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        return self

    async def new_context(self, **kwargs):
        self.calls.append(("new_context", kwargs))
        return self

    def set_default_timeout(self, timeout) -> None:
        self.calls.append(("set_default_timeout", timeout))

    async def route(self, pattern, handler) -> None:
        self.calls.append(("route", pattern, handler))

    async def new_page(self):
        self.calls.append(("new_page",))
        return self

    async def set_content(self, html, **kwargs) -> None:
        self.calls.append(("set_content", html))

    async def pdf(self, **kwargs) -> bytes:
        await asyncio.sleep(self.pdf_delay)
        return b"%PDF-fake"

    async def close(self) -> None:
        self.calls.append(("close",))
        self.active -= 1


@pytest.fixture
def browser(monkeypatch):
    fake = FakePlaywright()
    monkeypatch.setattr(pdf, "async_playwright", fake)
    monkeypatch.setattr(pdf.sys, "platform", "linux")
    monkeypatch.setenv("SENTINELS_PDF_ENABLED", "true")
    return fake


# --- the server-side switch --------------------------------------------------

@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_pdf_is_enabled_only_by_an_explicit_yes(monkeypatch, value):
    monkeypatch.setenv("SENTINELS_PDF_ENABLED", value)
    assert pdf_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_pdf_is_off_for_anything_else(monkeypatch, value):
    monkeypatch.setenv("SENTINELS_PDF_ENABLED", value)
    assert pdf_enabled() is False


def test_pdf_is_off_when_the_variable_is_missing(monkeypatch):
    monkeypatch.delenv("SENTINELS_PDF_ENABLED", raising=False)
    assert pdf_enabled() is False


async def test_disabled_pdf_is_a_404_and_never_starts_a_browser(browser, monkeypatch):
    monkeypatch.setenv("SENTINELS_PDF_ENABLED", "false")

    with pytest.raises(PdfDisabled) as excinfo:
        await generate_pdf(_report())
    with pytest.raises(HTTPException):
        await PdfExporter().render(_report())

    assert excinfo.value.status_code == 404
    assert browser.calls == []


def test_both_pdf_routes_answer_404_when_disabled(temp_db, monkeypatch, browser):
    monkeypatch.setenv("SENTINELS_PDF_ENABLED", "false")
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", "test-signing-secret")
    from fastapi.testclient import TestClient

    import main
    from auth import session
    from storage.scans import save_scan
    from storage.users import sign_in

    token = session.new_token()
    user = sign_in(
        github_id=7, github_login="octo", avatar_url=None,
        token_hash=session.hash_token(token), expires_at=session.session_expiry(),
    )
    save_scan(_report(), user_id=user.id)
    client = TestClient(main.app)
    client.cookies.set("sentinels_session", session.cookie_value(token, "test-signing-secret"))

    stored = client.get("/scans/scan-1/export/pdf")
    posted = client.post("/scan/pdf", json=_report().model_dump(mode="json"))
    other_format = client.get("/scans/scan-1/export/json")

    assert stored.status_code == 404 and "turned off" in stored.json()["detail"]
    assert posted.status_code == 404 and "turned off" in posted.json()["detail"]
    assert other_format.status_code == 200
    assert browser.calls == []


# --- the sandbox -------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/pixel.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/health",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "ws://example.com/socket",
        "blob:null/1234",
        "about:blank",
    ],
)
async def test_every_request_except_data_urls_is_aborted(url):
    route = FakeRoute(url)
    await _block_network(route)
    assert route.outcome == "aborted"


async def test_data_urls_are_allowed():
    route = FakeRoute("data:image/png;base64,iVBORw0KGgo=")
    await _block_network(route)
    assert route.outcome == "continued"


async def test_browser_is_offline_scriptless_and_filtered_before_any_page_exists(browser):
    assert await generate_pdf(_report()) == b"%PDF-fake"

    names = [call[0] for call in browser.calls]
    assert names == ["launch", "new_context", "set_default_timeout", "route", "new_page", "set_content", "close"]

    context_options = browser.calls[1][1]
    assert context_options["offline"] is True
    assert context_options["java_script_enabled"] is False
    assert context_options["service_workers"] == "block"

    _, pattern, handler = browser.calls[3]
    assert pattern == "**/*"
    assert handler is _block_network


async def test_browser_is_closed_when_rendering_fails(browser, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("renderer crashed")

    monkeypatch.setattr(browser, "set_content", boom)

    with pytest.raises(RuntimeError):
        await generate_pdf(_report())

    assert browser.calls[-1] == ("close",)
    assert browser.active == 0


# --- limits ------------------------------------------------------------------

async def test_only_one_render_runs_at_a_time(browser):
    browser.pdf_delay = 0.05

    results = await asyncio.gather(*(generate_pdf(_report()) for _ in range(4)))

    assert results == [b"%PDF-fake"] * 4
    assert browser.max_active == 1


async def test_render_past_the_deadline_is_a_503_and_closes_the_browser(browser, monkeypatch):
    browser.pdf_delay = 5.0
    monkeypatch.setattr(pdf, "PDF_DEADLINE_SECONDS", 0.05)

    with pytest.raises(HTTPException) as excinfo:
        await generate_pdf(_report())

    assert excinfo.value.status_code == 503
    assert browser.calls[-1] == ("close",)
    assert browser.active == 0
    assert not pdf._render_lock.locked()
