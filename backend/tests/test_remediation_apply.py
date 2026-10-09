"""Tests for remediation/apply.py -- the only code in Sentinels that writes to
somebody's repository, and therefore the code whose refusals matter most.

Every test here asserts a *rejection* happens before any write, or that a
write happened exactly once. No real GitHub call: `_routes` builds a mocked
transport and `_FakeProvider` stands in for the token minting entirely.
"""
from __future__ import annotations

import httpx
import pytest

from models import (
    FilePatch,
    Finding,
    FixApplicationState,
    FixApplyPreview,
    FixApplyResult,
    FixPlan,
    ScanReport,
    Severity,
    Status,
)
from remediation import apply as apply_module
from remediation.apply import ApplyError, apply_fixes, refresh_applications
from remediation.tokens import InstallationToken, TokenError, TokenProvider
from storage.installations import save_installation
from storage.remediation import (
    list_audit,
    list_fix_applications,
    save_fix_application,
    save_fix_plan,
)
from storage.scan_links import save_scan_repo_link
from storage.scans import save_scan
from storage.users import sign_in

BASE = "/repos/octo/demo"

# A realistic uuid4 scan id. It matters: `BRANCH_PATTERN` requires the branch
# to carry 8 hex characters of the scan id, so a made-up id like "scan1" would
# fail a guard that is doing its job.
SCAN_ID = "a1b2c3d4-0000-4000-8000-000000000001"


class _FakeProvider(TokenProvider):
    async def token_for(self, client, installation_id):
        return InstallationToken(token="ghs_fake", expires_at="")


class _FailingProvider(TokenProvider):
    async def token_for(self, client, installation_id):
        raise TokenError("no key configured")


def _happy_routes(gitignore_exists: bool = False) -> dict:
    """A repo where `.gitignore` does not exist (so the create patch is clean)
    and every write succeeds."""
    routes = {
        ("GET", BASE): (200, {"default_branch": "main"}),
        # The App-side check that the signed-in user may push to this repo.
        ("GET", f"{BASE}/collaborators/octo/permission"): (
            200, {"role_name": "write", "permission": "write"}
        ),
        ("GET", f"{BASE}/commits/main"): (200, {"sha": "basesha"}),
        ("GET", f"{BASE}/git/commits/basesha"): (200, {"tree": {"sha": "basetree"}}),
        ("POST", f"{BASE}/git/blobs"): (201, {"sha": "blobsha"}),
        ("POST", f"{BASE}/git/trees"): (201, {"sha": "newtree"}),
        ("POST", f"{BASE}/git/commits"): (201, {"sha": "newcommit"}),
        ("POST", f"{BASE}/git/refs"): (201, {}),
        ("POST", f"{BASE}/pulls"): (
            201, {"number": 7, "html_url": "https://github.com/octo/demo/pull/7"}
        ),
    }
    if gitignore_exists:
        routes[("GET", f"{BASE}/contents/.gitignore")] = (
            200, {"sha": "existing", "encoding": "base64", "content": "IyBoaQ=="}
        )
    return routes


def _patch_transport(monkeypatch, routes: dict) -> list[tuple[str, str]]:
    """Make every `httpx.AsyncClient` apply.py opens speak to `routes`.

    Returns the shared call log, so a test can assert that a rejection wrote
    nothing -- "no POST happened" is the actual claim these tests make.
    """
    calls: list[tuple[str, str]] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        calls.append(key)
        entry = routes.get(key)
        if entry is None:
            return httpx.Response(404, json={"message": "Not Found"})
        status, body = entry
        return httpx.Response(status, json=body)

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(apply_module.httpx, "AsyncClient", factory)
    return calls


def _finding(key: str = "gitignore-present", file_path: str | None = None) -> Finding:
    return Finding(
        id=key,
        title="No .gitignore",
        category="Repo Hygiene",
        severity=Severity.MEDIUM,
        status=Status.FAIL,
        description="This repository has no .gitignore.",
        agent="config",
        file_path=file_path,
    )


def _plan(key: str = "gitignore-present", path: str = ".gitignore") -> FixPlan:
    return FixPlan(
        finding_key=key,
        fixer_slug="gitignore-present",
        tier=1,
        summary="Add a .gitignore that excludes .env and build output.",
        patches=[
            FilePatch(path=path, action="create", new_content=".env\n", diff="+.env\n")
        ],
        created_at="2026-08-12T00:00:00+00:00",
    )


