"""Sentry reporting: off by default, and nothing about a scan target leaves."""
from __future__ import annotations

import json

import pytest

import observability
from observability import init_sentry, scrub_event, scrub_text

SECRET_BITS = [
    "victim-site.example",
    "10.0.0.5",
    "ghp_" + "a" * 36,
    "gsk_" + "b" * 40,
    "alice@corp.example",
    "BEGIN RSA PRIVATE KEY",
    "session=abc123",
    "hunter2",
]


def _dirty_event() -> dict:
    return {
        "message": "scan of https://victim-site.example/login?next=/admin failed",
        "user": {"id": "42", "ip_address": "1.2.3.4", "email": "alice@corp.example"},
        "server_name": "render-host-7",
        "extra": {"evidence": "hunter2 leaked on victim-site.example"},
        "contexts": {"runtime": {"name": "CPython"}},
        "breadcrumbs": {"values": [{"message": "GET https://victim-site.example/"}]},
        "tags": {"handled": "no", "target": "victim-site.example", "environment": "production"},
        "request": {
            "method": "GET",
            "url": "https://api.sentinels.test/scan/stream?url=https%3A%2F%2Fvictim-site.example",
            "query_string": "url=https://victim-site.example",
            "headers": {"Cookie": "session=abc123", "Authorization": "Bearer " + "x" * 30},
            "cookies": {"session": "abc123"},
            "data": {"url": "https://victim-site.example"},
        },
        "logentry": {
            "message": "fetch %s failed for %s",
            "formatted": "fetch https://victim-site.example failed for alice@corp.example",
            "params": ["https://victim-site.example", "10.0.0.5", {"nested": "obj"}, 7],
        },
        "exception": {
            "values": [
                {
                    "type": "ConnectError",
                    "value": (
                        "cannot reach victim-site.example at 10.0.0.5 using ghp_" + "a" * 36
                        + " and gsk_" + "b" * 40 + " -----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----"
                    ),
                    "stacktrace": {
                        "frames": [
                            {"filename": "agents/tls.py", "function": "run", "vars": {"host": "victim-site.example"}},
                        ]
                    },
                }
            ]
        },
    }


def test_scrubbed_event_contains_no_target_or_secret():
    out = scrub_event(_dirty_event())
    blob = json.dumps(out)
    for bit in SECRET_BITS:
        assert bit not in blob, bit


def test_scrubbed_event_keeps_what_is_needed_to_debug():
    out = scrub_event(_dirty_event())
    assert out["request"] == {"method": "GET", "url": "/scan/stream"}
    assert out["tags"] == {"handled": "no", "environment": "production"}
    value = out["exception"]["values"][0]
    assert value["type"] == "ConnectError"
    frame = value["stacktrace"]["frames"][0]
    assert frame["filename"] == "agents/tls.py" and frame["function"] == "run"
    assert "vars" not in frame
    assert out["logentry"]["params"] == ["[url]", "[ip]", "[removed]", 7]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("see https://a.example/x?y=1 now", "see [url] now"),
        ("host a.b.example failed", "host [host] failed"),
        ("edit main.py and package.json", "edit main.py and package.json"),
        ("Authorization: Bearer abcdefghij123456", "Authorization: Bearer [token]"),
        ("ipv6 ::1 and 2001:db8::1", "ipv6 [ip] and [ip]"),
    ],
)
def test_scrub_text(text, expected):
    assert scrub_text(text) == expected


def test_init_is_a_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry() is False


def test_init_uses_private_settings(monkeypatch):
    import sentry_sdk

    seen: dict = {}
    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/1")
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    assert init_sentry() is True
    assert seen["send_default_pii"] is False
    assert seen["max_request_body_size"] == "never"
    assert seen["include_local_variables"] is False
    assert seen["traces_sample_rate"] == 0
    assert seen["before_send"] is scrub_event
    assert seen["before_breadcrumb"]({"message": "x"}) is None
    assert seen["before_send_transaction"]({"type": "transaction"}) is None


def test_init_failure_does_not_stop_startup(monkeypatch):
    import sentry_sdk

    def boom(**_kw):
        raise RuntimeError("bad dsn")

    monkeypatch.setenv("SENTRY_DSN", "not-a-dsn")
    monkeypatch.setattr(sentry_sdk, "init", boom)
    assert init_sentry() is False


def test_real_sdk_sends_only_scrubbed_event(monkeypatch):
    """Drive the real SDK with a capturing transport, not just our function."""
    import sentry_sdk
    from sentry_sdk.transport import Transport

    sent: list = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.type == "event":
                    sent.append(item.payload.json)

    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/1")
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: real_init(transport=Capture(kw), **kw))
    try:
        assert init_sentry() is True
        try:
            # Built at runtime so the literals never sit in the source lines
            # the SDK attaches as context.
            target = "victim-" + "site.example"
            secret_local = "hunter" + "2"  # noqa: F841 - must not ship as a local variable
            raise ValueError("could not reach " + target + " at 10.0.0." + "5")
        except ValueError:
            sentry_sdk.capture_exception()
        sentry_sdk.flush()
    finally:
        # init() with no arguments falls back to SENTRY_DSN, so clear it first
        # or the SDK would be left live (with a real transport) for later tests.
        monkeypatch.delenv("SENTRY_DSN")
        real_init()

    assert len(sent) == 1
    blob = json.dumps(sent[0])
    for bit in ("victim-site.example", "10.0.0.5", "hunter2"):
        assert bit not in blob, bit
    assert sent[0]["exception"]["values"][0]["type"] == "ValueError"
    assert observability.scrub_text("x") == "x"
