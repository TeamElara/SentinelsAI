"""The exact words of the branch name, commit message, PR title, and PR body.

All of it built by string formatting from data already on the `Finding` and
`FixPlan`. No model writes any of it — a pull request body is a claim about
what the code does, and CLAUDE.md's remediation rule 1 does not have an
exception for prose that happens to sit next to a diff.

Rule 9 is the reason this file exists at all rather than being three f-strings
inside `apply.py`: **every PR says what it does not fix**, and the most
important instance of that is secret removal. Deleting a committed secret from
the working tree does not rotate the credential and does not remove it from
git history — anyone with the repository still has it. A PR that quietly
implied otherwise would be actively dangerous, so the caveat is assembled
from the same table that decides which fixer ran, not left to whoever writes
the next fixer to remember.
"""
from __future__ import annotations

from models import Finding, FixPlan

# What each fixer's change deliberately leaves undone, keyed by fixer slug.
# A fixer with no entry here still gets the generic caveat below — silence is
# never read as "nothing left to do".
_LIMITATIONS: dict[str, list[str]] = {
    "ci-unpinned-action": [
        "Only the actions flagged by this scan are pinned. Other workflows, and "
        "actions added later, still need pinning.",
        "Pinning freezes each action at today's commit — it does not subscribe you to "
        "that action's future security fixes. Re-pin deliberately when you update.",
    ],
    "gitignore-present": [
        "This adds ignore rules going forward. It does **not** remove anything already "
        "committed, and it does not erase anything from git history.",
        "If a secret was ever committed, it is still in the repository's history and "
        "must be treated as compromised and rotated.",
    ],
    "repo-readme-present": [
        "This is a starting skeleton, not documentation. It says nothing true about "
        "your project until you write it.",
    ],
    "repo-env-example-present": [
        "Only the variable *names* found in your committed `.env` are listed, with "
        "every value blanked. No value from your `.env` appears in this change.",
        "This does **not** remove the committed `.env` itself, and it does not rotate "
        "any credential that file contains. Both are still your job, and urgent.",
    ],
    "docker-root-user": [
        "**Review this one before merging.** Adding a non-root `USER` can break an "
        "image that writes to paths owned by root at runtime.",
        "Anything the container does before this line still runs as root, including "
        "every earlier build step.",
    ],
    "docker-latest-tag": [
        "This pins today's resolution of `:latest` by digest. It does not switch you "
        "to a real version tag, and it does not subscribe you to that image's future "
        "security fixes -- re-resolve deliberately when you want a newer build.",
        "Only Docker Hub images were resolved. A base image from another registry "
        "would not have produced this fix.",
    ],
    "secret-env-committed": [
        "**This does not resolve the leak.** Deleting the file removes it from the "
        "working tree only -- it is still recoverable from this repository's git "
        "history by anyone who has (or ever had) read access. Rotate every credential "
        "this file contained now; that is not optional and this PR does not do it "
        "for you.",
        "If `.gitignore` does not already exclude this path, add a rule for it "
        "yourself -- otherwise the same file (or a new version of it) can be "
        "committed again. This fixer does not edit `.gitignore`.",
    ],
    "ci-pull-request-target": [
        "This only ran because the workflow showed no sign of checking out the pull "
        "request's own head (no `pull_request.head.*` / `github.head_ref` reference "
        "anywhere in the file) -- the one pattern that turns `pull_request_target` "
        "into a real risk. If that reasoning is wrong for this workflow, say so in "
        "review; this is a text-pattern check, not a full analysis of what the "
        "workflow (or anything it calls) actually does.",
        "If this workflow relied on `pull_request_target`'s elevated permissions or "
        "secret access on purpose (for example, to comment on a fork's pull request), "
        "switching to `pull_request` removes that access and may break it.",
    ],
    "repo-license-present": [
        "The license text was chosen from a license id your own `package.json` or "
        "`pyproject.toml` already declared -- Sentinels did not pick a license for "
        "you. Confirm it says what you intended before merging.",
    ],
    "repo-ci-configured": [
        "This is a minimal starting workflow, not a full CI setup. It only runs the "
        "npm scripts (`lint`/`test`/`build`) your `package.json` already defines, and "
        "nothing else in your project's actual test/build process.",
    ],
}