def _seed(temp_db, findings=None, plans=None, owner_login="octo",
          link_installation=True, owned=True):
    """A signed-in user who owns a repo scan with a saved fix plan and a live
    installation -- i.e. everything that has to be true before an apply is
    even considered."""
    user = sign_in(
        github_id=1, github_login="octo", avatar_url=None,
        token_hash="hash1", expires_at="2099-01-01T00:00:00+00:00",
    )
    report = ScanReport(
        id=SCAN_ID,
        url="https://github.com/octo/demo",
        target_type="repo",
        scanned_at="2026-08-12T00:00:00+00:00",
        duration_ms=100,
        score=80,
        grade="B",
        findings=findings if findings is not None else [_finding()],
    )
    save_scan(report, user_id=user.id if owned else None)
    for plan in (plans if plans is not None else [_plan()]):
        save_fix_plan(report.id, plan)
    if link_installation:
        save_installation(user.id, 500, owner_login, "selected")
    return user, report


# --- Invariant #3: strict scan ownership -----------------------------------

async def test_an_unowned_scan_can_never_be_applied(temp_db, monkeypatch):
    """Unlike DELETE /scans/{id}, which treats a legacy unowned scan as fair
    game, an apply has no one whose installation it would even use."""
    calls = _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db, owned=False)

    with pytest.raises(ApplyError) as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 403
    assert calls == []


