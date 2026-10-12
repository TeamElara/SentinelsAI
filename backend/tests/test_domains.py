"""Domain verification (Launch Plan gate 6): proving control of a host, and the
scan gate that needs it when `SENTINELS_REQUIRE_DOMAIN_VERIFICATION` is on.

No real DNS or HTTP: the TXT lookup is a fake, and the file check runs either
on an `httpx.MockTransport` or on the real scan client with a fake resolver,
so the outbound policy itself is what refuses a private address."""
from __future__ import annotations

from uuid import uuid4

import dns.exception
import dns.resolver
import httpx
import pytest

import domains
from net.client import make_scan_client
from tests.test_scan_permission import _as, _audit_rows, client, scans_started  # noqa: F401

TOKEN = "tok3n-value"


def _txt(records: list[str] | Exception):
    async def lookup(name: str) -> list[str]:
        lookup.asked.append(name)
        if isinstance(records, Exception):
            raise records
        return records

    lookup.asked = []
    return lookup


def _site(handler):
    """A client factory for check_file whose requests go to `handler`."""
    seen: list[str] = []

    def factory(**kwargs):
        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(wrapped), **kwargs)

    factory.seen = seen
    return factory


# --- Hosts ----------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    ["example.com", "Example.COM", "https://example.com/some/page?x=1", "http://example.com.", " example.com "],
)
def test_a_host_is_read_the_same_however_it_is_typed(typed):
    assert domains.claimable_host(typed) == "example.com"


@pytest.mark.parametrize(
    "typed, message",
    [
        ("", "Enter a domain name"),
        ("localhost", "full domain name"),
        ("93.184.216.34", "IP address"),
        ("[2606:2800:220:1::1]", "IP address"),
        ("example.com:8080", "ports 80 and 443"),
        ("ftp://example.com", "http:// and https://"),
        ("https://user:pw@example.com", "username or password"),
    ],
)
def test_what_cannot_be_verified_is_refused_with_a_reason(typed, message):
    with pytest.raises(ValueError, match=message):
        domains.claimable_host(typed)


def test_a_dns_proof_covers_names_under_the_host_and_a_file_proof_does_not():
    assert domains.covers("example.com", "dns", "example.com")
    assert domains.covers("example.com", "dns", "www.example.com")
    assert domains.covers("example.com", "file", "example.com")
    assert not domains.covers("example.com", "file", "www.example.com")
    # A suffix match on the text alone would let these through.
    assert not domains.covers("example.com", "dns", "badexample.com")
    assert not domains.covers("example.com", "dns", "example.com.evil.test")
    # Not verified yet.
    assert not domains.covers("example.com", None, "example.com")


# --- The two proofs -------------------------------------------------------


async def test_the_txt_record_proves_it():
    lookup = _txt(["v=spf1 -all", f"sentinels-verification={TOKEN}"])
    assert await domains.check_dns("example.com", TOKEN, lookup=lookup) is None
    assert lookup.asked == ["_sentinels.example.com"]


@pytest.mark.parametrize(
    "records, expected",
    [
        (["sentinels-verification=someone-elses-token"], "doesn't contain this token"),
        ([TOKEN], "doesn't contain this token"),  # the token without its prefix
        (dns.resolver.NXDOMAIN(), "No TXT record at _sentinels.example.com"),
        (dns.resolver.NoAnswer(), "No TXT record"),
        (dns.exception.Timeout(), "lookup for _sentinels.example.com failed"),
    ],
)
async def test_a_missing_or_wrong_txt_record_says_what_was_found(records, expected):
    problem = await domains.check_dns("example.com", TOKEN, lookup=_txt(records))
    assert expected in problem


async def test_the_file_proves_it():
    site = _site(lambda request: httpx.Response(200, text=f"{TOKEN}\n"))
    assert await domains.check_file("example.com", TOKEN, client_factory=site) is None
    assert site.seen == ["https://example.com/.well-known/sentinels-verification.txt"]


