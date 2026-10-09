"""Error tracking that cannot carry Sentinels' data out (Launch Plan 7.1).

Sentinels handles things people consider private: the addresses they scan,
what the scans found, repository paths, GitHub tokens and session cookies. An
error report that included any of that would be a data leak with a dashboard.
`send_default_pii=False` alone doesn't prevent it: exception messages, log
lines, request URLs and stack-frame variables all carry values the scrubber of
a default install never looks at.

So this module is a **whitelist**: `scrub_event` builds a new event from the
few fields that are safe, and everything else is dropped by default, including
fields a later SDK version adds. What survives is the error's type, where in
the code it happened, which route (with ids replaced), and the version.

Nothing is sent unless `SENTRY_DSN` is set, so local runs and tests send
nothing.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# What a free-text field (an exception message, a log line) may not contain.
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>)\]}]+")
_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL)
_SECRET = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{16,}"                 # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{16,}"
    r"|gsk_[A-Za-z0-9]{16,}"                         # Groq keys
    r"|sk-[A-Za-z0-9_\-]{16,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"   # JWTs
    r"|(?i:bearer|basic|token)\s+[A-Za-z0-9._\-~+/=]{8,})"
)
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# Candidates are confirmed with the ipaddress module, so a clock time such as
# 12:30:45 isn't mistaken for an address.
_IPV6_CANDIDATE = re.compile(r"(?<![\w:])[0-9a-fA-F:.]*:[0-9a-fA-F:.]*:[0-9a-fA-F:.]*(?![\w:])")
# A bare hostname such as "victim-site.example": an error like "could not
# resolve victim-site.example" carries the scanned site without any scheme.
# Names that are really files (main.py, package.json) are left readable.
_FILE_SUFFIXES = {
    "py", "pyc", "json", "toml", "txt", "md", "yml", "yaml", "db", "pem", "js", "mjs",
    "ts", "tsx", "jsx", "css", "html", "lock", "cfg", "ini", "conf", "log", "sql", "env",
}
_HOST = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+([A-Za-z]{2,})\b")
_MAX_TEXT = 300


def _ipv6(match: re.Match) -> str:
    try:
        ipaddress.IPv6Address(match.group(0))
    except ValueError:
        return match.group(0)
    return "<ip>"


def _host(match: re.Match) -> str:
    return match.group(0) if match.group(1).lower() in _FILE_SUFFIXES else "<host>"


def scrub_text(value: Any) -> str:
    """A string with addresses, secrets, ids and e-mail addresses replaced."""
    text = str(value)
    text = _PEM.sub("<secret>", text)
    text = _SECRET.sub("<secret>", text)
    text = _URL.sub("<url>", text)
    text = _EMAIL.sub("<email>", text)
    text = _UUID.sub("<id>", text)
    text = _IPV4.sub("<ip>", text)
    text = _IPV6_CANDIDATE.sub(_ipv6, text)
    text = _HOST.sub(_host, text)
    return text[:_MAX_TEXT]


def scrub_path(url: str | None) -> str | None:
    """The path of a request URL with no host, no query string and no ids."""
    if not url:
        return None
    path = urlsplit(url).path or "/"
    return _UUID.sub("<id>", path)[:_MAX_TEXT]


def _frames(stacktrace: dict | None) -> dict | None:
    if not stacktrace:
        return None
    keep = ("filename", "function", "module", "lineno", "in_app")
    return {
        "frames": [
            {k: frame[k] for k in keep if k in frame}
            for frame in stacktrace.get("frames", [])
        ]
    }


def scrub_event(event: dict, hint: dict | None = None) -> dict | None:
    """Build the report that is allowed to leave: only whitelisted fields."""
    clean: dict[str, Any] = {}
    for key in ("event_id", "timestamp", "level", "platform", "release", "environment", "sdk"):
        if key in event:
            clean[key] = event[key]

    if event.get("exception"):
        clean["exception"] = {
            "values": [
                {
                    "type": value.get("type"),
                    "value": scrub_text(value.get("value", "")),
                    "module": value.get("module"),
                    "stacktrace": _frames(value.get("stacktrace")),
                    "mechanism": value.get("mechanism"),
                }
                for value in event["exception"].get("values", [])
            ]
        }
    if event.get("logentry"):
        # The unformatted template only: its parameters are what carry data.
        clean["logentry"] = {"message": scrub_text(event["logentry"].get("message", ""))}
    if event.get("message"):
        clean["message"] = scrub_text(event["message"])

    request = event.get("request") or {}
    if request:
        clean["request"] = {"method": request.get("method"), "url": scrub_path(request.get("url"))}
    if event.get("transaction"):
        clean["transaction"] = scrub_path(event["transaction"]) or scrub_text(event["transaction"])

    contexts = event.get("contexts") or {}
    clean["contexts"] = {k: contexts[k] for k in ("runtime",) if k in contexts}
    return clean


def init_error_tracking() -> bool:
    """Start reporting if `SENTRY_DSN` is set. Returns whether it started.

    A broken Sentry setup (a mistyped DSN, an SDK problem) must never stop the
    API from starting, so any failure here is logged and swallowed.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENVIRONMENT") or "production",
            release=os.environ.get("RENDER_GIT_COMMIT") or None,
            send_default_pii=False,
            include_local_variables=False,      # frame variables hold request data
            max_request_body_size="never",
            max_breadcrumbs=0,                  # breadcrumbs record log lines and HTTP calls
            traces_sample_rate=0.0,
            server_name="",
            before_send=scrub_event,
        )
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("error tracking could not start; continuing without it")
        return False
    logger.info("error tracking enabled")
    return True