async def test_another_users_scan_is_refused(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    _, report = _seed(temp_db)
    intruder = sign_in(
        github_id=2, github_login="mallory", avatar_url=None,
        token_hash="hash2", expires_at="2099-01-01T00:00:00+00:00",
    )
    save_installation(intruder.id, 900, "octo", "all")   # they even have access

    with pytest.raises(ApplyError) as exc:
        await apply_fixes(report, intruder, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 403
    assert calls == []


# --- Invariant #4: installation ownership, checked independently -----------

async def test_owning_the_scan_is_not_enough_without_an_installation(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db, link_installation=False)

    with pytest.raises(ApplyError, match="no repository access") as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 403
    assert calls == []


async def test_an_installation_on_a_different_account_does_not_count(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db, owner_login="some-other-org")

    with pytest.raises(ApplyError, match="no repository access"):
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert calls == []


# --- Preconditions ---------------------------------------------------------

async def test_a_finding_with_no_saved_plan_is_refused(temp_db, monkeypatch):
    """Rule 6: always preview before pushing. Apply never plans on the fly,
    so what gets pushed is always something a person could have looked at."""
    _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db, plans=[])
    with pytest.raises(ApplyError, match="No saved fix plan") as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 409


async def test_a_finding_not_in_the_scan_is_refused(temp_db, monkeypatch):
    _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)
    with pytest.raises(ApplyError) as exc:
        await apply_fixes(report, user, ["not-a-finding"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 404


async def test_an_empty_selection_is_refused(temp_db, monkeypatch):
    _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)
    with pytest.raises(ApplyError):
        await apply_fixes(report, user, [], dry_run=False, provider=_FakeProvider())


# --- Step 4: the cross-plan batch checks -----------------------------------

async def test_two_plans_touching_the_same_file_are_refused(temp_db, monkeypatch):
    """Each was built against the file as it is now, so applying both would
    silently discard whichever was written first."""
    calls = _patch_transport(monkeypatch, _happy_routes())
    findings = [_finding("gitignore-present"), _finding("repo-readme-present")]
    plans = [_plan("gitignore-present", ".gitignore"), _plan("repo-readme-present", ".gitignore")]
    user, report = _seed(temp_db, findings=findings, plans=plans)

    with pytest.raises(ApplyError, match="both change") as exc:
        await apply_fixes(
            report, user, ["gitignore-present", "repo-readme-present"],
            dry_run=False, provider=_FakeProvider(),
        )
    assert exc.value.status == 409
    assert calls == []


async def test_too_many_files_across_the_batch_is_refused(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    findings, plans, keys = [], [], []
    for index in range(11):
        key = f"ci-unpinned-action-{index}"   # tier 1, so nothing else rejects it first
        keys.append(key)
        findings.append(_finding(key))
        plans.append(_plan(key, f"file{index}.txt"))
    user, report = _seed(temp_db, findings=findings, plans=plans)

    with pytest.raises(ApplyError, match="over the 10-file limit") as exc:
        await apply_fixes(report, user, keys, dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 422
    assert calls == []


# --- Step 5: drift aborts the whole batch ----------------------------------

async def test_a_create_patch_aborts_if_the_file_now_exists(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes(gitignore_exists=True))
    user, report = _seed(temp_db)

    with pytest.raises(ApplyError, match="now exists") as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 409
    assert ("POST", f"{BASE}/git/refs") not in calls


async def test_a_modify_patch_aborts_when_the_blob_sha_moved(temp_db, monkeypatch):
    routes = _happy_routes()
    routes[("GET", f"{BASE}/contents/Dockerfile")] = (
        200, {"sha": "sha-now", "encoding": "base64", "content": "RlJPTSBweXRob24="}
    )
    calls = _patch_transport(monkeypatch, routes)

    finding = _finding("docker-root-user-Dockerfile", file_path="Dockerfile")
    plan = FixPlan(
        finding_key="docker-root-user-Dockerfile",
        fixer_slug="docker-root-user",
        tier=2,
        summary="Run as a non-root user.",
        patches=[FilePatch(
            path="Dockerfile", action="modify",
            original_sha="sha-when-planned", original_content="FROM python",
            new_content="FROM python\nUSER app\n", diff="+USER app",
        )],
        created_at="2026-08-12T00:00:00+00:00",
    )
    user, report = _seed(temp_db, findings=[finding], plans=[plan])

    with pytest.raises(ApplyError, match="has changed since") as exc:
        await apply_fixes(
            report, user, ["docker-root-user-Dockerfile"], dry_run=False, provider=_FakeProvider()
        )
    assert exc.value.status == 409
    assert ("POST", f"{BASE}/git/refs") not in calls


async def test_one_drifted_file_aborts_the_entire_batch(temp_db, monkeypatch):
    """Not "apply the clean ones" -- a pull request containing three good
    patches and one stale one is not something to open and hope."""
    routes = _happy_routes(gitignore_exists=True)
    calls = _patch_transport(monkeypatch, routes)
    findings = [_finding("gitignore-present"), _finding("repo-readme-present")]
    plans = [_plan("gitignore-present", ".gitignore"), _plan("repo-readme-present", "README.md")]
    user, report = _seed(temp_db, findings=findings, plans=plans)

    with pytest.raises(ApplyError):
        await apply_fixes(
            report, user, ["gitignore-present", "repo-readme-present"],
            dry_run=False, provider=_FakeProvider(),
        )
    assert ("POST", f"{BASE}/git/blobs") not in calls


# --- Step 6: budgets -------------------------------------------------------

async def test_the_per_scan_pr_budget_is_enforced(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)
    for index in range(3):
        plan = _plan(f"other-{index}")
        save_fix_application(
            report.id, plan, FixApplicationState.PR_OPEN,
            branch=f"sentinels/fix-a1b2c3d4-{index}", pr_number=index,
        )

    with pytest.raises(ApplyError, match="limit of 3") as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 429
    assert ("POST", f"{BASE}/git/refs") not in calls


# --- Step 7: dry run -------------------------------------------------------

async def test_dry_run_writes_nothing_and_returns_the_exact_plan(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)

    preview = await apply_fixes(
        report, user, ["gitignore-present"], dry_run=True, provider=_FakeProvider()
    )
    assert isinstance(preview, FixApplyPreview)
    assert preview.repo == "octo/demo"
    assert preview.base_branch == "main"
    assert preview.branch.startswith("sentinels/fix-a1b2c3d4")
    assert preview.patches[0].path == ".gitignore"
    assert "does _not_ do" in preview.pr_body

    assert not any(method == "POST" for method, _ in calls)
    assert list_fix_applications(report.id) == []


async def test_dry_run_still_runs_every_check(temp_db, monkeypatch):
    _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db, link_installation=False)
    with pytest.raises(ApplyError):
        await apply_fixes(report, user, ["gitignore-present"], dry_run=True, provider=_FakeProvider())


# --- Steps 8-10: the live path --------------------------------------------

async def test_a_live_apply_opens_exactly_one_pull_request(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)

    result = await apply_fixes(
        report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider()
    )
    assert isinstance(result, FixApplyResult)
    assert result.pr_url == "https://github.com/octo/demo/pull/7"
    assert result.branch.startswith("sentinels/fix-a1b2c3d4-")
    assert calls.count(("POST", f"{BASE}/pulls")) == 1
    assert calls.count(("POST", f"{BASE}/git/refs")) == 1


# --- PLAN-v5 Stage D: applying through a linked URL scan --------------------

async def test_a_linked_url_scan_opens_a_pr_against_the_linked_repo(temp_db, monkeypatch):
    """A URL scan's own `.url` is a website, not a GitHub repository -- the
    apply pipeline has to read `owner`/`repo` from `scan_repo_links` instead
    of trying (and failing) to parse it."""
    calls = _patch_transport(monkeypatch, _happy_routes())
    user = sign_in(
        github_id=1, github_login="octo", avatar_url=None,
        token_hash="hash1", expires_at="2099-01-01T00:00:00+00:00",
    )
    report = ScanReport(
        id=SCAN_ID, url="https://example.com", target_type="url",
        scanned_at="2026-08-13T00:00:00+00:00", duration_ms=10, score=60, grade="D",
        findings=[_finding()],
    )
    save_scan(report, user_id=user.id)
    save_fix_plan(report.id, _plan())
    save_installation(user.id, 500, "octo", "selected")
    save_scan_repo_link(report.id, user.id, 500, "octo", "demo")

    result = await apply_fixes(
        report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider()
    )
    assert isinstance(result, FixApplyResult)
    assert result.repo == "octo/demo"
    assert calls.count(("POST", f"{BASE}/pulls")) == 1


async def test_a_url_scan_with_no_link_is_refused_before_any_write(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    user = sign_in(
        github_id=1, github_login="octo", avatar_url=None,
        token_hash="hash1", expires_at="2099-01-01T00:00:00+00:00",
    )
    report = ScanReport(
        id=SCAN_ID, url="https://example.com", target_type="url",
        scanned_at="2026-08-13T00:00:00+00:00", duration_ms=10, score=60, grade="D",
        findings=[_finding()],
    )
    save_scan(report, user_id=user.id)
    save_fix_plan(report.id, _plan())
    save_installation(user.id, 500, "octo", "selected")

    with pytest.raises(ApplyError, match="linked repository") as exc:
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert exc.value.status == 400
    assert calls == []


async def test_a_live_apply_writes_an_audit_row_and_an_application(temp_db, monkeypatch):
    _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)
    await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())

    applications = list_fix_applications(report.id)
    assert len(applications) == 1
    assert applications[0].state == FixApplicationState.PR_OPEN
    assert applications[0].plan.patches[0].path == ".gitignore"

    audit = list_audit(report.id)
    assert [row["action"] for row in audit] == ["pr_opened"]
    assert "pr=#7" in audit[0]["detail"]


async def test_a_batch_writes_one_application_per_finding_but_one_pr(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    findings = [_finding("gitignore-present"), _finding("repo-readme-present")]
    plans = [_plan("gitignore-present", ".gitignore"), _plan("repo-readme-present", "README.md")]
    user, report = _seed(temp_db, findings=findings, plans=plans)

    await apply_fixes(
        report, user, ["gitignore-present", "repo-readme-present"],
        dry_run=False, provider=_FakeProvider(),
    )
    assert len(list_fix_applications(report.id)) == 2
    assert calls.count(("POST", f"{BASE}/pulls")) == 1


async def test_a_failed_pr_deletes_the_orphan_branch(temp_db, monkeypatch):
    """The branch exists but nothing explains what it is. Removing it is the
    difference between a failed apply and a mess left in someone's repo."""
    routes = _happy_routes()
    routes[("POST", f"{BASE}/pulls")] = (422, {})
    branch_deletes = []

    real = httpx.AsyncClient
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        calls.append(key)
        if request.method == "DELETE" and "/git/refs/heads/" in request.url.path:
            branch_deletes.append(request.url.path)
            return httpx.Response(204)
        entry = routes.get(key)
        if entry is None:
            return httpx.Response(404, json={"message": "Not Found"})
        status, body = entry
        return httpx.Response(status, json=body)

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(apply_module.httpx, "AsyncClient", factory)
    user, report = _seed(temp_db)

    with pytest.raises(ApplyError):
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())

    assert len(branch_deletes) == 1
    assert "sentinels/fix-a1b2c3d4-" in branch_deletes[0]
    # No application row for a fix that never landed, but the attempt is audited.
    assert list_fix_applications(report.id) == []
    assert [row["action"] for row in list_audit(report.id)] == ["pr_failed"]


async def test_a_failed_commit_never_reaches_the_pr_step(temp_db, monkeypatch):
    routes = _happy_routes()
    routes[("POST", f"{BASE}/git/commits")] = (500, {})
    calls = _patch_transport(monkeypatch, routes)
    user, report = _seed(temp_db)

    with pytest.raises(ApplyError):
        await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    assert ("POST", f"{BASE}/pulls") not in calls
    assert ("POST", f"{BASE}/git/refs") not in calls


async def test_a_token_that_cannot_be_minted_stops_before_any_write(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)

    with pytest.raises(ApplyError, match="no key configured") as exc:
        await apply_fixes(
            report, user, ["gitignore-present"], dry_run=False, provider=_FailingProvider()
        )
    assert exc.value.status == 502
    assert calls == []


# --- Step 2: idempotency ---------------------------------------------------

async def test_applying_the_same_finding_twice_returns_the_existing_pr(temp_db, monkeypatch):
    calls = _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)

    first = await apply_fixes(
        report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider()
    )
    posts_after_first = calls.count(("POST", f"{BASE}/pulls"))

    second = await apply_fixes(
        report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider()
    )
    assert second.already_applied is True
    assert second.pr_url == first.pr_url
    assert calls.count(("POST", f"{BASE}/pulls")) == posts_after_first


