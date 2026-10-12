"""The PDF sandbox, checked against a real Chromium (launch plan task B6).

`test_pdf_sandbox.py` replaces Playwright with a fake and proves the browser
is *configured* to have no network. This file proves the configuration
works: a real browser renders a page that tries to load things from a local
server every way a page can, and the server must see nothing.

Skipped where Chromium isn't installed (CI, and any machine that hasn't run
`playwright install chromium`).
"""
from __future__ import annotations

import asyncio
import http.server
import threading

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

import report.pdf as pdf
from models import ScanReport

# A 1x1 PNG, inline: the one kind of resource the sandbox lets through.
PIXEL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(scope="module")
def chromium_installed() -> None:
    async def launch() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            await browser.close()

    try:
        asyncio.run(launch())
    except PlaywrightError as exc:
        pytest.skip(f"Chromium is not installed for Playwright: {str(exc).splitlines()[0]}")


@pytest.fixture
def listener():
    """A local HTTP server that records every path requested from it."""
    paths: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            paths.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x")

        def log_message(self, *args) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", paths
    server.shutdown()
    server.server_close()


async def test_real_browser_makes_no_requests(chromium_installed, listener):
    base, paths = listener
    html = f"""<html><head>
    <link rel="stylesheet" href="{base}/style.css">
    <style>
      @import url("{base}/import.css");
      body {{ background: url("{base}/background.png"); }}
      @font-face {{ font-family: remote; src: url("{base}/font.woff"); }}
      p {{ font-family: remote; }}
    </style>
    <script src="{base}/script.js"></script>
    <script>fetch("{base}/fetch"); new Image().src = "{base}/scripted-image";</script>
    <meta http-equiv="refresh" content="0;url={base}/refresh">
    </head><body>
    <p>sandbox</p>
    <img src="{base}/image.png">
    <img src="{PIXEL}">
    <iframe src="{base}/frame"></iframe>
    <object data="{base}/object"></object>
    <video src="{base}/video.mp4"></video>
    <img src="http://169.254.169.254/latest/meta-data/">
    </body></html>"""

    pdf_bytes = await pdf._render_pdf(html)
    await asyncio.sleep(0.3)  # anything in flight would have landed by now

    assert pdf_bytes.startswith(b"%PDF-")
    assert paths == []


async def test_real_browser_renders_a_report(chromium_installed, monkeypatch):
    monkeypatch.setenv("SENTINELS_PDF_ENABLED", "true")
    report = ScanReport(
        id="scan-1", url="https://example.com", scanned_at="2026-10-12T00:00:00+00:00",
        duration_ms=1, score=90, grade="A", findings=[], checklist=[],
    )

    pdf_bytes = await pdf.generate_pdf(report)

    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 10_000
