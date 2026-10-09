"""Tests for the repo fetcher's download path (launch plan task B5): the
tarball is streamed to disk under a size limit and a time limit, and every
exit -- success, refusal, cancellation -- leaves nothing behind.

GitHub is a `httpx.MockTransport`; nothing here touches the network.
"""
from __future__ import annotations

import asyncio
import io
import tarfile
import tracemalloc
from pathlib import Path

import httpx
import pytest

import repo.fetch as fetch
from repo.fetch import fetch_repo


def make_tarball(files: dict[str, bytes]) -> bytes:
    """A gzipped tarball shaped like GitHub's: one wrapping top-level directory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(f"octo-demo-abc123/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class ChunkedBody(httpx.AsyncByteStream):
    """A response body delivered in pieces, optionally slowly or forever."""

    def __init__(self, chunks, delay: float = 0.0) -> None:
        self._chunks = chunks
        self._delay = delay
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def github(tarball_response, *, size_kb: int = 100) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/octo/demo":
            return httpx.Response(200, json={"size": size_kb, "default_branch": "main"})
        if request.url.path.startswith("/repos/octo/demo/tarball/"):
            return tarball_response() if callable(tarball_response) else tarball_response
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def work_dirs(tmp_path, monkeypatch):
    """Point the fetcher's temp directories at `tmp_path`; return a function
    listing whatever is left there."""
    monkeypatch.setattr(fetch.tempfile, "tempdir", str(tmp_path))
    return lambda: sorted(p.name for p in tmp_path.iterdir())


async def test_fetch_extracts_the_repo_and_cleans_up(work_dirs):
    tarball = make_tarball({"README.md": b"# demo\n", "src/app.py": b"print('hi')\n"})

    async with github(httpx.Response(200, content=tarball)) as client:
        async with fetch_repo("octo", "demo", None, client) as fetched:
            assert fetched.ref == "main"
            assert (fetched.root / "README.md").read_bytes() == b"# demo\n"
            assert (fetched.root / "src" / "app.py").exists()
            # Only the extracted tree is kept for the scan, not the tarball too.
            assert sorted(p.name for p in fetched.root.parent.iterdir()) == ["repo"]
            assert len(work_dirs()) == 1

    assert work_dirs() == []


async def test_download_is_written_to_disk_not_held_in_memory(work_dirs, monkeypatch):
    """40 MB arrives in 1 MB chunks; the fetcher's own memory use must stay
    far below that. (Extraction is stubbed: this is about the download.)"""
    chunk = b"\0" * (1024 * 1024)
    sizes: list[int] = []
    monkeypatch.setattr(fetch, "_extract_tarball", lambda tarball, dest: sizes.append(tarball.stat().st_size))

    async with github(lambda: httpx.Response(200, stream=ChunkedBody(chunk for _ in range(40)))) as client:
        tracemalloc.start()
        try:
            async with fetch_repo("octo", "demo", None, client):
                pass
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

    assert sizes == [40 * 1024 * 1024]
    assert peak < 8 * 1024 * 1024
    assert work_dirs() == []


async def test_download_over_the_limit_is_refused_and_cleaned_up(work_dirs, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_DOWNLOAD_BYTES", 5_000)
    body = ChunkedBody(b"x" * 1_000 for _ in range(50))

    async with github(httpx.Response(200, stream=body)) as client:
        with pytest.raises(ValueError, match="download size limit"):
            async with fetch_repo("octo", "demo", None, client):
                pytest.fail("should not get as far as scanning")

    assert body.closed
    assert work_dirs() == []


async def test_declared_size_over_the_limit_is_refused_before_reading(work_dirs, monkeypatch):
    monkeypatch.setattr(fetch, "MAX_DOWNLOAD_BYTES", 5_000)
    read: list[int] = []

    def chunks():
        read.append(1)
        yield b"x"

    response = httpx.Response(200, headers={"content-length": "999999"}, stream=ChunkedBody(chunks()))
    async with github(response) as client:
        with pytest.raises(ValueError, match="download size limit"):
            async with fetch_repo("octo", "demo", None, client):
                pass

    assert read == []
    assert work_dirs() == []


async def test_slow_trickle_hits_the_download_deadline(work_dirs, monkeypatch):
    """Each chunk arrives well inside any per-read timeout; only the overall
    deadline can end this."""
    monkeypatch.setattr(fetch, "DOWNLOAD_DEADLINE_SECONDS", 0.15)
    body = ChunkedBody((b"x" for _ in range(10_000)), delay=0.02)

    async with github(httpx.Response(200, stream=body)) as client:
        with pytest.raises(ValueError, match="took too long to download"):
            async with fetch_repo("octo", "demo", None, client):
                pass

    assert body.closed
    assert work_dirs() == []


async def test_cancelled_download_is_cleaned_up(work_dirs):
    started = asyncio.Event()

    def chunks():
        while True:
            started.set()
            yield b"x" * 1_000

    body = ChunkedBody(chunks(), delay=0.01)

    async def scan():
        async with github(httpx.Response(200, stream=body)) as client:
            async with fetch_repo("octo", "demo", None, client):
                pass

    task = asyncio.ensure_future(scan())
    await asyncio.wait_for(started.wait(), 5)
    assert len(work_dirs()) == 1  # the partial download is on disk right now

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert body.closed
    assert work_dirs() == []


async def test_cancelled_scan_removes_the_extracted_repo(work_dirs):
    tarball = make_tarball({"README.md": b"# demo\n"})
    inside = asyncio.Event()

    async def scan():
        async with github(httpx.Response(200, content=tarball)) as client:
            async with fetch_repo("octo", "demo", None, client):
                inside.set()
                await asyncio.Event().wait()

    task = asyncio.ensure_future(scan())
    await asyncio.wait_for(inside.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert work_dirs() == []


async def test_oversized_repo_is_refused_before_any_download(work_dirs):
    def tarball():
        raise AssertionError("tarball requested")

    async with github(tarball, size_kb=fetch.MAX_REPO_SIZE_KB + 1) as client:
        with pytest.raises(ValueError, match="too large to scan"):
            async with fetch_repo("octo", "demo", None, client):
                pass

    assert work_dirs() == []


async def test_path_traversal_entries_never_leave_the_repo_directory(work_dirs, tmp_path):
    """Extraction is unchanged by B5; this pins that moving it to read from
    a file kept `filter="data"` doing its job."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in ("octo-demo-abc123/ok.txt", "octo-demo-abc123/../../escaped.txt"):
            info = tarfile.TarInfo(name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b"hi"))

    async with github(httpx.Response(200, content=buffer.getvalue())) as client:
        try:
            async with fetch_repo("octo", "demo", None, client) as fetched:
                assert (fetched.root / "ok.txt").exists()
        except (ValueError, tarfile.TarError):
            pass  # refusing the whole archive is fine too

    assert not list(Path(tmp_path).rglob("escaped.txt"))
    assert work_dirs() == []