async def test_a_mixed_selection_is_rejected_rather_than_silently_split(temp_db, monkeypatch):
    """The user asked for one pull request covering two fixes. Quietly giving
    them one covering one is worse than refusing."""
    calls = _patch_transport(monkeypatch, _happy_routes())
    findings = [_finding("gitignore-present"), _finding("repo-readme-present")]
    plans = [_plan("gitignore-present", ".gitignore"), _plan("repo-readme-present", "README.md")]
    user, report = _seed(temp_db, findings=findings, plans=plans)

    await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())
    before = calls.count(("POST", f"{BASE}/pulls"))

    with pytest.raises(ApplyError, match="Apply them separately") as exc:
        await apply_fixes(
            report, user, ["gitignore-present", "repo-readme-present"],
            dry_run=False, provider=_FakeProvider(),
        )
    assert exc.value.status == 409
    assert calls.count(("POST", f"{BASE}/pulls")) == before


async def test_a_failed_attempt_does_not_block_a_retry(temp_db, monkeypatch):
    _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)
    save_fix_application(
        report.id, _plan(), FixApplicationState.FAILED, branch="sentinels/fix-a1b2c3d4-1"
    )
    result = await apply_fixes(
        report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider()
    )
    assert result.already_applied is False


# --- refresh_applications --------------------------------------------------

