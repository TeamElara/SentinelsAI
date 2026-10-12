"""Error reports must carry no scan data, addresses, tokens or cookies
(Launch Plan 7.1). Everything here is checked on the report that would actually
be sent, not on the scrubber's inputs."""
from __future__ import annotations

import json
import logging

import pytest

import observability
from observability import init_error_tracking, scrub_event, scrub_path, scrub_text

# Things that must never appear anywhere in a report. Each is something this
# product really handles.
SECRETS = [
    "https://victim-site.example/admin?token=abc",       # a scanned address
    "victim-site.example",
    "ghs_" + "A1b2C3d4E5f6G7h8I9j0K1l2",                 # an installation token
    "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8",
    "gsk_" + "abcdEFGH1234ijklMNOP5678",                 # a Groq key
    "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiIxMjMifQ.c2lnbmF0dXJl",
    "Bearer abcdef0123456789",
    "session-cookie-value-123",
    "alice@example.org",
    "203.0.113.77",
    "3f2c1b9e-8d4a-4f6b-9a1c-0e5d7c2b1a90",             # a scan id
    "AKIAIOSFODNN7EXAMPLE-masked-secret",
    "octocat/secret-repo",
    "scanned-target.example",                            # a bare hostname, no scheme
    "2001:db8:85a3::8a2e:370:7334",                      # an IPv6 address
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC-key-body",         # inside a PEM block
]


def rich_event() -> dict:
    """What the SDK would send for an unhandled error during a stream request,
    stuffed with every kind of field that can carry data."""
    leak = f"could not fetch {SECRETS[0]} with {SECRETS[2]} for {SECRETS[8]} from {SECRETS[9]}"
    return {
        "event_id": "e1", "timestamp": "2026-10-09T00:00:00Z", "level": "error",
        "platform": "python", "release": "abc123", "environment": "production",
        "sdk": {"name": "sentry.python", "version": "2.71.0"},
        "server_name": "srv-" + SECRETS[1],
        "user": {"id": 7, "email": SECRETS[8], "ip_address": SECRETS[9], "username": "alice"},
        "request": {
            "method": "GET",
            "url": f"https://api.example.com/scans/{SECRETS[10]}/export/pdf?url={SECRETS[0]}",
            "query_string": f"url={SECRETS[0]}",
            "cookies": {"sentinels_session": SECRETS[7]},
            "headers": {"Authorization": SECRETS[6], "Cookie": SECRETS[7], "X-Forwarded-For": SECRETS[9]},
            "data": {"finding": SECRETS[11], "repo": SECRETS[12]},
            "env": {"REMOTE_ADDR": SECRETS[9]},
        },
        "exception": {"values": [{
            "type": "ValueError", "value": leak, "module": "builtins",
            "mechanism": {"type": "asgi", "handled": False},
            "stacktrace": {"frames": [{
                "filename": "main.py", "function": "scan", "module": "main", "lineno": 10, "in_app": True,
                "abs_path": f"/srv/{SECRETS[1]}/main.py",
                "context_line": f"url = '{SECRETS[0]}'",
                "pre_context": [SECRETS[3]], "post_context": [SECRETS[4]],
                "vars": {"url": SECRETS[0], "token": SECRETS[2], "report": SECRETS[11]},
            }]},
        }]},
        "logentry": {"message": "scan of %s failed for %s", "formatted": leak, "params": [SECRETS[0], SECRETS[8]]},
        "message": leak + f" (also {SECRETS[13]} at {SECRETS[14]}, -----BEGIN PRIVATE KEY-----\n{SECRETS[15]}\n-----END PRIVATE KEY-----)",
        "transaction": f"/scans/{SECRETS[10]}/export/pdf",
        "breadcrumbs": {"values": [{"message": SECRETS[0], "data": {"url": SECRETS[0]}}]},
        "extra": {"installation_token": SECRETS[2], "repo": SECRETS[12]},
        "tags": {"user_url": SECRETS[0]},
        "contexts": {
            "runtime": {"name": "CPython", "version": "3.13.7"},
            "trace": {"data": {"url": SECRETS[0]}},
            "response": {"headers": {"set-cookie": SECRETS[7]}},
        },
        "modules": {"httpx": "0.28.1"},
        "some_future_field": {"holds": SECRETS[0]},
    }


def leaked(report: dict) -> list[str]:
    text = json.dumps(report)
    return [s for s in SECRETS if s in text]


def test_a_report_stuffed_with_scan_data_leaks_none_of_it():
    report = scrub_event(rich_event())
    assert leaked(report) == []


def test_what_is_kept_is_enough_to_debug_with():
    report = scrub_event(rich_event())
    value = report["exception"]["values"][0]
    assert value["type"] == "ValueError"
    assert report["level"] == "error" and report["release"] == "abc123"
    assert value["stacktrace"]["frames"] == [
        {"filename": "main.py", "function": "scan", "module": "main", "lineno": 10, "in_app": True}
    ]
    assert report["request"] == {"method": "GET", "url": "/scans/<id>/export/pdf"}
    assert report["transaction"] == "/scans/<id>/export/pdf"
    assert report["contexts"] == {"runtime": {"name": "CPython", "version": "3.13.7"}}


