"""The one place in Sentinels that writes to somebody's repository.

Everything else in `remediation/` produces text: a diff, a summary, a PR body.
This module is where that text becomes a branch and a pull request, so every
rule that governs the feature is enforced here, in one readable sequence,
rather than spread across the modules it calls.

The order below is deliberate and is the order in `docs/PLAN-v5.md`. Every
check that can reject the request runs *before* the first byte is written, so
a rejected apply is always a no-op — there is no half-applied state to
explain or clean up. The single exception is a pull request that fails to open
after the branch already exists, and that case deletes the branch it made.

`dry_run=True` runs every check and stops immediately before the first write.
It exists so that "would this work?" can be answered without hoping.

The two invariants signed off in PLAN-v5 Stage B, stated once:

  **#3 Strict ownership.** The scan must belong to the caller. Unlike
  `DELETE /scans/{id}`, an unowned legacy scan is *not* fair game — there is
  no one whose installation it would even use.

  **#4 Installation ownership, checked separately.** A live
  `github_installations` row belonging to the caller, on the account that owns
  the target repository. Passing one check never implies the other.
"""
from __future__ import annotations

import re
import time

import httpx

from models import (
    Finding,
    FilePatch,
    FixApplication,
    FixApplicationState,
    FixApplyPreview,
    FixApplyResult,
    FixPlan,
    ScanReport,
    User,
)
from remediation import pr_body
from remediation.budget import MAX_FILES_PER_PR, MAX_PRS_PER_HOUR, MAX_PRS_PER_SCAN
from remediation.access import RepoAccessError, require_write_access
from remediation.github import GitHubWriteError, GitHubWriter, commit_files
from remediation.linking import repo_target
from remediation.patch import PlanValidationError, validate_plan
from remediation.source import get_file, resolve_ref_sha
from remediation.tokens import TokenError, TokenProvider, default_provider
from storage.installations import active_installation_for
from storage.scans import scan_owner
from storage.remediation import (
    active_fix_applications,
    count_prs_for_scan,
    count_prs_since,
    get_fix_plan,
    list_fix_applications,
    save_fix_application,
    update_fix_application_state,
    write_audit,
)

# The only branch names Sentinels is allowed to create. Built by
# `pr_body.branch_name`, then checked here against the shape independently --
# a branch name is the one string in this flow that becomes a permanent part
# of someone else's repository, so it is worth verifying twice.
BRANCH_PATTERN = re.compile(r"^sentinels/fix-[0-9a-f]{8}-\d+$")


