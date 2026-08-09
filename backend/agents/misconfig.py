"""The Misconfiguration agent — checks for a server set up carelessly:
directory listings, forgotten backup files, debug output, version strings,
risky HTTP methods, leftover installer pages, and cache-exposed cookies.

Every check here was picked against PLAN-v4's overlap map: `.env`/`.git`
stay owned by `exposure.py`, the four security headers stay owned by
`headers.py`, and the generator tag / robots.txt sensitive paths stay owned
by `recon.py`. Nothing in this file re-checks any of those.

Unlike `headers.py`/`exposure.py`, most checks here are FAIL-only — a clean
result for "no directory listing found" isn't worth a PASS finding of its
own, since the check ran silently across six paths, not one specific thing
with an obvious present/absent state. The one deliberate exception is the
server-version check (D), which states its clean result explicitly so a
bare `Server: nginx` can't be read as "we didn't check."
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

import httpx

from agents.base import BaseAgent, ScanContext
from agents.probe import Budget, RobotsGate, safe_get, safe_options
from models import EvidenceKind, Finding, Severity, Status

OWASP_MISCONFIG = "A05:2021 - Security Misconfiguration"

MAX_REQUESTS = 18
DEADLINE_SECONDS = 12.0

DIR_LISTING_PATHS = ["/uploads/", "/files/", "/assets/", "/backup/", "/images/", "/static/"]
# Deliberately excludes .env and .git — those stay owned by exposure.py.
# Enforced directly by test_misconfig.py.
BACKUP_FILE_PATHS = [
    "/backup.zip", "/backup.sql", "/database.sql", "/site.tar.gz",
    "/web.config.bak", "/config.php.bak", "/.DS_Store",
]
DEFAULT_PAGE_PATHS = ["/install.php", "/setup.php", "/phpinfo.php"]

_RISKY_METHODS = {"PUT", "DELETE", "PATCH", "TRACE"}
_VERSION_HEADERS = ("server", "x-powered-by", "x-aspnet-version")
_VERSION_RE = re.compile(r"\d+\.\d+")

_AUTOINDEX_RE = re.compile(
    r"<title>Index of /|<h1>Index of|<table id=\"indexlist\"", re.IGNORECASE
)
_SENSITIVE_EXT_RE = re.compile(r"[\w.-]+\.(?:sql|env)\b", re.IGNORECASE)
_ARCHIVE_EXT_RE = re.compile(r"[\w.-]+\.(?:zip|tar\.gz|tgz|rar|bak|db|dump)\b", re.IGNORECASE)

_SQL_DUMP_MARKER_RE = re.compile(r"\bCREATE TABLE\b|\bINSERT INTO\b|\bDROP TABLE\b", re.IGNORECASE)

_DEBUG_PAGE_RE = re.compile(
    r"Traceback \(most recent call last\)|Werkzeug Debugger|Whoops[\\/]|"
    r"Symfony\\Component|Server Error in '/' Application",
    re.IGNORECASE,
)
_VERBOSE_DB_ERROR_RE = re.compile(r"Warning: mysqli|ORA-\d+|SQLSTATE\[", re.IGNORECASE)

_DEFAULT_PAGE_MARKERS = (
    "welcome to nginx!",
    "apache2 ubuntu default page",
    "iis windows server",
    "it works!",
    "the install worked successfully! congratulations!",  # Django
    "you've arrived home",  # Laravel
)
_PHPINFO_MARKERS = ("phpinfo()", "php version =>")
_INSTALLER_MARKERS = (
    "choose language", "select database", "setup wizard", "installation wizard",
    "famous five-minute install", "database configuration",
)

_SESSION_COOKIE_RE = re.compile(r"(?:PHPSESSID|JSESSIONID|connect\.sid|sess(?:ion)?[_-]?id)=", re.IGNORECASE)


class MisconfigAgent(BaseAgent):
    name = "misconfig"
    display_name = "Misconfiguration"
    purpose = "Checks for a server set up carelessly — listings, backups, debug pages, and risky methods."
    checks = [
        "Directory listing — checks whether common upload/asset directories serve a browsable file index",
        "Backup/config files — checks for forgotten backup archives and database dumps",
        "Debug/error exposure — scans responses for stack traces and verbose database errors",
        "Server version disclosure — checks Server/X-Powered-By for an exact version number",
        "HTTP methods — checks whether risky methods (PUT/DELETE/PATCH/TRACE) are advertised",
        "Default/setup pages — checks for a default install page or a reachable installer script",
        "Unsafe caching — checks whether a session cookie is served with a cacheable response",
    ]
    category = "Misconfiguration"

    async def scan(self, context: ScanContext) -> list[Finding]:
        robots = RobotsGate()
        await robots.load(context)
        budget = Budget(MAX_REQUESTS, DEADLINE_SECONDS)
        probed: list[dict] = []

        homepage = await self._fetch(context, context.url, robots, budget, probed)
        # Free: RobotsGate.load() (or another concurrent agent) already
        # fetched this through the same cache — this just reads that result
        # to scan its body, not a new network request.
        await self._fetch_free(context, urljoin(context.url, "/robots.txt"), probed)

        findings: list[Finding] = []
        findings.extend(await self._check_dir_listing(context, robots, budget, probed))
        findings.extend(await self._check_backup_files(context, robots, budget, probed))
        findings.extend(await self._check_default_pages(context, robots, budget, probed, homepage))
        findings.extend(self._check_server_version(homepage))
        methods_finding = await self._check_methods(context, budget)
        if methods_finding is not None:
            findings.append(methods_finding)
        findings.extend(self._check_debug_output(probed))
        findings.extend(self._check_unsafe_caching(probed))

        skipped = robots.skipped_paths
        if skipped:
            findings.append(self._robots_skipped_finding(skipped))
        if budget.partial:
            findings.append(self._partial_finding(budget))

        return findings

    # --- Fetch helpers -----------------------------------------------------------

    async def _fetch(self, context, url, robots, budget, probed) -> httpx.Response | None:
        path = urlsplit(url).path or "/"
        if not robots.allowed(path):
            return None
        if not budget.allow():
            return None
        response = await safe_get(context, url)
        if response is not None:
            probed.append({"url": url, "response": response})
        return response

    async def _fetch_free(self, context, url, probed) -> None:
        response = await safe_get(context, url)
        if response is not None:
            probed.append({"url": url, "response": response})

    # --- Check A: directory listing ------------------------------------------------

    async def _check_dir_listing(self, context, robots, budget, probed) -> list[Finding]:
        findings = []
        for path in DIR_LISTING_PATHS:
            url = urljoin(context.url, path)
            response = await self._fetch(context, url, robots, budget, probed)
            if response is None or response.status_code != 200:
                continue
            text = response.text
            if not _AUTOINDEX_RE.search(text):
                continue

            severity = Severity.LOW
            if _SENSITIVE_EXT_RE.search(text):
                severity = Severity.HIGH
            elif _ARCHIVE_EXT_RE.search(text):
                severity = Severity.MEDIUM

            evidence_text = f"GET {url} -> 200, response matches a directory listing (autoindex) page."
            findings.append(
                Finding(
                    id="dir-listing",
                    title="Directory listing is enabled",
                    category="Misconfiguration",
                    severity=severity,
                    status=Status.FAIL,
                    owasp=OWASP_MISCONFIG,
                    affected_url=url,
                    evidence=evidence_text,
                    description="A browsable file index lets anyone enumerate every file in this directory, including ones never meant to be linked from the site.",
                    remediation="Disable directory listing/autoindex on the web server for this path, or remove the directory from the public document root.",
                    evidence_items=[self.evidence(EvidenceKind.HTML_SNIPPET, "Directory listing response", evidence_text)],
                )
            )
        return findings

    # --- Check B: backup / config files -------------------------------------------

    async def _check_backup_files(self, context, robots, budget, probed) -> list[Finding]:
        findings = []
        for path in BACKUP_FILE_PATHS:
            url = urljoin(context.url, path)
            response = await self._fetch(context, url, robots, budget, probed)
            if response is None or response.status_code != 200:
                continue

            content_type = response.headers.get("content-type", "").lower()
            text = response.text
            stripped = text.lstrip().lower()
            looks_like_soft_404 = "html" in content_type or stripped.startswith(("<html", "<!doctype"))
            if looks_like_soft_404 or not text.strip():
                continue

            is_dump = path.endswith(".sql") or "dump" in path
            severity = Severity.HIGH
            title = "Backup/config file is publicly accessible"
            if is_dump and _SQL_DUMP_MARKER_RE.search(text):
                severity = Severity.CRITICAL
                title = "Database dump is publicly accessible"

            evidence_text = f"GET {url} -> 200, Content-Type: {content_type or '(none)'}, {len(text)} bytes."
            findings.append(
                Finding(
                    id="backup-file-exposed",
                    title=title,
                    category="Misconfiguration",
                    severity=severity,
                    status=Status.FAIL,
                    owasp=OWASP_MISCONFIG,
                    affected_url=url,
                    evidence=evidence_text,
                    description="A publicly reachable backup or database dump can hand over the entire site — code, configuration, and often real user data — in one request.",
                    remediation="Remove this file from the public document root immediately, and audit whether it was ever accessed by anyone else.",
                    evidence_items=[self.evidence(EvidenceKind.REQUEST, "Backup file response", evidence_text)],
                )
            )
        return findings

    # --- Check C: debug / error exposure -----------------------------------------

    def _check_debug_output(self, probed: list[dict]) -> list[Finding]:
        findings = []
        for item in probed:
            url, response = item["url"], item["response"]
            text = response.text
            has_debug_page = bool(_DEBUG_PAGE_RE.search(text)) or response.headers.get("x-debug-token") is not None
            has_verbose_error = bool(_VERBOSE_DB_ERROR_RE.search(text))
            if not has_debug_page and not has_verbose_error:
                continue

            severity = Severity.HIGH if has_debug_page else Severity.MEDIUM
            reason = "a stack trace or debugger page marker" if has_debug_page else "verbose database error text"
            evidence_text = f"GET {url} -> response contains {reason}."
            findings.append(
                Finding(
                    id="debug-output-exposed",
                    title="Debug or error output is publicly exposed",
                    category="Misconfiguration",
                    severity=severity,
                    status=Status.FAIL,
                    owasp=OWASP_MISCONFIG,
                    affected_url=url,
                    evidence=evidence_text,
                    description="Debug output and verbose error messages reveal file paths, framework internals, and sometimes query text — exactly what an attacker uses to plan a real attack.",
                    remediation="Disable debug mode and verbose error display in production; show a generic error page instead.",
                    evidence_items=[self.evidence(EvidenceKind.HTML_SNIPPET, "Debug output scan", evidence_text)],
                )
            )
        return findings

    # --- Check D: server version disclosure ---------------------------------------

    def _check_server_version(self, homepage) -> list[Finding]:
        if homepage is None:
            return []
        findings = []
        for header_name in _VERSION_HEADERS:
            value = homepage.headers.get(header_name)
            if value is None:
                continue
            evidence_text = f"{header_name}: {value}"
            if _VERSION_RE.search(value):
                findings.append(
                    Finding(
                        id="server-version-disclosed",
                        title=f"{header_name} reveals an exact software version",
                        category="Misconfiguration",
                        severity=Severity.LOW,
                        status=Status.WARN,
                        owasp=OWASP_MISCONFIG,
                        evidence=evidence_text,
                        description="An exact version number tells an attacker precisely which known vulnerabilities to try, without guessing.",
                        remediation=f"Configure the server to omit the version from {header_name}, or remove the header entirely.",
                        evidence_items=[self.evidence(EvidenceKind.RESPONSE_HEADERS, "Response headers", evidence_text)],
                    )
                )
            else:
                findings.append(
                    Finding(
                        id="server-version-disclosed",
                        title=f"{header_name} present without a version number",
                        category="Misconfiguration",
                        severity=Severity.INFO,
                        status=Status.PASS,
                        owasp=OWASP_MISCONFIG,
                        evidence=evidence_text,
                        evidence_items=[self.evidence(EvidenceKind.RESPONSE_HEADERS, "Response headers", evidence_text)],
                    )
                )
        return findings

    # --- Check E: risky HTTP methods ------------------------------------------------

    async def _check_methods(self, context: ScanContext, budget: Budget) -> Finding | None:
        if not budget.allow():
            return None
        response = await safe_options(context, context.url)
        if response is None:
            return None

        allow_header = response.headers.get("allow", "")
        methods = {m.strip().upper() for m in allow_header.split(",") if m.strip()}
        risky = methods & _RISKY_METHODS
        if not risky:
            return None

        severity = Severity.MEDIUM if risky & {"PUT", "DELETE"} else Severity.LOW
        evidence_text = f"OPTIONS {context.url} -> Allow: {allow_header}"
        return Finding(
            id="risky-http-methods",
            title="Server advertises risky HTTP methods",
            category="Misconfiguration",
            severity=severity,
            status=Status.WARN,
            owasp=OWASP_MISCONFIG,
            evidence=evidence_text,
            description=(
                f"The Allow header lists {', '.join(sorted(risky))}. If the "
                "matching handlers aren't independently authenticated, these "
                "methods can let a request write or delete data directly. "
                "These methods were never invoked."
            ),
            remediation="Disable TRACE entirely, and confirm any advertised write method is genuinely authenticated and intended.",
            evidence_items=[self.evidence(EvidenceKind.RESPONSE_HEADERS, "OPTIONS response", evidence_text)],
        )

    # --- Check F: default / setup pages ---------------------------------------------

    async def _check_default_pages(self, context, robots, budget, probed, homepage) -> list[Finding]:
        findings = []
        if homepage is not None:
            lowered = homepage.text.lower()
            if any(marker in lowered for marker in _DEFAULT_PAGE_MARKERS):
                evidence_text = f"GET {context.url} -> 200, response matches a default installation page."
                findings.append(
                    Finding(
                        id="default-page-served",
                        title="Default installation page is being served",
                        category="Misconfiguration",
                        severity=Severity.LOW,
                        status=Status.WARN,
                        owasp=OWASP_MISCONFIG,
                        affected_url=context.url,
                        evidence=evidence_text,
                        description="A default landing page means the real site was never deployed here, or a leftover install is still reachable.",
                        remediation="Replace the default page with the real site, or take the host down if it isn't meant to be live yet.",
                        evidence_items=[self.evidence(EvidenceKind.HTML_SNIPPET, "Homepage response", evidence_text)],
                    )
                )

        for path in DEFAULT_PAGE_PATHS:
            url = urljoin(context.url, path)
            response = await self._fetch(context, url, robots, budget, probed)
            if response is None or response.status_code != 200:
                continue
            lowered = response.text.lower()

            is_setup_page = False
            title = ""
            if path == "/phpinfo.php" and any(marker in lowered for marker in _PHPINFO_MARKERS):
                is_setup_page = True
                title = "phpinfo() output is publicly accessible"
            elif path in ("/install.php", "/setup.php") and any(marker in lowered for marker in _INSTALLER_MARKERS):
                is_setup_page = True
                title = "Installer script is publicly accessible"

            if not is_setup_page:
                continue

            evidence_text = f"GET {url} -> 200, response matches a live setup/diagnostic page."
            findings.append(
                Finding(
                    id="setup-page-exposed",
                    title=title,
                    category="Misconfiguration",
                    severity=Severity.HIGH,
                    status=Status.FAIL,
                    owasp=OWASP_MISCONFIG,
                    affected_url=url,
                    evidence=evidence_text,
                    description="An installer or diagnostic page reachable by anyone is a direct path to reconfiguring or fingerprinting the server — phpinfo() alone reveals paths, loaded modules, and often credentials in environment variables.",
                    remediation="Delete this file from the server entirely; it should never exist outside a local development environment.",
                    evidence_items=[self.evidence(EvidenceKind.HTML_SNIPPET, "Setup page response", evidence_text)],
                )
            )
        return findings

    # --- Check G: unsafe caching ------------------------------------------------------

    def _check_unsafe_caching(self, probed: list[dict]) -> list[Finding]:
        findings = []
        for item in probed:
            url, response = item["url"], item["response"]
            set_cookie = response.headers.get("set-cookie", "")
            if not set_cookie or not _SESSION_COOKIE_RE.search(set_cookie):
                continue

            cache_control = response.headers.get("cache-control", "").lower()
            if "no-store" in cache_control or "private" in cache_control:
                continue
            looks_cacheable = not cache_control or "public" in cache_control or "max-age" in cache_control
            if not looks_cacheable:
                continue

            evidence_text = f"GET {url} -> Set-Cookie carries a session-shaped cookie, Cache-Control: {cache_control or '(none)'}."
            findings.append(
                Finding(
                    id="sensitive-response-cacheable",
                    title="Session cookie served with a cacheable response",
                    category="Misconfiguration",
                    severity=Severity.MEDIUM,
                    status=Status.FAIL,
                    owasp=OWASP_MISCONFIG,
                    affected_url=url,
                    evidence=evidence_text,
                    description="A response that sets a session cookie and is also cacheable can be stored by a shared proxy or the browser's disk cache — potentially handing one user's session to the next person who loads the cached copy.",
                    remediation="Add Cache-Control: no-store (or private) to any response that sets a session cookie.",
                    evidence_items=[self.evidence(EvidenceKind.RESPONSE_HEADERS, "Cache-Control / Set-Cookie", evidence_text)],
                )
            )
        return findings

    # --- Fallbacks -------------------------------------------------------------------

    def _robots_skipped_finding(self, skipped: list[str]) -> Finding:
        evidence_text = f"Skipped per robots.txt: {', '.join(skipped)}"
        return Finding(
            id="misconfig-scan-robots-skipped",
            title="Some misconfiguration checks were skipped (robots.txt)",
            category="Misconfiguration",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_MISCONFIG,
            evidence=evidence_text,
        )

    def _partial_finding(self, budget: Budget) -> Finding:
        evidence_text = (
            f"Stopped after {budget.max_requests} requests / {budget.deadline_seconds}s "
            "— results may be incomplete."
        )
        return Finding(
            id="misconfig-scan-partial",
            title="Misconfiguration scan stopped early (budget exhausted)",
            category="Misconfiguration",
            severity=Severity.INFO,
            status=Status.PASS,
            owasp=OWASP_MISCONFIG,
            evidence=evidence_text,
        )