_GENERIC_LIMITATION = (
    "This change addresses only the specific finding above. It is not a general "
    "hardening pass, and the rest of the repository was not modified."
)

_TIER_NOTE = {
    1: "This fix is deterministic — Sentinels generated it from a fixed rule, not from a model.",
    2: "**Review required.** This fix is deterministic, but the correct result depends on "
    "context Sentinels cannot see. Read the diff before merging.",
}


def branch_name(scan_id: str, timestamp: int) -> str:
    """`sentinels/fix-<8 hex chars of scan id>-<epoch seconds>`.

    The scan id makes it traceable; the timestamp makes a second attempt on
    the same scan a different branch instead of a collision. Validated against
    a regex in `apply.py` before it is ever sent — this function producing the
    right shape and the caller checking the shape are two independent
    guarantees, and the branch name is the one string in this flow that
    becomes a permanent part of someone else's repository.
    """
    return f"sentinels/fix-{scan_id[:8]}-{timestamp}"


def commit_message(findings: list[Finding]) -> str:
    """Conventional-commit subject plus one bullet per finding.

    `fix(security):` rather than `chore:` — a reader scanning `git log` a year
    from now should be able to tell this commit changed a security property.
    """
    if len(findings) == 1:
        subject = f"fix(security): {findings[0].title}"
    else:
        subject = f"fix(security): resolve {len(findings)} findings from a Sentinels scan"

    body_lines = [f"- {finding.title} ({finding.id})" for finding in findings]
    return f"{subject}\n\n" + "\n".join(body_lines) + "\n\nApplied by Sentinels.\n"


def pull_request_title(findings: list[Finding]) -> str:
    if len(findings) == 1:
        return f"Sentinels: {findings[0].title}"
    return f"Sentinels: {len(findings)} security fixes"


def _finding_section(finding: Finding, plan: FixPlan) -> str:
    paths = ", ".join(f"`{patch.path}`" for patch in plan.patches) or "_no files_"
    lines = [
        f"### {finding.title}",
        "",
        f"- **Finding ID:** `{finding.id}`",
        f"- **Severity:** {finding.severity.value if hasattr(finding.severity, 'value') else finding.severity}",
        f"- **Files changed:** {paths}",
        "",
        f"**What was wrong.** {finding.description or finding.evidence or 'See the finding above.'}",
        "",
        f"**What this change does.** {plan.summary}",
        "",
        "**What this change does _not_ do.**",
        "",
    ]
    for limitation in _LIMITATIONS.get(plan.fixer_slug, []):
        lines.append(f"- {limitation}")
    lines.append(f"- {_GENERIC_LIMITATION}")
    lines.append("")
    note = _TIER_NOTE.get(plan.tier)
    if note:
        lines.append(note)
        lines.append("")
    return "\n".join(lines)


def pull_request_body(
    scan_id: str, pairs: list[tuple[Finding, FixPlan]], scan_url: str = ""
) -> str:
    """The full PR description: one section per finding, then the standing
    disclaimer that applies to the whole thing."""
    header = [
        "## Automated security fix from Sentinels",
        "",
        "Sentinels scanned this repository and produced the change below with a "
        "deterministic fixer — plain Python matching a known pattern. **No language "
        "model wrote any part of this diff.**",
        "",
        f"- **Scan:** `{scan_id}`",
    ]
    if scan_url:
        header.append(f"- **Target:** {scan_url}")
    header.extend(["", "---", ""])

    sections = [_finding_section(finding, plan) for finding, plan in pairs]

    footer = [
        "---",
        "",
        "### Before you merge",
        "",
        "- Read the diff. Sentinels opened a pull request precisely so that a human "
        "sees the change before it lands — it has no ability to merge this itself.",
        "- Run your test suite. Sentinels did not run it.",
        "- Nothing outside the files listed above was touched, and no branch other "
        "than this one was created or modified.",
        "",
    ]
    return "\n".join(header + sections + footer)