class ApplyError(RuntimeError):
    """A rejected apply. `status` is the HTTP code the route should return,
    so the reason a request was refused survives the trip to the user instead
    of collapsing into a generic 400."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _finding(report: ScanReport, key: str) -> Finding | None:
    return next((f for f in report.findings if f.id == key), None)


def _check_ownership(scan_id: str, user: User) -> None:
    """Invariant #3. `scan_owner` returns None both for a scan taken before
    Stage 0 added identity and for a scan that doesn't exist; here they mean
    the same thing and get the same refusal."""
    owner_id = scan_owner(scan_id)
    if owner_id is None:
        raise ApplyError(
            "This scan has no owner, so no repository access can be attributed to it. "
            "Re-run the scan while signed in before applying a fix.",
            status=403,
        )
    if owner_id != user.id:
        raise ApplyError("This scan belongs to another user.", status=403)


def _load_plans(report: ScanReport, finding_keys: list[str]) -> list[tuple[Finding, FixPlan]]:
    """Pair each requested key with its stored plan and its finding.

    A plan has to have been saved by `POST /scans/{id}/fix/plan` first. The
    apply path never plans on the fly: what gets pushed must be something a
    person had the opportunity to look at (CLAUDE.md remediation rule 6,
    "Always preview before pushing").
    """
    pairs: list[tuple[Finding, FixPlan]] = []
    for key in finding_keys:
        finding = _finding(report, key)
        if finding is None:
            raise ApplyError(f"Finding {key!r} is not part of this scan.", status=404)
        plan = get_fix_plan(report.id, key)
        if plan is None:
            raise ApplyError(
                f"No saved fix plan for {key!r}. Preview and save a plan before applying it.",
                status=409,
            )
        pairs.append((finding, plan))
    return pairs


def _check_idempotency(scan_id: str, finding_keys: list[str]) -> FixApplication | None:
    """Step 2. Returns the existing application when *every* requested finding
    already has one, so the caller can hand back the pull request that already
    exists instead of opening a second one for the same problem.

    A mixed selection — some already applied, some not — is rejected rather
    than silently split. Quietly dropping half of what was asked for is worse
    than refusing: the user asked for one pull request containing four fixes,
    and would get one containing two without being told which.
    """
    existing = active_fix_applications(scan_id, finding_keys)
    if not existing:
        return None
    if len(existing) == len(finding_keys):
        return next(iter(existing.values()))

    already = ", ".join(sorted(existing))
    raise ApplyError(
        f"Some of these findings already have an open fix ({already}) and some do not. "
        "Apply them separately.",
        status=409,
    )


def _check_batch(pairs: list[tuple[Finding, FixPlan]]) -> list[FilePatch]:
    """Step 4 — the cross-plan checks Stage A's `validate_plan` cannot make,
    because it only ever sees one plan at a time.

    Two plans touching the same path is the dangerous one: each was built
    against the file as it is *now*, so applying both would silently discard
    whichever change was written first.
    """
    patches: list[FilePatch] = []
    seen: dict[str, str] = {}
    for finding, plan in pairs:
        for patch in plan.patches:
            if patch.path in seen:
                raise ApplyError(
                    f"Two fixes both change {patch.path!r} ({seen[patch.path]} and "
                    f"{finding.id}). Apply them one at a time so neither overwrites the other.",
                    status=409,
                )
            seen[patch.path] = finding.id
            patches.append(patch)

    if len(patches) > MAX_FILES_PER_PR:
        raise ApplyError(
            f"This selection changes {len(patches)} files, over the "
            f"{MAX_FILES_PER_PR}-file limit for one pull request.",
            status=422,
        )
    return patches


async def _check_drift(
    client: httpx.AsyncClient, owner: str, repo: str, ref: str, patches: list[FilePatch]
) -> None:
    """Step 5 — CLAUDE.md remediation rule 7, "Drift aborts".

    Every patch was built against a specific version of a file, recorded as
    `original_sha`. If that file has changed since, the diff no longer
    describes the repository and applying it would overwrite work that arrived
    after planning. One mismatch anywhere aborts the *entire* batch: a pull
    request containing three good patches and one built on stale content is
    not something to open and hope someone notices.

    A `create` patch has no `original_sha` — its assumption is that the file
    does not exist, so *that* is what gets re-checked.
    """
    for patch in patches:
        current = await get_file(client, owner, repo, patch.path, ref)

        if patch.action == "create":
            if current is not None:
                raise ApplyError(
                    f"{patch.path} now exists in the repository — it did not when this fix "
                    "was planned. Re-scan and plan again.",
                    status=409,
                )
            continue

        if current is None:
            raise ApplyError(
                f"{patch.path} no longer exists in the repository. Re-scan and plan again.",
                status=409,
            )
        if current.sha != patch.original_sha:
            raise ApplyError(
                f"{patch.path} has changed since this fix was planned. Re-scan and plan "
                "again so the patch is built against the current file.",
                status=409,
            )


def _check_budget(scan_id: str) -> None:
    """Step 6 — the caps that have existed as constants since Stage A and are
    enforced starting here. Same discipline as `agents/probe.py`'s `Budget`:
    a hard ceiling stated as a readable constant, not an implicit hope that
    nothing loops."""
    per_scan = count_prs_for_scan(scan_id)
    if per_scan >= MAX_PRS_PER_SCAN:
        raise ApplyError(
            f"This scan has already opened {per_scan} pull requests, the limit of "
            f"{MAX_PRS_PER_SCAN}. Merge or close them before opening another.",
            status=429,
        )
    per_hour = count_prs_since(hours=1)
    if per_hour >= MAX_PRS_PER_HOUR:
        raise ApplyError(
            f"Sentinels has opened {per_hour} pull requests in the last hour, the limit of "
            f"{MAX_PRS_PER_HOUR}. Try again shortly.",
            status=429,
        )


async def apply_fixes(
    report: ScanReport,
    user: User,
    finding_keys: list[str],
    dry_run: bool = True,
    provider: TokenProvider | None = None,
) -> FixApplyPreview | FixApplyResult:
    """Turn saved fix plans into one branch, one commit, and one pull request.

    Returns a `FixApplyPreview` when `dry_run` is true (nothing written), and a
    `FixApplyResult` otherwise. Raises `ApplyError` for every refusal, always
    before anything has been written.
    """
    if not finding_keys:
        raise ApplyError("No findings selected.", status=422)

    _check_ownership(report.id, user)                                 # 1

    try:
        owner, repo, url_ref = repo_target(report)
    except ValueError as exc:
        raise ApplyError(f"This scan's target is not a GitHub repository: {exc}", 400) from exc

    installation = active_installation_for(user.id, owner)            # 1 (invariant #4)
    if installation is None:
        raise ApplyError(
            f"Sentinels has no repository access for {owner!r}. Install the Sentinels App "
            "on that account, then try again.",
            status=403,
        )

    pairs = _load_plans(report, finding_keys)
    existing = _check_idempotency(report.id, finding_keys)            # 2
    if existing is not None:
        return FixApplyResult(
            repo=f"{owner}/{repo}",
            branch=existing.branch or "",
            pr_url=existing.pr_url,
            pr_number=existing.pr_number,
            already_applied=True,
            applications=[existing],
        )

    for finding, plan in pairs:                                       # 3
        try:
            validate_plan(finding, plan)
        except PlanValidationError as exc:
            raise ApplyError(f"Stored plan for {finding.id!r} is no longer valid: {exc}", 422) from exc

    patches = _check_batch(pairs)                                     # 4

    # The token is minted here rather than at the last moment (PLAN-v5 Stage B
    # step 9) because the drift re-check below has to *read* the repository,
    # and an unauthenticated read cannot see a private one at all -- it 404s,
    # which the drift check would honestly but wrongly report as "the file was
    # deleted". Minting a token writes nothing; see PLAN-v5.md conflict #11.
    provider = provider or default_provider()

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            installation_token = await provider.token_for(client, installation.installation_id)
        except TokenError as exc:
            raise ApplyError(str(exc), status=502) from exc

        # Every subsequent read on this client is authenticated too -- an
        # unauthenticated Contents API call cannot see a private repository at
        # all, and the drift check must not mistake "you can't see it" for
        # "it was deleted".
        client.headers["Authorization"] = f"Bearer {installation_token.token}"

        # An installation is a grant to the whole account. Whether *this
        # person* may push to *this repository* is a separate question, and
        # it is asked before any read of the repository's contents or any
        # write -- including for a dry run.
        try:
            await require_write_access(
                client, installation_token.token, owner, repo, user.github_login
            )
        except RepoAccessError as exc:
            raise ApplyError(str(exc), status=exc.status) from exc

        writer = GitHubWriter(client, owner, repo, installation_token.token)
        try:
            repo_meta = await writer.get_repo()
        except GitHubWriteError as exc:
            raise ApplyError(str(exc), status=exc.status or 502) from exc

        default_branch = repo_meta.get("default_branch") or "main"
        base_branch = url_ref or default_branch

        await _check_drift(client, owner, repo, base_branch, patches)        # 5
        _check_budget(report.id)                                             # 6

        branch = pr_body.branch_name(report.id, int(time.time()))
        message = pr_body.commit_message([finding for finding, _ in pairs])
        title = pr_body.pull_request_title([finding for finding, _ in pairs])
        body = pr_body.pull_request_body(report.id, pairs, report.url)

        if dry_run:                                                          # 7
            return FixApplyPreview(
                repo=f"{owner}/{repo}",
                base_branch=base_branch,
                branch=branch,
                commit_message=message,
                pr_title=title,
                pr_body=body,
                finding_keys=list(finding_keys),
                patches=patches,
            )

        # 8 -- defense in depth. The prefix should make a collision with a
        # real branch structurally impossible, which is exactly why an
        # unexpected name here means something is wrong enough to stop.
        if not BRANCH_PATTERN.match(branch) or branch == default_branch:
            raise ApplyError(f"Refusing to create branch {branch!r}.", status=500)

        base_sha = await resolve_ref_sha(client, owner, repo, base_branch)
        if base_sha is None:
            raise ApplyError(
                f"Could not resolve {base_branch!r} in {owner}/{repo}.", status=404
            )

        files = [(patch.path, patch.new_content or "") for patch in patches]
        try:
            await commit_files(writer, base_branch, base_sha, branch, message, files)  # 9
        except GitHubWriteError as exc:
            write_audit(user.id, report.id, None, "pr_failed", f"commit failed: {exc}")
            raise ApplyError(str(exc), status=exc.status or 502) from exc

        try:
            pull = await writer.create_pull_request(title, body, branch, base_branch)
        except GitHubWriteError as exc:
            # The branch exists but has no pull request explaining it. Remove
            # it rather than leaving a stray `sentinels/...` branch in
            # someone's repository (Stage B step 10).
            removed = await writer.delete_ref(branch)
            write_audit(
                user.id, report.id, None, "pr_failed",
                f"PR creation failed on branch {branch}: {exc} "
                f"(branch {'removed' if removed else 'could NOT be removed'})",
            )
            raise ApplyError(str(exc), status=exc.status or 502) from exc

    applications = []                                                            # 10
    for finding, plan in pairs:
        application = save_fix_application(
            report.id,
            plan,
            FixApplicationState.PR_OPEN,
            branch=branch,
            pr_url=pull.url,
            pr_number=pull.number,
        )
        applications.append(application)
        write_audit(
            user.id, report.id, finding.id, "pr_opened",
            f"{owner}/{repo} branch={branch} pr=#{pull.number} fixer={plan.fixer_slug} "
            f"files={','.join(p.path for p in plan.patches)}",
        )

    return FixApplyResult(
        repo=f"{owner}/{repo}",
        branch=branch,
        pr_url=pull.url,
        pr_number=pull.number,
        already_applied=False,
        applications=applications,
    )


async def refresh_applications(
    report: ScanReport,
    user: User,
    provider: TokenProvider | None = None,
) -> list[FixApplication]:
    """Every application recorded for a scan, with any still-open pull request
    re-checked against GitHub.

    Stage C decides whether to re-run an agent based on whether the PR merged,
    so this field has to mean "GitHub says so", not "this is what we saw when
    we opened it". A row that cannot be refreshed (no installation, GitHub
    unreachable) is returned unchanged rather than failing the whole list —
    the stored history is still worth showing.
    """
    applications = list_fix_applications(report.id)
    open_rows = [a for a in applications if a.state == FixApplicationState.PR_OPEN and a.pr_number]
    if not open_rows or scan_owner(report.id) != user.id:
        return applications

    try:
        owner, repo, _ = repo_target(report)
    except ValueError:
        return applications

    installation = active_installation_for(user.id, owner)
    if installation is None:
        return applications

    provider = provider or default_provider()
    refreshed: dict[str, FixApplicationState] = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            token = await provider.token_for(client, installation.installation_id)
        except TokenError:
            return applications

        writer = GitHubWriter(client, owner, repo, token.token)
        # One lookup per distinct PR, not per row -- a batch apply writes one
        # row per finding but opens exactly one pull request.
        for number in {a.pr_number for a in open_rows if a.pr_number}:
            try:
                pull = await writer.get_pull_request(number)
            except GitHubWriteError:
                continue
            if pull is None:
                continue
            if pull.get("merged"):
                state = FixApplicationState.MERGED
            elif pull.get("state") == "closed":
                state = FixApplicationState.ABANDONED
            else:
                continue
            for application in open_rows:
                if application.pr_number == number:
                    refreshed[application.id] = state

    for application_id, state in refreshed.items():
        update_fix_application_state(application_id, state)
        write_audit(user.id, report.id, None, f"pr_{state.value}", f"application {application_id}")

    if refreshed:
        applications = list_fix_applications(report.id)
    return applications
