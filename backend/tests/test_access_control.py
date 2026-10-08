"""Every route is accounted for, and every route that takes a scan id only works
for that scan's owner (Launch Plan A2).

`test_scan_ownership.py` checks A1's behaviour with a hand-written list of
routes, which is exactly the kind of list that goes stale: add a route, forget
the list, and nothing fails. This file walks `app.routes` instead. A new route
has to be put on one of the lists below, and any route whose path contains
`{scan_id}` is then tested for ownership automatically — there is no per-route
test to forget to write.

The four kinds of route:

* public     — no sign-in on purpose (`/health`, the agent lists, API docs)
* own auth   — the route runs the sign-in flow itself (`/auth/github/login`)
* signed in  — needs a session but isn't about one scan (`/scan`, `/scans`)
* scan route — any path with `{scan_id}`: must answer another user, an unowned
               scan and a missing scan with the same 404
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from auth.deps import current_user
from tests.test_main_audit import _signed_in_cookie
from tests.test_scan_ownership import SCAN_ID, _save_url_scan, client  # noqa: F401

# (METHOD, path) pairs exactly as FastAPI registers them.
PUBLIC = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/agents"),
    ("GET", "/repo/agents"),
    ("GET", "/export/formats"),
}
# FastAPI's own documentation routes. They aren't APIRoutes, so they're
# matched by path alone.
FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}

OWN_AUTH = {
    ("GET", "/auth/github/login"),
    ("GET", "/auth/github/callback"),
    ("GET", "/auth/github/install/callback"),
    ("POST", "/auth/logout"),
    ("POST", "/github/webhook"),     # authenticated by GitHub's HMAC signature
}

SIGNED_IN = {
    ("GET", "/auth/me"),
    ("GET", "/auth/github/install"),
    ("GET", "/installations"),
    ("POST", "/installations/{installation_id}/revoke"),
    ("GET", "/audit"),
    ("POST", "/scan"),
    ("POST", "/repo/scan"),
    ("GET", "/scan/stream"),
    ("GET", "/repo/stream"),
    ("GET", "/scans"),
}

# Values for path parameters other than `scan_id`. `format_id` has to be a
# real format: an unknown one is rejected before ownership is looked at.
PARAM_VALUES = {
    "scan_id": SCAN_ID,
    "agent_name": "headers",
    "item_key": "some-item",
    "finding_key": "missing-csp",
    "format_id": "json",
    "installation_id": "1",
}

# Request bodies for the scan routes that validate one. Validation runs before
# the handler, so without a valid body the ownership check is never reached.
ROUTE_BODIES = {
    ("POST", "/scans/{scan_id}/checklist/{item_key}"): {"state": "pass"},
    ("POST", "/scans/{scan_id}/fix/plan"): {"finding_keys": ["missing-csp"]},
    ("POST", "/scans/{scan_id}/fix/apply"): {"finding_keys": ["missing-csp"]},
    ("POST", "/scans/{scan_id}/chat"): {"question": "Is this site safe?"},
    ("POST", "/scans/{scan_id}/link-repo"): {"installation_id": 1, "repo": "demo"},
}


def _route_keys(route: APIRoute) -> list[tuple[str, str]]:
    return [(m, route.path) for m in sorted(route.methods - {"HEAD", "OPTIONS"})]


def _is_scan_route(path: str) -> bool:
    return "{scan_id}" in path


def _depends_on_current_user(route: APIRoute) -> bool:
    def walk(dependant) -> bool:
        return any(d.call is current_user or walk(d) for d in dependant.dependencies)

    return walk(route.dependant)


def unclassified_routes(app: FastAPI) -> list[str]:
    """Routes that aren't on any list, and list entries that no longer exist."""
    classified = PUBLIC | OWN_AUTH | SIGNED_IN
    seen: set[tuple[str, str]] = set()
    problems: list[str] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            if route.path not in FRAMEWORK_PATHS:
                problems.append(f"unlisted non-API route {route.path}")
            continue
        for key in _route_keys(route):
            seen.add(key)
            if _is_scan_route(route.path):
                if key in classified:
                    problems.append(f"{key} has {{scan_id}} but is listed as not owner-checked")
            elif key not in classified:
                problems.append(f"{key} is on no list: add it to PUBLIC, OWN_AUTH or SIGNED_IN")

    for key in sorted((classified | set(ROUTE_BODIES)) - seen):
        problems.append(f"{key} is listed but no such route exists any more")
    return problems


def _url(path: str) -> str:
    return path.format(**PARAM_VALUES)


def _scan_routes(app: FastAPI) -> list[tuple[APIRoute, str]]:
    return [
        (route, method)
        for route in app.routes
        if isinstance(route, APIRoute) and _is_scan_route(route.path)
        for method, _ in _route_keys(route)
    ]


def _call(client, method: str, path: str):
    return client.request(method, _url(path), json=ROUTE_BODIES.get((method, path)))


def _not_found_detail() -> str:
    return f"Scan {SCAN_ID!r} not found"


# --- The checker itself ---------------------------------------------------


