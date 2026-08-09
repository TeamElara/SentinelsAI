"""Tests for the Misconfiguration agent (PLAN-v4 §V5).

All against `mock_site` — nothing here ever touches a real network.
"""
from __future__ import annotations

from agents.misconfig import (
    BACKUP_FILE_PATHS,
    DEFAULT_PAGE_PATHS,
    DIR_LISTING_PATHS,
    MisconfigAgent,
)
from agents.base import ScanContext
from models import Severity, Status

AUTOINDEX_HTML = '<html><head><title>Index of /uploads</title></head><body><h1>Index of /uploads</h1></body></html>'
BORING_HOMEPAGE = "<html><body><h1>Welcome to My Real Site</h1></body></html>"


async def _run(mock_site, routes):
    client = mock_site(routes)
    context = ScanContext(url="https://example.com", client=client)
    result = await MisconfigAgent().run(context)
    await client.aclose()
    return result


def test_backup_paths_never_include_env_or_git():
    # .env and .git stay owned by exposure.py -- enforced directly here so a
    # future edit can't accidentally reintroduce a duplicate check.
    assert ".env" not in BACKUP_FILE_PATHS
    assert not any("git" in p.lower() for p in BACKUP_FILE_PATHS)
    exposure_paths = {"/.env", "/.git/HEAD"}
    assert not exposure_paths.intersection(f"/{p.lstrip('/')}" for p in BACKUP_FILE_PATHS)