@pytest.mark.parametrize(
    "response, expected",
    [
        (httpx.Response(404), "answered 404"),
        (httpx.Response(200, text="some other text"), "doesn't contain this token"),
        (httpx.Response(200, text=f"x{TOKEN}x"), "doesn't contain this token"),
        (
            httpx.Response(301, headers={"location": "https://attacker.test/token.txt"}),
            "redirects elsewhere",
        ),
    ],
)
async def test_a_missing_or_wrong_file_says_what_was_found(response, expected):
    site = _site(lambda request: response)
    problem = await domains.check_file("example.com", TOKEN, client_factory=site)
    assert expected in problem
    # A redirect is reported, never followed.
    assert len(site.seen) == 1


async def test_a_token_past_the_read_limit_does_not_count():
    body = "x" * domains.FILE_READ_LIMIT + f"\n{TOKEN}\n"
    site = _site(lambda request: httpx.Response(200, text=body))
    assert "doesn't contain" in await domains.check_file("example.com", TOKEN, client_factory=site)


async def test_the_file_check_goes_through_the_outbound_policy():
    """A host whose DNS points at a private address is refused by the scan
    client itself, before anything is sent."""

    async def private(host: str) -> list[str]:
        return ["10.0.0.5"]

    def factory(**kwargs):
        return make_scan_client(resolver=private, **kwargs)

    problem = await domains.check_file("internal.example.com", TOKEN, client_factory=factory)
    assert "can't be checked" in problem
    assert "private" in problem


async def test_prove_tries_dns_then_the_file_and_reports_both_failures():
    site = _site(lambda request: httpx.Response(404))
    method, problems = await domains.prove(
        "example.com", TOKEN, lookup=_txt(dns.resolver.NXDOMAIN()), client_factory=site
    )
    assert method is None
    assert len(problems) == 2

    method, problems = await domains.prove(
        "example.com", TOKEN, lookup=_txt([f"sentinels-verification={TOKEN}"]), client_factory=site
    )
    assert (method, problems) == ("dns", [])
    assert site.seen == ["https://example.com/.well-known/sentinels-verification.txt"]  # only the first call

    ok = _site(lambda request: httpx.Response(200, text=TOKEN))
    method, _ = await domains.prove(
        "example.com", TOKEN, lookup=_txt(dns.resolver.NXDOMAIN()), client_factory=ok
    )
    assert method == "file"


# --- Routes ---------------------------------------------------------------


@pytest.fixture
def proof(monkeypatch):
    """What `domains.prove` answers, settable per test; records each call."""
    import main

    state = {"method": None, "problems": ["No TXT record.", "File answered 404."], "calls": []}

    async def fake_prove(host, token):
        state["calls"].append((host, token))
        return state["method"], list(state["problems"]) if state["method"] is None else []

    monkeypatch.setattr(main.domains, "prove", fake_prove)
    return state


def _no_cooldown(monkeypatch):
    monkeypatch.setattr("storage.domains.CHECK_COOLDOWN_SECONDS", -1)


def test_adding_a_domain_returns_both_ways_to_prove_it(client):
    _as(client)
    res = client.post("/domains", json={"host": "https://Example.com/path"})
    assert res.status_code == 200
    claim = res.json()
    assert claim["host"] == "example.com"
    assert claim["verified_at"] is None
    assert claim["dns_name"] == "_sentinels.example.com"
    assert claim["dns_value"] == f"sentinels-verification={claim['file_contents']}"
    assert claim["file_url"] == "https://example.com/.well-known/sentinels-verification.txt"
    assert len(claim["file_contents"]) >= 32

    listed = client.get("/domains").json()
    assert listed["required"] is False
    assert [d["host"] for d in listed["domains"]] == ["example.com"]


def test_adding_the_same_domain_again_keeps_its_token(client):
    _as(client)
    first = client.post("/domains", json={"host": "example.com"}).json()
    again = client.post("/domains", json={"host": "EXAMPLE.com"}).json()
    assert again["file_contents"] == first["file_contents"]
    assert len(client.get("/domains").json()["domains"]) == 1