def test_the_checker_flags_a_new_route_that_is_on_no_list():
    app = FastAPI()

    @app.get("/scans/{scan_id}/brand-new")
    def brand_new(scan_id: str):  # pragma: no cover - never called
        ...

    @app.get("/reports/latest")
    def latest():  # pragma: no cover - never called
        ...

    problems = unclassified_routes(app)
    assert any("/reports/latest" in p for p in problems)
    # Scan routes aren't listed anywhere on purpose; the behaviour tests below
    # are what cover them, so they are not "unlisted".
    assert not any("brand-new" in p for p in problems)


def test_the_checker_flags_a_scan_route_listed_as_not_owner_checked():
    app = FastAPI()

    @app.get("/health")
    def health():  # pragma: no cover - never called
        ...

    @app.get("/scans/{scan_id}/oops")
    def oops(scan_id: str):  # pragma: no cover - never called
        ...

    PUBLIC.add(("GET", "/scans/{scan_id}/oops"))
    try:
        problems = unclassified_routes(app)
    finally:
        PUBLIC.discard(("GET", "/scans/{scan_id}/oops"))
    assert any("oops" in p and "owner-checked" in p for p in problems)


# --- The real app ---------------------------------------------------------


def test_every_route_in_the_app_is_accounted_for(client):
    assert unclassified_routes(client.app) == []


def test_every_route_except_the_public_ones_needs_a_signed_in_user(client):
    exempt = PUBLIC | OWN_AUTH
    missing = [
        f"{method} {route.path}"
        for route in client.app.routes
        if isinstance(route, APIRoute)
        for method, _ in _route_keys(route)
        if (method, route.path) not in exempt and not _depends_on_current_user(route)
    ]
    assert missing == [], f"these routes don't depend on current_user: {missing}"


def test_every_scan_route_that_validates_a_body_has_one_on_file(client):
    missing = [
        f"{method} {route.path}"
        for route, method in _scan_routes(client.app)
        if route.body_field is not None and (method, route.path) not in ROUTE_BODIES
    ]
    assert missing == [], f"add a request body to ROUTE_BODIES for: {missing}"


def test_there_is_at_least_one_scan_route_to_test(client):
    # Guards the loops below against passing because they found nothing.
    assert len(_scan_routes(client.app)) >= 20


def test_every_scan_route_answers_another_user_with_the_same_404(client):
    alice, _ = _signed_in_cookie(1, "alice")
    _, bob_cookie = _signed_in_cookie(2, "bob")
    _save_url_scan(SCAN_ID, alice.id)
    client.cookies.set("sentinels_session", bob_cookie)

    wrong = []
    for route, method in _scan_routes(client.app):
        res = _call(client, method, route.path)
        if res.status_code != 404 or res.json().get("detail") != _not_found_detail():
            wrong.append(f"{method} {route.path} -> {res.status_code} {res.text[:80]}")
    assert wrong == []


def test_every_scan_route_is_closed_on_an_unowned_legacy_scan(client):
    _, cookie = _signed_in_cookie(1, "alice")
    _save_url_scan(SCAN_ID, None)
    client.cookies.set("sentinels_session", cookie)

    wrong = []
    for route, method in _scan_routes(client.app):
        res = _call(client, method, route.path)
        if res.status_code != 404 or res.json().get("detail") != _not_found_detail():
            wrong.append(f"{method} {route.path} -> {res.status_code} {res.text[:80]}")
    assert wrong == []


def test_every_scan_route_answers_a_missing_scan_with_the_same_404(client):
    _, cookie = _signed_in_cookie(1, "alice")
    client.cookies.set("sentinels_session", cookie)

    wrong = []
    for route, method in _scan_routes(client.app):
        res = _call(client, method, route.path)
        if res.status_code != 404 or res.json().get("detail") != _not_found_detail():
            wrong.append(f"{method} {route.path} -> {res.status_code} {res.text[:80]}")
    assert wrong == []


def test_another_user_s_requests_changed_nothing(client):
    """The routes above answer 404 — and must also have done no work first."""
    from storage.scans import get_scan

    alice, _ = _signed_in_cookie(1, "alice")
    _, bob_cookie = _signed_in_cookie(2, "bob")
    _save_url_scan(SCAN_ID, alice.id)
    before = get_scan(SCAN_ID)
    client.cookies.set("sentinels_session", bob_cookie)

    for route, method in _scan_routes(client.app):
        _call(client, method, route.path)

    assert get_scan(SCAN_ID) == before


def test_every_signed_in_and_scan_route_returns_401_without_a_session(client):
    _save_url_scan(SCAN_ID, None)

    wrong = []
    targets = [(m, p) for m, p in sorted(SIGNED_IN)] + [
        (method, route.path) for route, method in _scan_routes(client.app)
    ]
    for method, path in targets:
        res = client.request(method, _url(path), json=ROUTE_BODIES.get((method, path), {}))
        if res.status_code != 401:
            wrong.append(f"{method} {path} -> {res.status_code}")
    assert wrong == []
