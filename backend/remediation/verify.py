"""Re-run one agent against the repository as it is *now*, and report what
actually changed (PLAN-v5 Stage C).

This module exists because of one line in CLAUDE.md: "Never report a fix as
working on the strength of having written it." Stage A produced a diff, Stage B
turned it into a merged pull request — and neither of those is evidence that
the finding is gone. The only evidence is looking again.

So the shape of this file is deliberately narrow:

  * it re-downloads the repository and re-runs **one** agent, the one whose
    slug is stored on the finding being verified — not the whole scan;
  * it never writes a new `scans` row and never touches the original report,
    which is immutable history (PLAN-v5.md conflict #6);
  * the score comes out of the untouched deterministic
    `scoring.calculate_score`, called twice over two lists of findings. No
    model is anywhere near it, and this module does not compute a score itself.

The one write it performs is to the audit trail: the `fix_applications` row for
this finding moves to `verified` and keeps the result as its evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from agents.base import ScanContext
from agents.registry import agent_for as url_agent_for
from agents.repo.base import RepoContext, list_repo_files
from agents.repo_registry import repo_agent_for
from models import (
    AgentResult,
    Finding,
    FixApplication,
    FixApplicationState,
    ScanReport,
    Status,
    User,
    VerificationResult,
)
from remediation.apply import refresh_applications
from remediation.headers_fix import FIXABLE_FINDING_IDS as _LINK_REPO_VERIFIABLE_IDS
from remediation.tokens import TokenError, TokenProvider, default_provider
from repo.fetch import fetch_repo, parse_github_url
from scoring import SCORER_VERSION, calculate_score
from storage.installations import active_installation_for
from storage.remediation import active_fix_applications, save_verification, write_audit
from storage.scans import scan_owner

# Verifying is a read, but it closes out an audit row, so it is gated exactly
# as tightly as applying was: only from the state a merged pull request leaves
# behind, or from `verified` itself (re-verifying is allowed and idempotent).
_VERIFIABLE_STATES = (FixApplicationState.MERGED, FixApplicationState.VERIFIED)


class VerifyError(RuntimeError):
    """A refused verification. `status` is the HTTP code the route should
    return, so the reason survives the trip to the user — same contract as
    `apply.ApplyError`, kept as its own type because the refusals are
    different ones with different wording."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _failing_ids(findings: list[Finding]) -> set[str]:
    """The ids of findings that represent an actual problem.

    PASS findings are excluded for the same reason `calculate_score` excludes
    them: an agent emits them to show a check ran and was clean, and "the
    check passed" is not a thing that can be fixed.
    """
    return {f.id for f in findings if f.status != Status.PASS}


def _check_ownership(scan_id: str, user: User) -> None:
    """Same strict rule as `apply.py`'s: the scan must belong to the caller,
    and an unowned legacy scan belongs to nobody.

    Deliberately duplicated rather than shared, because the refusals say
    different things — this one is not about repository *access*, it is about
    whose audit trail gets a row written to it.
    """
    owner_id = scan_owner(scan_id)
    if owner_id is None:
        raise VerifyError(
            "This scan has no owner, so there is no remediation history to verify against. "
            "Re-run the scan while signed in.",
            status=403,
        )
    if owner_id != user.id:
        raise VerifyError("This scan belongs to another user.", status=403)


def _resolve_agent(finding: Finding):
    """The agent class that produced this finding, or a refusal explaining
    why it can't be re-run."""
    agent_cls = repo_agent_for(finding.agent)
    if agent_cls is not None:
        return agent_cls

    url_agent_cls = url_agent_for(finding.agent)
    if url_agent_cls is not None:
        # A URL-scan agent observes a live site, not a repository, so there is
        # normally no ref to re-read and no merged pull request to have
        # changed anything. The one exception (PLAN-v5 Stage D) is the four
        # header findings: once a repository is linked, a PR against *it* can
        # change what the live site actually sends, so re-observing the site
        # is a real re-check for exactly those ids -- every other URL finding
        # still has no Fixer and no PR to have merged, so it stays refused.
        if finding.id in _LINK_REPO_VERIFIABLE_IDS:
            return url_agent_cls
        raise VerifyError(
            f"{finding.agent!r} scans a live URL, not a repository — verification for "
            "this URL finding is not available yet.",
            status=400,
        )
    raise VerifyError(
        f"Finding {finding.id!r} names an agent ({finding.agent or 'none'}) that no longer "
        "exists, so it cannot be re-run.",
        status=409,
    )