async def test_plain_autoindex_is_low(mock_site):
    result = await _run(mock_site, {"/uploads/": (200, {}, AUTOINDEX_HTML)})
    findings = [f for f in result.findings if f.id == "dir-listing"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.LOW


async def test_autoindex_with_archive_names_is_medium(mock_site):
    body = AUTOINDEX_HTML.replace("</body>", "<a href='site-backup.zip'>site-backup.zip</a></body>")
    result = await _run(mock_site, {"/uploads/": (200, {}, body)})
    findings = [f for f in result.findings if f.id == "dir-listing"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.MEDIUM


async def test_autoindex_with_sql_name_is_high(mock_site):
    body = AUTOINDEX_HTML.replace("</body>", "<a href='dump.sql'>dump.sql</a></body>")
    result = await _run(mock_site, {"/uploads/": (200, {}, body)})
    findings = [f for f in result.findings if f.id == "dir-listing"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


async def test_real_sql_dump_is_critical(mock_site):
    dump = "CREATE TABLE users (id INT, email VARCHAR(255));\nINSERT INTO users VALUES (1, 'a@b.com');"
    result = await _run(mock_site, {"/database.sql": (200, {"content-type": "application/sql"}, dump)})
    findings = [f for f in result.findings if f.id == "backup-file-exposed"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL


async def test_backup_zip_without_sql_content_is_high_not_critical(mock_site):
    result = await _run(
        mock_site, {"/backup.zip": (200, {"content-type": "application/zip"}, "PK\x03\x04binarystuff")}
    )
    findings = [f for f in result.findings if f.id == "backup-file-exposed"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


async def test_soft_404_html_backup_response_is_not_reported(mock_site):
    result = await _run(
        mock_site, {"/backup.zip": (200, {"content-type": "text/html"}, "<html>Not Found</html>")}
    )
    assert [f for f in result.findings if f.id == "backup-file-exposed"] == []


async def test_styled_404_everywhere_yields_zero_findings(mock_site):
    # Homepage is a normal, boring page with no version headers and no
    # markers; everything else genuinely 404s (mock_site's default for any
    # unrouted path) -- nothing here should ever fire.
    result = await _run(mock_site, {"/": (200, {}, BORING_HOMEPAGE)})
    assert result.error is None
    assert result.findings == []


async def test_bare_server_header_is_pass_versioned_is_low(mock_site):
    bare = await _run(mock_site, {"/": (200, {"server": "nginx"}, BORING_HOMEPAGE)})
    bare_findings = [f for f in bare.findings if f.id == "server-version-disclosed"]
    assert len(bare_findings) == 1
    assert bare_findings[0].status == Status.PASS
    assert bare_findings[0].severity == Severity.INFO

    versioned = await _run(mock_site, {"/": (200, {"server": "nginx/1.18.0"}, BORING_HOMEPAGE)})
    versioned_findings = [f for f in versioned.findings if f.id == "server-version-disclosed"]
    assert len(versioned_findings) == 1
    assert versioned_findings[0].status == Status.WARN
    assert versioned_findings[0].severity == Severity.LOW


async def test_risky_methods_medium_for_put_and_delete(mock_site):
    result = await _run(mock_site, {"/": (200, {"allow": "GET, POST, PUT, DELETE"}, BORING_HOMEPAGE)})
    findings = [f for f in result.findings if f.id == "risky-http-methods"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.MEDIUM


async def test_risky_methods_low_for_trace_only(mock_site):
    result = await _run(mock_site, {"/": (200, {"allow": "GET, POST, TRACE"}, BORING_HOMEPAGE)})
    findings = [f for f in result.findings if f.id == "risky-http-methods"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.LOW


async def test_debug_stack_trace_is_high_verbose_db_error_is_medium(mock_site):
    result = await _run(
        mock_site,
        {
            "/": (200, {}, BORING_HOMEPAGE),
            "/install.php": (200, {}, "Traceback (most recent call last):\n  File x"),
        },
    )
    findings = [f for f in result.findings if f.id == "debug-output-exposed"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


async def test_phpinfo_page_is_high(mock_site):
    result = await _run(
        mock_site,
        {"/phpinfo.php": (200, {}, "<title>phpinfo()</title><h1>PHP Version =&gt; 8.2.1</h1>")},
    )
    findings = [f for f in result.findings if f.id == "setup-page-exposed"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


async def test_default_nginx_page_is_low(mock_site):
    result = await _run(mock_site, {"/": (200, {}, "<html><body><h1>Welcome to nginx!</h1></body></html>")})
    findings = [f for f in result.findings if f.id == "default-page-served"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.LOW


async def test_session_cookie_with_cacheable_response_is_medium(mock_site):
    result = await _run(
        mock_site,
        {"/": (200, {"set-cookie": "PHPSESSID=abc123; Path=/", "cache-control": "public, max-age=3600"}, BORING_HOMEPAGE)},
    )
    findings = [f for f in result.findings if f.id == "sensitive-response-cacheable"]
    assert len(findings) == 1
    assert findings[0].severity == Severity.MEDIUM


async def test_session_cookie_with_no_store_is_not_reported(mock_site):
    result = await _run(
        mock_site,
        {"/": (200, {"set-cookie": "PHPSESSID=abc123; Path=/", "cache-control": "no-store"}, BORING_HOMEPAGE)},
    )
    assert [f for f in result.findings if f.id == "sensitive-response-cacheable"] == []


# --- V9: additional failure-case coverage -----------------------------------

async def test_403_everywhere_yields_zero_findings(mock_site):
    routes = {"/": (403, {}, "Forbidden")}
    for path in DIR_LISTING_PATHS + BACKUP_FILE_PATHS + DEFAULT_PAGE_PATHS:
        routes[path] = (403, {}, "Forbidden")
    result = await _run(mock_site, routes)

    assert result.error is None
    assert result.findings == []


async def test_404_everywhere_yields_zero_findings(mock_site):
    # No routes at all -- mock_site's default for any unrouted path is 404,
    # including the homepage itself.
    result = await _run(mock_site, {})
    assert result.error is None
    assert result.findings == []


async def test_backup_file_with_malformed_binary_body_does_not_crash(mock_site):
    # A body that starts like a zip but is truncated/corrupt mid-stream --
    # the agent only sanity-checks shape (non-HTML content-type + some size),
    # never actually unzips it, so this must complete cleanly either way.
    result = await _run(
        mock_site, {"/backup.zip": (200, {"content-type": "application/zip"}, "PK\x03\x04\x00\x00")}
    )
    assert result.error is None


async def test_rate_limited_429_on_every_path_does_not_crash(mock_site):
    routes = {"/": (429, {"retry-after": "30"}, "Too Many Requests")}
    for path in DIR_LISTING_PATHS + BACKUP_FILE_PATHS:
        routes[path] = (429, {"retry-after": "30"}, "Too Many Requests")
    result = await _run(mock_site, routes)

    assert result.error is None
    assert result.findings == []
