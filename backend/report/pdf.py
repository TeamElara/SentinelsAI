"""Turn a finished `ScanReport` into a downloadable PDF.

M17 split this file in two: `html_doc.py` now owns "what does the report
look like" (the HTML string), and this file owns only "how do you turn HTML
into a PDF" — driving a real (headless) browser to print it, the same way a
human would use Ctrl+P on a normal web page. See
`docs/learning/17-pdf-export-and-playwright.md` for why a headless browser
is the tool for "HTML in, PDF out" at all.

The browser is the one piece of this backend that would happily fetch any
URL it finds in a page, so it runs boxed in (launch plan task B6):

- `SENTINELS_PDF_ENABLED` is checked here, on the server. Hiding the button
  is not a switch — the route is still there for anyone who calls it.
- The page gets no network at all: every request except a `data:` URL is
  aborted, the browser context is offline, and JavaScript is off. The
  report's HTML is self-contained, so nothing it needs is lost; text that
  came from a scanned site can't make the browser load anything.
- One render at a time, with an overall deadline. Chromium is by far the
  most memory this process ever asks for.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

from fastapi import HTTPException
from playwright.async_api import Route, async_playwright

from models import FixSuggestion, ScanReport
from report.html_doc import render_html


# Longest one render may take, browser start-up included.
PDF_DEADLINE_SECONDS = 30.0
# Per browser operation (launch, load, print), in Playwright's milliseconds.
_STEP_TIMEOUT_MS = 15_000

_render_lock = asyncio.Lock()


def pdf_enabled() -> bool:
    """Whether this server renders PDFs at all.

    Off unless `SENTINELS_PDF_ENABLED` says otherwise: a deployment that
    forgot the variable should not be the one that finds out Chromium does
    not fit in its memory.
    """
    return os.environ.get("SENTINELS_PDF_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


class PdfDisabled(HTTPException):
    """PDF export is switched off on this server.

    An `HTTPException` so that it reaches the client as a plain 404 from
    whichever route asked for a PDF, without each route having to check the
    switch itself (same idea as `rate_limit.py` raising its own 429).
    """

    def __init__(self) -> None:
        super().__init__(status_code=404, detail="PDF export is turned off on this server.")


async def _block_network(route: Route) -> None:
    """Abort every request the page makes, except inline `data:` URLs."""
    if route.request.url.startswith("data:"):
        await route.continue_()
    else:
        await route.abort()


async def _render_pdf(html: str) -> bytes:
    """Drive a real headless Chromium — the same engine, and the same `Ctrl+P`
    machinery, a human would use on a normal web page."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(timeout=_STEP_TIMEOUT_MS)
        try:
            context = await browser.new_context(
                offline=True, java_script_enabled=False, service_workers="block"
            )
            context.set_default_timeout(_STEP_TIMEOUT_MS)
            # Registered on the context, before any page exists, so there is
            # no moment at which a page could load something unfiltered.
            await context.route("**/*", _block_network)
            page = await context.new_page()
            await page.set_content(html, wait_until="load")
            return await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
        finally:
            await browser.close()


def _render_pdf_on_own_loop(html: str) -> bytes:
    """Run `_render_pdf` on a Proactor event loop this function owns.

    Windows has two event loop implementations, and only one of them —
    Proactor — can start a subprocess. Playwright *must* start one: the
    browser it drives is a separate program, not a Python library. Normally
    that's fine, since Proactor is Python's default on Windows.

    `uvicorn --reload` breaks that assumption. In reload mode uvicorn
    deliberately switches the process to `WindowsSelectorEventLoopPolicy`,
    and on a Selector loop `asyncio.create_subprocess_exec` raises a bare
    `NotImplementedError` — so PDF export died with an unexplained 500 for
    anyone running the documented dev command.

    Building the loop directly (rather than via `asyncio.get_event_loop`)
    is the point: the policy is exactly what uvicorn has overridden, so
    asking the policy for a loop would hand back a Selector one again.
    """
    loop = asyncio.ProactorEventLoop()
    try:
        return loop.run_until_complete(_render_pdf(html))
    finally:
        loop.close()


async def generate_pdf(
    report: ScanReport, fixes: Optional[dict[str, FixSuggestion]] = None
) -> bytes:
    """Render `report` to HTML, then print that HTML to PDF.

    Raises `PdfDisabled` (a 404) when the server has PDF export off, and a
    503 when a render doesn't finish inside `PDF_DEADLINE_SECONDS`.
    """
    if not pdf_enabled():
        raise PdfDisabled()
    html = render_html(report, fixes)

    # One Chromium at a time: a second request waits for the first rather
    # than starting another browser next to it.
    async with _render_lock:
        try:
            async with asyncio.timeout(PDF_DEADLINE_SECONDS):
                if sys.platform != "win32":
                    return await _render_pdf(html)

                # A loop can only be run by the thread that owns it, and this
                # thread is already busy running uvicorn's. `to_thread` hands
                # the work to a spare thread — which is free to run a loop of
                # its own — and awaits the result without blocking the server.
                # Same tool the TLS agent (A8) uses to keep a blocking socket
                # handshake off the event loop.
                return await asyncio.to_thread(_render_pdf_on_own_loop, html)
        except TimeoutError:
            raise HTTPException(
                status_code=503, detail="The PDF took too long to render. Try again."
            ) from None


class PdfExporter:
    """Registered in `report/registry.py` under format_id "pdf"."""

    format_id = "pdf"
    media_type = "application/pdf"
    extension = "pdf"

    async def render(
        self, report: ScanReport, fixes: Optional[dict[str, FixSuggestion]] = None
    ) -> bytes:
        return await generate_pdf(report, fixes)