async def _application_to_verify(
    report: ScanReport, user: User, finding_key: str, provider: TokenProvider | None
) -> FixApplication | None:
    """The `fix_applications` row this verification closes out, if there is
    one.

    The live PR-state refresh runs first, on purpose: the decision below is
    "did this merge?", and the stored `state` only means "GitHub said so"
    right after a refresh. Returning `None` is a normal answer — a finding
    someone fixed by hand has nothing recorded against it, and verifying it
    is still a legitimate thing to want.
    """
    await refresh_applications(report, user, provider=provider)

    application = active_fix_applications(report.id, [finding_key]).get(finding_key)
    if application is None:
        return None

    if application.state not in _VERIFIABLE_STATES:
        if application.state == FixApplicationState.PR_OPEN and application.pr_number:
            raise VerifyError(
                f"Pull request #{application.pr_number} has not been merged yet. Merge it "
                "first — verifying now would only re-observe the original problem.",
                status=409,
            )
        raise VerifyError(
            f"This fix is in state {application.state.value!r}; there is nothing merged to "
            "verify yet.",
            status=409,
        )
    return application


async def _rerun_agent(
    report: ScanReport, agent_cls, user_id: int, provider: TokenProvider | None
) -> tuple[AgentResult, str]:
    """Download the repository again and run exactly one agent over it.

    The tarball has to be re-fetched because `repo/fetch.py` deletes it as
    soon as a scan ends and stores no file content (PLAN-v5.md conflict #5) —
    which is fortunate, since a cached copy would show the repository as it
    was *before* the merge and quietly verify nothing at all.

    Returns `(AgentResult, ref)`.
    """
    try:
        owner, repo, ref = parse_github_url(report.url)
    except ValueError as exc:
        raise VerifyError(f"This scan's target is not a GitHub repository: {exc}", 400) from exc

    async with httpx.AsyncClient(timeout=30.0) as client:
        # An installation token is not required to read a public repository,
        # but it raises GitHub's rate ceiling from 60 requests an hour to
        # 5000 and is the only way to see a private one at all. Verification
        # re-downloads a tarball every run, so the unauthenticated ceiling is
        # a real wall, not a theoretical one. Setting the header on the client
        # is enough -- `fetch_repo` makes all of its calls through it.
        installation = active_installation_for(user_id, owner)
        if installation is not None:
            provider = provider or default_provider()
            try:
                token = await provider.token_for(client, installation.installation_id)
            except TokenError as exc:
                raise VerifyError(str(exc), status=502) from exc
            client.headers["Authorization"] = f"Bearer {token.token}"

        try:
            async with fetch_repo(owner, repo, ref, client) as fetched:
                context = RepoContext(
                    repo_url=report.url,
                    owner=owner,
                    repo=repo,
                    ref=fetched.ref,
                    root=fetched.root,
                    files=list_repo_files(fetched.root),
                    client=client,
                )
                observed_ref = fetched.ref
                result = await agent_cls().run(context)
        except ValueError as exc:
            # Same mapping the scan endpoints use: anything fetch_repo rejects
            # (gone, renamed, too large) is the caller's problem to read, not
            # a 500.
            raise VerifyError(str(exc), status=400) from exc

    # `BaseRepoAgent.run` never raises -- it reports failure on the result. That
    # matters more here than anywhere else: a crashed agent returns *zero*
    # findings, which would compute as "everything was fixed". Refusing is the
    # only honest answer.
    if result.error:
        raise VerifyError(
            f"The {result.agent} agent failed while re-checking the repository "
            f"({result.error}), so nothing can be concluded about the fix.",
            status=502,
        )
    return result, observed_ref