def test_fields_the_scrubber_has_never_heard_of_are_dropped():
    report = scrub_event(rich_event())
    assert "some_future_field" not in report
    for dropped in ("user", "extra", "tags", "breadcrumbs", "modules", "server_name"):
        assert dropped not in report


@pytest.mark.parametrize("text", [SECRETS[0], f"see {SECRETS[0]}.", f"({SECRETS[0]})", f"'{SECRETS[0]}'"])
def test_addresses_in_free_text_are_replaced(text):
    assert "victim-site" not in scrub_text(text)
    assert "<url>" in scrub_text(text)


@pytest.mark.parametrize("text,gone", [
    ("could not resolve victim-site.example", "victim-site.example"),
    ("TLS handshake with api.victim-site.example failed", "victim-site.example"),
    ("connect to 2001:db8:85a3::8a2e:370:7334 refused", "2001:db8"),
    ("key -----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY----- bad", "MIIEvQIBADANBgkq"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ("auth failed: token ghs_notarealtokenbutlongenough", "ghs_notareal"),
])
def test_things_that_identify_a_target_or_a_secret_are_replaced(text, gone):
    assert gone not in scrub_text(text)


@pytest.mark.parametrize("text", [
    "File main.py line 10 in scan",
    "could not read package.json",
    "failed at 12:30:45 on try 3",
    "ValueError in orchestrator.py",
])
def test_ordinary_text_stays_readable(text):
    assert scrub_text(text) == text


def test_long_messages_are_cut():
    assert len(scrub_text("x" * 5000)) <= 300


@pytest.mark.parametrize("raw,expected", [
    ("https://h.example/scans/3f2c1b9e-8d4a-4f6b-9a1c-0e5d7c2b1a90?url=https://v.example", "/scans/<id>"),
    ("https://h.example/health", "/health"),
    ("https://h.example", "/"),
    (None, None),
    ("", None),
])
def test_request_paths_keep_the_route_and_lose_host_query_and_ids(raw, expected):
    assert scrub_path(raw) == expected


def test_without_a_dsn_nothing_starts(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_error_tracking() is False


# --- through the real SDK -----------------------------------------------------


from sentry_sdk.transport import Transport


class Capture(Transport):
    """Stands in for the network transport and keeps what would be sent. It has
    to be a real `Transport`: the SDK ignores any other object and builds its
    HTTP transport instead."""

    def __init__(self, *args, **kwargs):
        super().__init__(None)
        self.sent: list[dict] = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.payload.json is not None:
                self.sent.append(item.payload.json)

    def flush(self, *args, **kwargs):
        pass

    def kill(self):
        pass

    def is_healthy(self):
        return True


@pytest.fixture
def sdk(monkeypatch):
    import sentry_sdk

    capture = Capture()
    monkeypatch.setenv("SENTRY_DSN", "https://publickey@o0.ingest.sentry.io/1")
    original_init = sentry_sdk.init

    def init_with_capture(**kwargs):
        return original_init(transport=capture, **kwargs)

    monkeypatch.setattr(sentry_sdk, "init", init_with_capture)
    assert init_error_tracking() is True
    yield capture
    sentry_sdk.get_client().close()
    sentry_sdk.init()          # back to a disabled client for other tests


def test_a_captured_exception_with_scan_data_reaches_the_transport_clean(sdk):
    import sentry_sdk

    try:
        token = SECRETS[2]                           # a local variable, must not be sent
        raise RuntimeError(f"fetch {SECRETS[0]} failed using {token} for {SECRETS[8]}")
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)

    assert sdk.sent, "nothing reached the transport"
    for report in sdk.sent:
        assert leaked(report) == []
        assert report["exception"]["values"][0]["type"] == "RuntimeError"
        assert all("vars" not in frame for frame in report["exception"]["values"][0]["stacktrace"]["frames"])


def test_logged_errors_do_not_leak_through_log_arguments(sdk):
    logging.getLogger("scan").error("scan of %s failed for %s", SECRETS[0], SECRETS[8])

    for report in sdk.sent:
        assert leaked(report) == []


def test_an_unhandled_error_in_a_request_reports_the_route_but_no_request_data(sdk, temp_db, monkeypatch):
    monkeypatch.setenv("SENTINELS_SESSION_SECRET", "test-signing-secret")
    from fastapi.testclient import TestClient

    import main

    @main.app.get("/boom/{scan_id}")
    def boom(scan_id: str):
        raise RuntimeError(f"cannot read {SECRETS[0]}")

    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("sentinels_session", SECRETS[7])
    response = client.get(
        f"/boom/{SECRETS[10]}", params={"url": SECRETS[0]}, headers={"Authorization": SECRETS[6]}
    )

    assert response.status_code == 500
    assert sdk.sent, "the unhandled error was not reported"
    for report in sdk.sent:
        assert leaked(report) == []
    request = next(r for r in sdk.sent if "request" in r)["request"]
    assert request == {"method": "GET", "url": "/boom/<id>"}


def test_a_broken_dsn_does_not_stop_the_api_from_starting(monkeypatch, caplog):
    import sentry_sdk

    def explode(**kwargs):
        raise ValueError("Unsupported scheme")

    monkeypatch.setenv("SENTRY_DSN", "not-a-dsn")
    monkeypatch.setattr(sentry_sdk, "init", explode)

    with caplog.at_level(logging.ERROR):
        assert init_error_tracking() is False
    assert "could not start" in caplog.text
