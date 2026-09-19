"""Sentinels API — the FastAPI application object.

This module owns `app`. Uvicorn imports it by the string "main:app" and drives
it; nothing here opens a socket or listens on a port itself.

Run locally:
    .venv/Scripts/python.exe -m uvicorn main:app --reload
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import secrets

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

# Loads backend/.env into the process's environment (GROQ_API_KEY, if
# present) once, at startup — so ai/analyst.py's os.environ.get() call later,
# at request time, sees it without every module that wants an env var
# needing to load .env itself.
#
# Explicit path, not bare load_dotenv(): with no argument, python-dotenv
# searches the *current working directory* and upward for a .env — and the
# working directory uvicorn is launched from (e.g. the repo root, per
# .claude/launch.json) is not necessarily this file's own directory. Search
# never goes *into* a subdirectory, so a bare call silently finds nothing and
# every env-gated feature (sign-in, autofix) fails with "not configured"
# despite backend/.env being filled in correctly.
load_dotenv(Path(__file__).parent / ".env")

from agents.registry import list_agents  # noqa: E402
from agents.repo_registry import list_repo_agents  # noqa: E402
from ai.chat import answer as chat_answer  # noqa: E402
from ai.client import get_api_key  # noqa: E402
from ai.fixes import get_or_generate_fix  # noqa: E402
from ai.prompts import PROMPT_VERSION  # noqa: E402
from auth.deps import current_user, optional_user  # noqa: E402
from auth.github_oauth import (  # noqa: E402
    authorize_url,
    exchange_code,
    fetch_identity,
    get_app_slug,
    get_frontend_origin,
    install_url,
    missing_settings,
    oauth_configured,
)
from auth.session import (  # noqa: E402
    COOKIE_NAME,
    SESSION_TTL,
    cookie_value,
    get_session_secret,
    hash_token,
    new_token,
    session_expiry,
    token_from_cookie,
)
from db import init_db  # noqa: E402
from models import AgentInfo, AgentResult, AuditLogEntry, ChatMessage, ChecklistItem, FixApplication, FixApplyPreview, FixPlan, FixSuggestion, FixSummary, GitHubInstallation, RepoFileEntry, ScanReport, ScanRepoLink, ScanRequest, ScanSummary, User, VerificationResult  # noqa: E402
from orchestrator import run_scan, run_scan_stream  # noqa: E402
from remediation.apply import ApplyError, apply_fixes, refresh_applications  # noqa: E402
from remediation.patch import PlanValidationError  # noqa: E402
from remediation.planning import NotARepoScan, build_bundle_zip, plan_and_save, preview_plan  # noqa: E402
from remediation.registry import fixable_findings  # noqa: E402
from remediation.tokens import fetch_installation  # noqa: E402
from remediation.verify import VerifyError, verify_finding  # noqa: E402
from repo_orchestrator import run_repo_scan, run_repo_scan_stream  # noqa: E402
from report.pdf import generate_pdf  # noqa: E402
from report.registry import get_exporter, list_formats  # noqa: E402
from storage.chat import load_messages  # noqa: E402
from storage.fixes import load_fixes_for_scan  # noqa: E402
from storage.installations import list_installations, revoke_installation, save_installation  # noqa: E402
from storage.scan_links import delete_scan_repo_link, get_scan_repo_link, save_scan_repo_link  # noqa: E402
from storage.remediation import list_audit, list_audit_for_user  # noqa: E402
from storage.repo_files import get_repo_files  # noqa: E402
from storage.scans import delete_scan, get_scan, list_scans, scan_owner, update_checklist_item  # noqa: E402
from storage.users import delete_session, sign_in  # noqa: E402

# Creates backend/data/sentinels.db and brings its schema up to date if it
# isn't already — safe to call on every startup (see db.init_db's docstring).
init_db()

VERSION = "0.1.0"

app = FastAPI(
    title="Sentinels",
    description="Passive website security auditor. Read-only checks only.",
    version=VERSION,
)

# The browser treats http://localhost:3000 (the Next.js frontend) and
# http://localhost:8000 (this API) as different origins — same host, different
# port is enough — and by default refuses to let JavaScript on one read a
# response from the other. This middleware sends the headers that grant that
# permission explicitly.
#
# Listing the two dev origins rather than allow_origins=["*"] is deliberate:
# a wildcard would let a page on *any* site drive this API using the visitor's
# machine as the source of the scan traffic. For a tool that makes outbound
# requests to third-party sites, that's a genuinely bad default to ship.
#
# allow_credentials=True (PLAN-v5 Stage 0) is what lets the browser attach the
# session cookie to a cross-port request at all — without it the cookie is
# silently dropped and every protected route looks like it's rejecting a
# signed-in user. The two are a package deal in the CORS spec: a wildcard
# origin and allow_credentials cannot be combined, which is one more reason
# the explicit two-origin list above was already the right call.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
    allow_credentials=True,
)

# Cookie flags shared by both the login and logout responses, so the two can
# never drift apart — a cookie set with one set of flags and cleared with
# another can end up not actually clearing (the browser treats "same name,
# different path/attrs" as a different cookie).
_COOKIE_KWARGS = dict(
    key=COOKIE_NAME,
    httponly=True,       # invisible to page JavaScript — an XSS bug can't read it
    samesite="lax",      # sent on top-level navigation, not on cross-site POSTs
    path="/",
)


@app.get("/")
def root() -> dict:
    """Signpost for anyone who opens the bare host, so it isn't a blank 404."""
    return {"service": "Sentinels", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health() -> dict:
    """Liveness check.

    Deliberately does no work — no network calls, no disk, no agents. Its only
    job is to answer "is this process up and routing?". If it ever gets slow,
    a monitor watching it can no longer tell "server down" from "server busy".
    """
    return {
        "status": "ok",
        "service": "Sentinels",
        "version": VERSION,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/auth/github/login")
def auth_login(request: Request) -> RedirectResponse:
    """Send the browser to GitHub to sign in.

    The `state` value is round-tripped through the cookie itself rather than
    server-side session storage — there's no session yet to store it in, this
    is how one gets created. It's a plain httponly cookie, short-lived enough
    (10 minutes) that it only ever needs to survive the trip to github.com and
    back.
    """
    if not oauth_configured():
        raise HTTPException(
            status_code=503,
            detail=f"GitHub sign-in is not configured. Missing: {', '.join(missing_settings())}.",
        )
    state = secrets.token_urlsafe(24)
    redirect_uri = str(request.url_for("auth_callback"))
    response = RedirectResponse(authorize_url(state, redirect_uri))
    response.set_cookie(
        "sentinels_oauth_state", state, httponly=True, samesite="lax", max_age=600, path="/",
    )
    return response


@app.get("/auth/github/callback")
async def auth_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    """GitHub's landing point after the user approves (or denies) sign-in.

    Trades `code` for a token, reads the identity it belongs to, opens a
    session, and sends the browser back to the app with the session cookie
    set. Every failure path redirects to the frontend's login page with a
    `?error=` instead of showing a bare API error page — the user is on a
    browser tab, not a curl session.
    """
    frontend = get_frontend_origin()
    expected_state = request.cookies.get("sentinels_oauth_state")

    def _fail(reason: str) -> RedirectResponse:
        return RedirectResponse(f"{frontend}/login?error={reason}")

    if not expected_state or state != expected_state:
        return _fail("state_mismatch")
    if not code:
        return _fail("missing_code")

    secret = get_session_secret()
    if secret is None:
        return _fail("server_not_configured")

    redirect_uri = str(request.url_for("auth_callback"))
    token = await exchange_code(code, redirect_uri)
    if token is None:
        return _fail("exchange_failed")

    identity = await fetch_identity(token)
    if identity is None:
        return _fail("identity_failed")

    session_token = new_token()
    sign_in(
        github_id=identity.github_id,
        github_login=identity.login,
        avatar_url=identity.avatar_url,
        token_hash=hash_token(session_token),
        expires_at=session_expiry(),
    )

    response = RedirectResponse(frontend)
    response.delete_cookie("sentinels_oauth_state", path="/")
    response.set_cookie(
        value=cookie_value(session_token, secret),
        max_age=int(SESSION_TTL.total_seconds()),
        **_COOKIE_KWARGS,
    )
    return response


@app.get("/auth/me", response_model=User)
def auth_me(user: User = Depends(current_user)) -> User:
    """Who the current session cookie belongs to."""
    return user


@app.post("/auth/logout")
def auth_logout(request: Request) -> Response:
    """Revoke the current session and clear its cookie.

    Deletes the session row (not just the cookie) so the exact bytes sitting
    in a browser's cookie jar stop working immediately, rather than merely
    being politely asked not to send them again.
    """
    secret = get_session_secret()
    raw = request.cookies.get(COOKIE_NAME)
    if raw and secret:
        token = token_from_cookie(raw, secret)
        if token:
            delete_session(hash_token(token))

    response = Response(status_code=204)
    response.delete_cookie(**_COOKIE_KWARGS)
    return response


@app.get("/auth/github/install")
def auth_install(user: User = Depends(current_user)) -> RedirectResponse:
    """Send a signed-in user to GitHub to install the App on their account.

    Requires a session, unlike `/auth/github/login`: the callback has to know
    which Sentinels user the resulting installation belongs to, and there is
    nobody to attribute it to if the browser arrives here anonymous.
    """
    if get_app_slug() is None:
        raise HTTPException(
            status_code=503,
            detail="Repository access is not configured. Missing: GITHUB_APP_SLUG.",
        )
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(install_url(state))
    response.set_cookie(
        "sentinels_install_state", state, httponly=True, samesite="lax", max_age=600, path="/",
    )
    return response


@app.get("/auth/github/install/callback")
async def auth_install_callback(
    request: Request,
    installation_id: int = 0,
    setup_action: str = "",
    state: str = "",
) -> RedirectResponse:
    """GitHub's landing point after the user installs (or configures) the App.

    There is no code-for-token exchange here, and none is needed to trust the
    result: GitHub only redirects to this URL after the person signed in *on
    github.com* completed the install screen for that installation, and the
    `state` cookie ties that redirect to this Sentinels session. What the
    callback still has to do is ask GitHub *which account* the installation
    covers — the redirect carries only a number, and `account_login` is what
    every later write check compares against.
    """
    frontend = get_frontend_origin()
    expected_state = request.cookies.get("sentinels_install_state")

    def _fail(reason: str) -> RedirectResponse:
        response = RedirectResponse(f"{frontend}/settings?install_error={reason}")
        response.delete_cookie("sentinels_install_state", path="/")
        return response

    if not expected_state or not state or state != expected_state:
        return _fail("state_mismatch")
    if not installation_id:
        return _fail("missing_installation")

    user = optional_user(request)
    if user is None:
        return _fail("not_signed_in")

    async with httpx.AsyncClient(timeout=10.0) as client:
        metadata = await fetch_installation(client, installation_id)
    if metadata is None:
        return _fail("installation_lookup_failed")

    account = (metadata.get("account") or {}).get("login")
    if not isinstance(account, str) or not account:
        return _fail("installation_lookup_failed")

    save_installation(
        user_id=user.id,
        installation_id=installation_id,
        account_login=account,
        repo_selection=metadata.get("repository_selection") or "selected",
        permissions=metadata.get("permissions") or {},
    )

    response = RedirectResponse(f"{frontend}/settings?installed={account}")
    response.delete_cookie("sentinels_install_state", path="/")
    return response


@app.get("/installations", response_model=list[GitHubInstallation])
def installations_list(user: User = Depends(current_user)) -> list[GitHubInstallation]:
    """The repository-write grants this user currently holds."""
    return list_installations(user.id)


@app.post("/installations/{installation_id}/revoke")
def installations_revoke(
    installation_id: int, user: User = Depends(current_user)
) -> Response:
    """Stop Sentinels using one installation. 404 if the caller has no live
    grant with that id — including when it belongs to somebody else, which is
    deliberately indistinguishable from "no such installation".

    This is only half a revocation, and the response says so: the App stays
    installed on GitHub until the user removes it there. Sentinels can only
    promise to stop using it, which is what this row now records.
    """
    if not revoke_installation(user.id, installation_id):
        raise HTTPException(status_code=404, detail="No active installation with that id.")
    return Response(status_code=204)


@app.get("/audit", response_model=list[AuditLogEntry])
def audit_list(limit: int = 100, user: User = Depends(current_user)) -> list[AuditLogEntry]:
    """This account's recent remediation history, across every scan (PLAN-v5
    Stage E) -- the rows `write_audit` has been recording since Stage B,
    surfaced for the first time anywhere wider than a direct query.
    """
    return [AuditLogEntry(**row) for row in list_audit_for_user(user.id, limit=limit)]


class LinkRepoRequest(BaseModel):
    installation_id: int
    repo: str
    ref: Optional[str] = None


@app.post("/scans/{scan_id}/link-repo", response_model=ScanRepoLink)
def scan_link_repo(
    scan_id: str, body: LinkRepoRequest, user: User = Depends(current_user)
) -> ScanRepoLink:
    """Link a URL scan to the repository that serves its site (PLAN-v5 Stage D)
    -- the bridge a header finding needs, since it has no repository of its
    own to patch.

    `owner` always comes from the installation's own `account_login`, never
    typed by the caller, so a link can never point at an account they hold
    no grant on -- the same shape invariant #4 already enforces for applying
    a fix.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    if report.target_type != "url":
        raise HTTPException(
            status_code=400, detail="Only a URL scan can be linked to a repository."
        )
    if scan_owner(scan_id) != user.id:
        raise HTTPException(status_code=403, detail="This scan belongs to another user.")

    installation = next(
        (i for i in list_installations(user.id) if i.installation_id == body.installation_id),
        None,
    )
    if installation is None:
        raise HTTPException(
            status_code=403, detail="No active installation with that id."
        )

    return save_scan_repo_link(
        scan_id, user.id, installation.installation_id, installation.account_login,
        body.repo, body.ref,
    )


@app.get("/scans/{scan_id}/link-repo", response_model=Optional[ScanRepoLink])
def scan_get_link(scan_id: str, user: User = Depends(current_user)) -> ScanRepoLink | None:
    """The repository currently linked to this scan, or `null`."""
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    link = get_scan_repo_link(scan_id)
    if link is None or link.user_id != user.id:
        return None
    return link


@app.delete("/scans/{scan_id}/link-repo")
def scan_unlink_repo(scan_id: str, user: User = Depends(current_user)) -> Response:
    """Unlink a scan's repository. 404 if there was nothing to unlink,
    including a link that belongs to somebody else."""
    if not delete_scan_repo_link(scan_id, user.id):
        raise HTTPException(status_code=404, detail="No linked repository to remove.")
    return Response(status_code=204)


@app.post("/scan", response_model=ScanReport)
async def scan(request: ScanRequest, user: User = Depends(current_user)) -> ScanReport:
    """Run a full scan against `request.url` and return the report.

    `async def` here (unlike `/health`'s plain `def`) because this endpoint
    genuinely awaits something — `run_scan` awaits real HTTP requests inside
    the agents it calls.
    """
    try:
        return await run_scan(request.url, user_id=user.id)
    except ValueError as exc:
        # normalize_url's complaints (empty string, bad scheme, no host) are
        # the client's fault, not the server's — 400, not a 500 crash.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/repo/scan", response_model=ScanReport)
async def repo_scan(request: ScanRequest, user: User = Depends(current_user)) -> ScanReport:
    """Run a full scan against a public GitHub repo (`request.url`) and
    return the report. The repo-side sibling of `POST /scan` -- same
    request/response shape, same 400-on-ValueError contract, a different
    orchestrator underneath (`repo_orchestrator.run_repo_scan`).

    The live streaming variant (`GET /repo/stream`, mirroring
    `GET /scan/stream`) and the frontend launcher that calls it are R11's
    job (PLAN-v3, Phase R-D); this endpoint exists now so a repo scan is
    independently reachable and verifiable over real HTTP, the same way
    every other milestone in this codebase has been.
    """
    try:
        return await run_repo_scan(request.url, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sse(event: str, data: str) -> str:
    """Format one Server-Sent Events message. `\\n\\n` is the wire format's
    own message terminator — the browser's EventSource won't deliver a
    message until it sees the blank line after it."""
    return f"event: {event}\ndata: {data}\n\n"


@app.get("/scan/stream")
async def scan_stream(url: str, user: User = Depends(current_user)) -> StreamingResponse:
    """Same scan as `POST /scan`, reported as it happens instead of all at
    once. Server-Sent Events, not JSON — a one-way, GET-only, plain-text
    streaming protocol the browser understands natively via `EventSource`,
    which is why this takes `url` as a query parameter instead of a JSON
    body the way `POST /scan` does: `EventSource` can only issue GET.

    Emits one `event: agent` per finished agent (real completion order, not
    `AGENTS`' declared order), then one `event: done` carrying the complete
    `ScanReport`. A bad URL can't become a `400` the way it does for
    `POST /scan` — once the first byte of a streaming response has gone out,
    the status code (200) is already committed — so it's reported as
    `event: failed` instead, a message *inside* the otherwise-successful
    stream.

    `Depends(current_user)` runs — and can 401 — before this function body
    starts, so an unauthenticated `EventSource` never gets as far as opening
    the stream.
    """

    async def events():
        try:
            async for event_name, payload in run_scan_stream(url, user_id=user.id):
                yield _sse(event_name, payload.model_dump_json())
        except ValueError as exc:
            yield _sse("failed", json.dumps({"detail": str(exc)}))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/repo/stream")
async def repo_scan_stream(url: str, user: User = Depends(current_user)) -> StreamingResponse:
    """Same repo scan as `POST /repo/scan`, reported as it happens instead of
    all at once. The repo-side sibling of `GET /scan/stream` -- same SSE
    shape (`event: agent` per finished agent, one `event: done` at the end,
    a bad URL reported in-band as `event: failed` since the 200 is already
    committed by the time it's known), a different generator underneath
    (`repo_orchestrator.run_repo_scan_stream`).
    """

    async def events():
        try:
            async for event_name, payload in run_repo_scan_stream(url, user_id=user.id):
                yield _sse(event_name, payload.model_dump_json())
        except ValueError as exc:
            yield _sse("failed", json.dumps({"detail": str(exc)}))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/agents", response_model=list[AgentInfo])
def agents_list() -> list[AgentInfo]:
    """Return metadata for every registered scanner agent."""
    return list_agents()


@app.get("/repo/agents", response_model=list[AgentInfo])
def repo_agents_list() -> list[AgentInfo]:
    """Return metadata for every registered repo-scanner agent."""
    return list_repo_agents()


@app.get("/scans", response_model=list[ScanSummary])
def scans_list(
    limit: int = 20, offset: int = 0, user: User = Depends(current_user)
) -> list[ScanSummary]:
    """List stored scans, newest first. Paginate with `limit` and `offset`.

    Scoped to the caller's own scans plus every unowned (pre-Stage-0) one —
    see `storage.scans.list_scans`'s docstring for why unowned scans stay
    visible rather than disappearing after an upgrade.
    """
    limit = min(max(limit, 1), 100)
    return list_scans(limit=limit, offset=offset, user_id=user.id)


@app.get("/scans/{scan_id}", response_model=ScanReport)
def scans_get(scan_id: str, user: User = Depends(current_user)) -> ScanReport:
    """Return the full stored ScanReport for `scan_id`."""
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    return report


@app.get("/scans/{scan_id}/agents/{agent_name}", response_model=AgentResult)
def scans_agent_get(scan_id: str, agent_name: str, user: User = Depends(current_user)) -> AgentResult:
    """Return one agent's result slice from a stored scan.

    Used by the per-agent detail page so it only fetches what it needs instead
    of the full report. Returns 404 if either the scan or the agent is missing.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    for result in report.agents:
        if result.agent == agent_name:
            return result
    raise HTTPException(
        status_code=404,
        detail=f"Agent {agent_name!r} not found in scan {scan_id!r}",
    )


@app.get("/scans/{scan_id}/files", response_model=list[RepoFileEntry])
def scans_files_get(scan_id: str, user: User = Depends(current_user)) -> list[RepoFileEntry]:
    """Return the file tree (path, size, language, finding count) for a repo
    scan. Empty for a URL scan — `target_type` is what the frontend checks
    before ever calling this, but an empty list is also a perfectly valid
    answer on its own, not an error.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    return get_repo_files(scan_id)


@app.delete("/scans/{scan_id}")
def scans_delete(scan_id: str, user: User = Depends(current_user)) -> Response:
    """Delete a scan and all its findings. Returns 204 on success, 404 if not found.

    Ownership is checked here, not just sign-in: deletion is destructive and
    permanent, so a scan someone else owns returns 403 rather than being
    silently deletable by anyone who is merely signed in. Unowned (legacy)
    scans have no owner to protect and stay deletable by any signed-in user,
    matching how `list_scans` already treats them as shared.
    """
    owner = scan_owner(scan_id)
    if owner is not None and owner != user.id:
        raise HTTPException(status_code=403, detail="This scan belongs to another user.")
    if not delete_scan(scan_id):
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    return Response(status_code=204)


@app.get("/scans/{scan_id}/checklist", response_model=list[ChecklistItem])
def checklist_get(scan_id: str, user: User = Depends(current_user)) -> list[ChecklistItem]:
    """Return the deployment checklist for a stored scan."""
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    return report.checklist


class ChecklistAnswer(BaseModel):
    state: str          # "pass" | "fail"
    explanation: str = ""


@app.post("/scans/{scan_id}/checklist/{item_key}", response_model=ChecklistItem)
def checklist_answer(
    scan_id: str, item_key: str, body: ChecklistAnswer, user: User = Depends(current_user)
) -> ChecklistItem:
    """Update a self-attested checklist item's state.

    Only self_attested items are writable — auto and inferred items are computed
    from findings and cannot be overridden here.
    """
    if body.state not in ("pass", "fail"):
        raise HTTPException(status_code=422, detail="state must be 'pass' or 'fail'")

    explanation = body.explanation or (
        "Confirmed as done." if body.state == "pass" else "Marked as not done."
    )
    updated = update_checklist_item(scan_id, item_key, body.state, explanation)
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=f"Self-attested item {item_key!r} not found in scan {scan_id!r}",
        )

    # Return the updated item from the DB
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    for item in report.checklist:
        if item.item_key == item_key:
            return item
    raise HTTPException(status_code=404, detail=f"Item {item_key!r} not found")


@app.post("/scans/{scan_id}/findings/{finding_key}/fix", response_model=FixSuggestion)
async def finding_fix(
    scan_id: str, finding_key: str, regenerate: bool = False, user: User = Depends(current_user)
) -> FixSuggestion:
    """Return an AI-generated fix suggestion for one finding (cached).

    First call is a live LLM request (~3-8 s). Subsequent calls return the
    cached result instantly. `?regenerate=true` forces a fresh LLM call.
    With no GROQ_API_KEY: returns 503 with a clear message, never a 500.
    """
    if not get_api_key():
        raise HTTPException(status_code=503, detail="AI fix suggestions require GROQ_API_KEY.")

    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")

    finding = next((f for f in report.findings if f.id == finding_key), None)
    if finding is None:
        raise HTTPException(
            status_code=404,
            detail=f"Finding {finding_key!r} not found in scan {scan_id!r}",
        )

    suggestion = await get_or_generate_fix(scan_id, finding_key, finding, regenerate=regenerate)
    if suggestion is None:
        raise HTTPException(status_code=503, detail="Fix suggestion generation failed. Try again.")
    return suggestion


@app.get("/scans/{scan_id}/fix/summary", response_model=FixSummary)
def scan_fix_summary(scan_id: str, user: User = Depends(current_user)) -> FixSummary:
    """How many of this scan's current findings have a deterministic Fixer --
    what the scan overview page's "N fixes available" badge reads.

    Deliberately cheap: `fixable_findings` only calls each Fixer's
    `handles()`, a pure string check with no network involved, so this never
    re-reads the repository the way previewing an actual plan does. That
    also means it can say "might be fixable" but not "is fixable right
    now" -- a Fixer can still find nothing left to do once it reads the
    repo's current state, which is what `GET .../fix/plan` is for.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")

    candidates = fixable_findings(report.findings)
    first = candidates[0] if candidates else None
    return FixSummary(
        fixable_count=len(candidates),
        first_finding_key=first.id if first else None,
        first_agent=first.agent if first else None,
    )


@app.get("/scans/{scan_id}/findings/{finding_key}/fix/plan", response_model=Optional[FixPlan])
async def finding_fix_plan(
    scan_id: str, finding_key: str, user: User = Depends(current_user)
) -> FixPlan | None:
    """Preview a deterministic fix for one finding (PLAN-v5 Stage A) --
    computed live against the repo's current GitHub state, never persisted.

    `null` means there's no deterministic Fixer for this finding (only tier
    1/2 findings ever have one) -- a normal answer, not an error; the
    frontend falls back to the existing AI `FixSuggestionPanel`.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    try:
        return await preview_plan(report, finding_key)
    except NotARepoScan as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class FixPlanRequest(BaseModel):
    finding_keys: list[str]


class FixPlanResult(BaseModel):
    finding_key: str
    plan: Optional[FixPlan] = None
    fixable: bool = False


@app.post("/scans/{scan_id}/fix/plan", response_model=list[FixPlanResult])
async def scan_fix_plan(
    scan_id: str, body: FixPlanRequest, user: User = Depends(current_user)
) -> list[FixPlanResult]:
    """Plan and persist a deterministic fix for each requested finding
    (PLAN-v5 Stage A). Every key gets a result even when it isn't fixable
    (`fixable=False`, `plan=null`) -- one unfixable finding in the batch is
    never a reason to fail the whole request.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    if not body.finding_keys:
        raise HTTPException(status_code=422, detail="finding_keys must not be empty")

    try:
        results = await plan_and_save(report, body.finding_keys)
    except NotARepoScan as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return [
        FixPlanResult(finding_key=key, plan=plan, fixable=plan is not None)
        for key, plan in results.items()
    ]


@app.get("/scans/{scan_id}/fix/bundle.zip")
def scan_fix_bundle(scan_id: str, user: User = Depends(current_user)) -> Response:
    """Download every planned fix for a scan as one zip of unified diffs --
    for applying fixes by hand instead of going through Stage B's PR flow
    (not implemented yet). 404 if nothing has been planned via
    `POST /scans/{id}/fix/plan` yet.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")

    bundle = build_bundle_zip(scan_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="No fix plans have been generated for this scan yet.")

    return Response(
        content=bundle,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="sentinels-fixes-{scan_id[:8]}.zip"'},
    )


class FixApplyRequest(BaseModel):
    finding_keys: list[str]
    # Defaults to a dry run on purpose. A request that forgets the flag
    # previews; it never pushes. The dangerous option has to be typed.
    dry_run: bool = True


# `response_model=None` because this endpoint returns one of two shapes and
# they overlap: every field `FixApplyResult` requires also exists on
# `FixApplyPreview`, so a declared union would let pydantic validate a preview
# *as* a result and silently drop the diff. Returning the model unchanged is
# both simpler and the only version that cannot lose data.
@app.post("/scans/{scan_id}/fix/apply", response_model=None)
async def scan_fix_apply(
    scan_id: str, body: FixApplyRequest, user: User = Depends(current_user)
) -> FixApplyPreview | FixApplyResult:
    """Open one pull request containing the saved fix plans for the requested
    findings (PLAN-v5 Stage B).

    With `dry_run` (the default), every check runs and nothing is written —
    the response is exactly what *would* be pushed. With `dry_run: false`,
    the same checks run and then one branch, one commit, and one pull request
    are created. Sentinels never merges it.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")

    try:
        return await apply_fixes(report, user, body.finding_keys, dry_run=body.dry_run)
    except ApplyError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/scans/{scan_id}/fix/applications", response_model=list[FixApplication])
async def scan_fix_applications(
    scan_id: str, user: User = Depends(current_user)
) -> list[FixApplication]:
    """The remediation history for one scan, with any still-open pull request
    re-read from GitHub first.

    Stage C decides whether to re-verify based on whether a PR merged, so
    `state` has to reflect GitHub's answer rather than what Sentinels last
    happened to see.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    return await refresh_applications(report, user)


@app.post(
    "/scans/{scan_id}/findings/{finding_key}/verify", response_model=VerificationResult
)
async def finding_verify(
    scan_id: str, finding_key: str, user: User = Depends(current_user)
) -> VerificationResult:
    """Re-run the agent responsible for one finding and report what changed
    (PLAN-v5 Stage C).

    Downloads the repository again, runs exactly that one agent, and returns
    the real before/after score from the untouched deterministic scorer. When
    a fix Sentinels opened has merged, the matching `fix_applications` row
    moves to `verified` and keeps this result as its evidence; a pull request
    that hasn't merged yet is refused rather than verified against the old
    state of the repository.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")

    try:
        return await verify_finding(report, user, finding_key)
    except VerifyError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@app.get("/scans/{scan_id}/audit", response_model=list[AuditLogEntry])
def scan_audit(scan_id: str, user: User = Depends(current_user)) -> list[AuditLogEntry]:
    """The audit trail for one scan (PLAN-v5 Stage E), oldest first -- a thin,
    ownership-checked wrapper over the same `list_audit` Stage B has written
    to since the first pull request, never read back until now.

    Unlike `GET /scans/{id}/fix/applications` (which lets any signed-in user
    read a legacy unowned scan's history), this refuses outright for a scan
    that belongs to someone else -- the audit trail is who-did-what, and
    "who" is exactly what shouldn't leak across accounts.
    """
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    owner = scan_owner(scan_id)
    if owner is not None and owner != user.id:
        raise HTTPException(status_code=403, detail="This scan belongs to another user.")
    return [AuditLogEntry(**row) for row in list_audit(scan_id)]


class ChatQuestion(BaseModel):
    question: str


@app.post("/scans/{scan_id}/chat", response_model=ChatMessage)
async def chat_post(scan_id: str, body: ChatQuestion, user: User = Depends(current_user)) -> ChatMessage:
    """Ask one question about a completed scan.

    Persists both the user question and the assistant answer to the DB so the
    conversation survives refresh. With no GROQ_API_KEY: returns 503, not 500.
    """
    if not get_api_key():
        raise HTTPException(status_code=503, detail="Chat requires GROQ_API_KEY.")

    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")

    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be empty")

    msg = await chat_answer(scan_id, report, report.checklist, question)
    if msg is None:
        raise HTTPException(status_code=503, detail="Chat answer generation failed. Try again.")
    return msg


@app.get("/scans/{scan_id}/chat", response_model=list[ChatMessage])
def chat_history(scan_id: str, user: User = Depends(current_user)) -> list[ChatMessage]:
    """Return the full conversation history for a scan, oldest first."""
    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")
    return load_messages(scan_id)


@app.get("/export/formats")
def export_formats() -> list[dict[str, str]]:
    """List every registered export format — id, MIME type, file extension."""
    return list_formats()


@app.get("/scans/{scan_id}/export/{format_id}")
async def scan_export(scan_id: str, format_id: str, user: User = Depends(current_user)) -> Response:
    """Export a stored scan in the given format (pdf | json | markdown).

    Looks up any cached AI fix suggestions for the scan and passes them to
    the exporter — a scan with none still exports cleanly (M19's graceful
    degradation, same rule as the rest of the AI layer).
    """
    exporter = get_exporter(format_id)
    if exporter is None:
        raise HTTPException(status_code=404, detail=f"Unknown export format {format_id!r}")

    report = get_scan(scan_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found")

    fixes = load_fixes_for_scan(scan_id, PROMPT_VERSION)
    content = await exporter.render(report, fixes)

    host = urlparse(report.url).netloc or "report"
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "report"

    return Response(
        content=content,
        media_type=exporter.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="sentinels-{slug}.{exporter.extension}"'
        },
    )


@app.post("/scan/pdf")
async def scan_pdf(report: ScanReport, user: User = Depends(current_user)) -> Response:
    """Deprecated alias — kept through M17 per PLAN-v2.md, then removed.

    Prints a *finished* report to PDF, taking the whole `ScanReport` as the
    request body instead of a `url` — the frontend already has one sitting
    in state the moment the "Download PDF" button is visible. Re-scanning
    from just the URL was rejected: a live site can change between the two
    requests, so the PDF could show different findings than the report the
    user is actually looking at. This way, what downloads is guaranteed to
    match what's on screen.

    Prefer `GET /scans/{id}/export/pdf` — it also includes cached AI fixes,
    which this alias (no scan_id, just a bare report) has no way to look up.
    """
    pdf_bytes = await generate_pdf(report)

    host = urlparse(report.url).netloc or "report"
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-") or "report"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="sentinels-{slug}.pdf"'},
    )