async def _rerun_url_agent(report: ScanReport, agent_cls) -> tuple[AgentResult, str]:
    """Re-run a URL agent against the live site (PLAN-v5 Stage D's bridge).

    There is no git ref here -- what actually changed, if anything, is
    whatever the site's current deployment sends. The observed "ref" is the
    URL itself, since that's the one thing that was really re-read; the
    caller stores it the same way `_rerun_agent` stores a commit ref, so
    `VerificationResult.ref` always means "what was actually observed."
    """
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        context = ScanContext(url=report.url, client=client)
        result = await agent_cls().run(context)

    if result.error:
        raise VerifyError(
            f"The {result.agent} agent failed while re-checking the site "
            f"({result.error}), so nothing can be concluded about the fix.",
            status=502,
        )
    return result, report.url


async def verify_finding(
    report: ScanReport,
    user: User,
    finding_key: str,
    provider: TokenProvider | None = None,
) -> VerificationResult:
    """Re-run the agent responsible for one finding and report the delta.

    Raises `VerifyError` for every refusal. Never writes a scan row; the only
    write is the `fix_applications` update at the end, and only when there is
    a merged application to close out.
    """
    _check_ownership(report.id, user)

    finding = next((f for f in report.findings if f.id == finding_key), None)
    if finding is None:
        raise VerifyError(f"Finding {finding_key!r} is not part of this scan.", status=404)

    # `_resolve_agent` is the actual gate: a repo scan resolves a repo agent
    # unconditionally, a URL scan only ever resolves one of the four header
    # ids (PLAN-v5 Stage D), and everything else is refused there with a
    # specific reason. `report.target_type` alone is no longer enough to
    # decide this -- a "repo" scan whose agent isn't a repo agent, or a "url"
    # scan whose finding isn't a header finding, both still have to fail
    # through that same refusal, not a blanket type check here.
    agent_cls = _resolve_agent(finding)
    is_repo_agent = repo_agent_for(finding.agent) is not None
    if is_repo_agent and report.target_type != "repo":
        # Can't happen from a real scan (a URL scan never produces a repo
        # agent's findings), but a repo agent has a git ref to re-read and a
        # URL scan has none -- refuse cleanly rather than let the mismatch
        # surface as a confusing "not a GitHub repository" error later.
        raise VerifyError(
            f"Scan {report.id!r} is a URL scan; there is no repository to re-read.",
            status=400,
        )

    application = await _application_to_verify(report, user, finding_key, provider)

    if is_repo_agent:
        result, ref = await _rerun_agent(report, agent_cls, user.id, provider)
    else:
        result, ref = await _rerun_url_agent(report, agent_cls)

    # The substitution: this agent's stored findings are dropped and its fresh
    # ones take their place. Every other agent's findings stay exactly as the
    # original scan recorded them -- nothing else was re-observed, so claiming
    # anything about it would be inventing data.
    others = [f for f in report.findings if f.agent != agent_cls.name]
    before = calculate_score(report.findings, report.url)
    after = calculate_score(others + result.findings, report.url)

    was_failing = _failing_ids([f for f in report.findings if f.agent == agent_cls.name])
    still = _failing_ids(result.findings)

    verification = VerificationResult(
        scan_id=report.id,
        finding_key=finding_key,
        agent=agent_cls.name,
        ref=ref,
        verified_at=datetime.now(timezone.utc).isoformat(),
        before=before,
        after=after,
        delta=after - before,
        scorer_version=SCORER_VERSION,
        stored_scorer_version=report.scorer_version,
        stored_score=report.score,
        target_fixed=finding_key not in still,
        fixed=sorted(was_failing - still),
        still_failing=sorted(was_failing & still),
        application_id=application.id if application else None,
        recorded=False,
    )

    if application is not None:
        save_verification(application.id, verification)
        verification.recorded = True
        write_audit(
            user.id, report.id, finding_key, "fix_verified",
            f"agent={agent_cls.name} target_fixed={verification.target_fixed} "
            f"score {before}->{after} application={application.id}",
        )
    else:
        # Still worth a row: someone asked Sentinels to check, and the answer
        # is part of the history of this scan even when no PR of ours produced
        # the change (CLAUDE.md remediation rule 10).
        write_audit(
            user.id, report.id, finding_key, "fix_verified_unrecorded",
            f"agent={agent_cls.name} target_fixed={verification.target_fixed} "
            f"score {before}->{after} (no fix application for this finding)",
        )

    return verification