async def test_refresh_promotes_an_open_pr_to_merged(temp_db, monkeypatch):
    routes = _happy_routes()
    routes[("GET", f"{BASE}/pulls/7")] = (200, {"merged": True, "state": "closed"})
    _patch_transport(monkeypatch, routes)
    user, report = _seed(temp_db)
    await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())

    applications = await refresh_applications(report, user, provider=_FakeProvider())
    assert applications[0].state == FixApplicationState.MERGED


async def test_refresh_marks_a_closed_unmerged_pr_abandoned(temp_db, monkeypatch):
    routes = _happy_routes()
    routes[("GET", f"{BASE}/pulls/7")] = (200, {"merged": False, "state": "closed"})
    _patch_transport(monkeypatch, routes)
    user, report = _seed(temp_db)
    await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())

    applications = await refresh_applications(report, user, provider=_FakeProvider())
    assert applications[0].state == FixApplicationState.ABANDONED


async def test_refresh_leaves_an_open_pr_alone(temp_db, monkeypatch):
    routes = _happy_routes()
    routes[("GET", f"{BASE}/pulls/7")] = (200, {"merged": False, "state": "open"})
    _patch_transport(monkeypatch, routes)
    user, report = _seed(temp_db)
    await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())

    applications = await refresh_applications(report, user, provider=_FakeProvider())
    assert applications[0].state == FixApplicationState.PR_OPEN


async def test_refresh_returns_stored_history_when_github_is_unreachable(temp_db, monkeypatch):
    _patch_transport(monkeypatch, _happy_routes())
    user, report = _seed(temp_db)
    await apply_fixes(report, user, ["gitignore-present"], dry_run=False, provider=_FakeProvider())

    applications = await refresh_applications(report, user, provider=_FailingProvider())
    assert len(applications) == 1
    assert applications[0].state == FixApplicationState.PR_OPEN