def test_two_people_get_different_tokens_for_the_same_domain(client):
    _as(client, 1, "alice")
    alice = client.post("/domains", json={"host": "example.com"}).json()
    _as(client, 2, "bob")
    bob = client.post("/domains", json={"host": "example.com"}).json()
    assert alice["file_contents"] != bob["file_contents"]


def test_an_unverifiable_domain_is_refused(client):
    _as(client)
    res = client.post("/domains", json={"host": "localhost"})
    assert res.status_code == 400
    assert client.get("/domains").json()["domains"] == []


def test_there_is_a_limit_on_how_many_domains_one_person_adds(client, monkeypatch):
    monkeypatch.setattr("storage.domains.MAX_CLAIMS_PER_USER", 2)
    _as(client)
    assert client.post("/domains", json={"host": "a.example"}).status_code == 200
    assert client.post("/domains", json={"host": "b.example"}).status_code == 200
    res = client.post("/domains", json={"host": "c.example"})
    assert res.status_code == 400
    assert "up to" in res.json()["detail"]
    # The one already there can still be added again.
    assert client.post("/domains", json={"host": "a.example"}).status_code == 200


def test_a_failed_check_says_why_and_verifies_nothing(client, proof, monkeypatch):
    _no_cooldown(monkeypatch)
    _as(client)
    token = client.post("/domains", json={"host": "example.com"}).json()["file_contents"]

    res = client.post("/domains/example.com/check")

    assert res.status_code == 200
    assert res.json()["problems"] == ["No TXT record.", "File answered 404."]
    assert res.json()["domain"]["verified_at"] is None
    assert proof["calls"] == [("example.com", token)]
    assert [r["action"] for r in _audit_rows()] == []


def test_a_passing_check_verifies_the_domain_and_is_audited(client, proof, monkeypatch):
    _no_cooldown(monkeypatch)
    user = _as(client)
    client.post("/domains", json={"host": "example.com"})
    proof["method"] = "dns"

    res = client.post("/domains/example.com/check")

    assert res.status_code == 200
    body = res.json()
    assert body["problems"] == []
    assert body["domain"]["method"] == "dns"
    assert body["domain"]["verified_at"] is not None
    assert _audit_rows() == [
        {"user_id": user.id, "scan_id": None, "action": "domain_verified", "detail": "example.com (dns)"}
    ]
    # Checking a verified domain again sends nothing.
    client.post("/domains/example.com/check")
    assert len(proof["calls"]) == 1


def test_checks_are_spaced_out(client, proof):
    _as(client)
    client.post("/domains", json={"host": "example.com"})
    assert client.post("/domains/example.com/check").status_code == 200
    res = client.post("/domains/example.com/check")
    assert res.status_code == 429
    assert len(proof["calls"]) == 1


def test_checks_pause_with_scans(client, proof, monkeypatch):
    _as(client)
    client.post("/domains", json={"host": "example.com"})
    monkeypatch.setenv("SENTINELS_SCANS_PAUSED", "1")
    assert client.post("/domains/example.com/check").status_code == 503
    assert proof["calls"] == []


def test_someone_elses_domain_looks_the_same_as_one_nobody_added(client, proof, monkeypatch):
    _no_cooldown(monkeypatch)
    _as(client, 1, "alice")
    client.post("/domains", json={"host": "example.com"})

    _as(client, 2, "bob")
    for res in (
        client.post("/domains/example.com/check"),
        client.delete("/domains/example.com"),
        client.post("/domains/nobody.example/check"),
    ):
        assert res.status_code == 404
        assert res.json()["detail"] == "You haven't added that domain."
    assert client.get("/domains").json()["domains"] == []
    assert proof["calls"] == []

    _as(client, 1, "alice")
    assert [d["host"] for d in client.get("/domains").json()["domains"]] == ["example.com"]


def test_removing_a_domain(client):
    _as(client)
    client.post("/domains", json={"host": "example.com"})
    assert client.delete("/domains/Example.com").status_code == 204
    assert client.get("/domains").json()["domains"] == []
    assert client.delete("/domains/example.com").status_code == 404


