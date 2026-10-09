"""Error reporting to Sentry, with everything a scan could carry stripped first.

Off unless SENTRY_DSN is set, so local development and tests send nothing.

`send_default_pii=False` only stops the SDK adding the user's IP and cookies.
It does nothing about the things this app handles that are far more sensitive
than that: the URL a user scans, finding evidence quoted from the scanned
site, GitHub tokens, and repo file contents. Those reach Sentry through
exception messages, log lines, request URLs, breadcrumbs and stack-frame
variables, so each of those is cut or scrubbed in `scrub_event`, which runs on
every event right before it leaves the process.

The rule is to keep what lets us debug (exception type, our own source frames,
the route path and method) and drop what describes the user's targets. When in
doubt a field is dropped, not cleaned.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Order matters: the PEM block and URLs go before the narrower patterns, so a
# token sitting inside a URL is replaced with the URL marker once.
_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL)
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>)]+")
_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gsk_[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*)"
)
_BEARER = re.compile(r"\b(Bearer|Basic|token)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# Candidates are checked with the ipaddress module, so clock times such as
# 12:30:45 are not mistaken for addresses.
_IPV6_CANDIDATE = re.compile(r"(?<![\w:])[0-9a-fA-F:.]*:[0-9a-fA-F:.]*:[0-9a-fA-F:.]*(?![\w:])")
# A bare hostname such as "example.com". File names that look like hostnames
# (main.py, package.json) are left alone so messages stay readable.
_FILE_SUFFIXES = {
    "py", "pyc", "json", "toml", "txt", "md", "yml", "yaml", "db", "pem", "js",
    "ts", "tsx", "jsx", "css", "html", "lock", "cfg", "ini", "conf", "log", "sql",
}
_HOST = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+([A-Za-z]{2,})\b")


def _host_repl(match: re.Match[str]) -> str:
    return match.group(0) if match.group(1).lower() in _FILE_SUFFIXES else "[host]"


def _ipv6_repl(match: re.Match[str]) -> str:
    try:
        ipaddress.IPv6Address(match.group(0))
    except ValueError:
        return match.group(0)
    return "[ip]"


def scrub_text(text: str) -> str:
    """Replace anything in free text that could identify a target or a secret."""
    text = _PEM.sub("[key]", text)
    text = _URL.sub("[url]", text)
    text = _TOKEN.sub("[token]", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} [token]", text)
    text = _EMAIL.sub("[email]", text)
    text = _IPV4.sub("[ip]", text)
    text = _IPV6_CANDIDATE.sub(_ipv6_repl, text)
    return _HOST.sub(_host_repl, text)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    # Anything else (dict, object, bytes) is dropped rather than walked.
    return "[removed]" if value is not None and not isinstance(value, (int, float, bool)) else value


def _scrub_request(request: dict[str, Any]) -> dict[str, Any]:
    """Keep the method and the path; drop query string, headers, cookies, body."""
    path = ""
    raw_url = request.get("url")
    if isinstance(raw_url, str):
        path = urlsplit(raw_url).path
    return {"method": request.get("method"), "url": path}


def scrub_event(event: dict[str, Any], hint: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """`before_send`: the last stop before an event leaves the process."""
    if "request" in event and isinstance(event["request"], dict):
        event["request"] = _scrub_request(event["request"])

    # Fields that describe the person or the machine, or that free-form code
    # can fill with scan data. Dropped whole.
    for key in ("user", "extra", "breadcrumbs", "server_name", "modules", "contexts"):
        event.pop(key, None)
    event["tags"] = {
        k: v for k, v in (event.get("tags") or {}).items() if k in {"environment", "handled"}
    }

    for entry in (event.get("exception") or {}).get("values", []):
        if isinstance(entry.get("value"), str):
            entry["value"] = scrub_text(entry["value"])
        for frame in (entry.get("stacktrace") or {}).get("frames", []):
            frame.pop("vars", None)

    if isinstance(event.get("message"), str):
        event["message"] = scrub_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for key in ("message", "formatted"):
            if isinstance(logentry.get(key), str):
                logentry[key] = scrub_text(logentry[key])
        if "params" in logentry:
            logentry["params"] = _scrub_value(logentry["params"])
    return event


def _drop(*_args: Any, **_kwargs: Any) -> None:
    return None


def init_sentry() -> bool:
    """Start Sentry if SENTRY_DSN is set. Returns whether it started.

    A broken Sentry setup must never stop the API from starting, so every
    failure here is logged and swallowed.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
            release=os.environ.get("SENTRY_RELEASE") or None,
            send_default_pii=False,
            max_request_body_size="never",
            include_local_variables=False,
            include_source_context=True,
            # Errors only: no performance traces, no profiles, no log shipping,
            # since each of those carries request URLs.
            traces_sample_rate=0,
            before_send=scrub_event,
            before_breadcrumb=_drop,
            before_send_transaction=_drop,
        )
    except Exception:
        logger.exception("Sentry failed to start; continuing without it")
        return False
    logger.info("Sentry error reporting is on")
    return True