def test_the_domain_routes_need_a_signed_in_user(client):
    for res in (
        client.get("/domains"),
        client.post("/domains", json={"host": "example.com"}),
        client.post("/domains/example.com/check"),
        client.delete("/domains/example.com"),
    ):
        assert res.status_code == 401


# --- The scan gate --------------------------------------------------------


def _verify(user_id: int, host: str, method: str) -> None:
    from storage import domains as claims

    claims.add_claim(user_id, host)
    claims.mark_verified(user_id, host, method)


def _scan(client, url):
    return client.post("/scan", json={"url": url, "permission_confirmed": True})


def _stream(client, url):
    return client.get(
        "/scan/stream",
        params={"url": url, "permission_confirmed": "true", "request_id": str(uuid4())},
    )


def test_with_the_gate_off_an_unverified_site_can_be_scanned(client, scans_started):
    _as(client)
    assert _scan(client, "https://example.com").status_code == 200
    assert scans_started == ["https://example.com"]


def test_with_the_gate_on_an_unverified_site_is_refused_and_nothing_starts(
    client, scans_started, monkeypatch
):
    monkeypatch.setenv("SENTINELS_REQUIRE_DOMAIN_VERIFICATION", "1")
    _as(client)

    for res in (_scan(client, "https://example.com"), _stream(client, "https://example.com")):
        assert res.status_code == 403
        assert "Verify that you control example.com" in res.json()["detail"]
    assert scans_started == []
    assert client.get("/domains").json()["required"] is True


def test_with_the_gate_on_a_verified_site_scans(client, scans_started, monkeypatch):
    monkeypatch.setenv("SENTINELS_REQUIRE_DOMAIN_VERIFICATION", "1")
    user = _as(client)
    _verify(user.id, "example.com", "dns")

    assert _scan(client, "https://EXAMPLE.com/login").status_code == 200
    assert _scan(client, "https://shop.example.com").status_code == 200
    assert _stream(client, "https://example.com").status_code == 200
    assert len(scans_started) == 3


def test_a_file_proof_lets_only_that_host_be_scanned(client, scans_started, monkeypatch):
    monkeypatch.setenv("SENTINELS_REQUIRE_DOMAIN_VERIFICATION", "1")
    user = _as(client)
    _verify(user.id, "example.com", "file")

    assert _scan(client, "https://example.com").status_code == 200
    assert _scan(client, "https://www.example.com").status_code == 403


def test_one_persons_proof_does_not_let_another_person_scan(client, scans_started, monkeypatch):
    monkeypatch.setenv("SENTINELS_REQUIRE_DOMAIN_VERIFICATION", "1")
    alice = _as(client, 1, "alice")
    _verify(alice.id, "example.com", "dns")

    _as(client, 2, "bob")
    assert _scan(client, "https://example.com").status_code == 403
    assert scans_started == []


def test_an_unverified_claim_does_not_open_the_gate(client, scans_started, monkeypatch):
    from storage import domains as claims

    monkeypatch.setenv("SENTINELS_REQUIRE_DOMAIN_VERIFICATION", "1")
    user = _as(client)
    claims.add_claim(user.id, "example.com")
    assert _scan(client, "https://example.com").status_code == 403


def test_a_repo_scan_is_not_gated(client, scans_started, monkeypatch):
    monkeypatch.setenv("SENTINELS_REQUIRE_DOMAIN_VERIFICATION", "1")
    _as(client)
    res = client.post("/repo/scan", json={"url": "https://github.com/octo/demo"})
    assert res.status_code == 200


# --- Storage --------------------------------------------------------------


def test_deleting_an_account_deletes_its_domains(client):
    from db import get_connection
    from storage.account import delete_account

    user = _as(client)
    _verify(user.id, "example.com", "dns")
    delete_account(user.id)

    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM domain_claims").fetchone()["n"] == 0
    finally:
        conn.close()
