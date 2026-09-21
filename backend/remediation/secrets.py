"""Fixer for `secret-env-committed-*` (`agents/repo/secrets.py`'s
`_env_file_finding` -- a `.env`-shaped file committed to the repository).

The deferred note this fixer finally answers (PLAN-v5.md, "Deferred beyond
Stage F") flagged that it "needs its own read of CLAUDE.md's remediation
rule 9 before it's built": *"Every PR says what it does not fix. Especially
secret removal: removing a committed secret does not rotate it and does not
erase it from git history, and the PR body must say so in full."* That text
lives in `remediation/pr_body.py`'s `_LIMITATIONS["secret-env-committed"]`
entry, assembled into every PR this fixer's plan ends up in -- never left to
this module to restate (and risk drifting from) in its own words.

Delete-only, deliberately. `validate_plan`'s path-containment rule means a
plan for a finding with a `file_path` may only ever touch that one path --
so this fixer cannot also add a `.gitignore` rule in the same plan (a
second path), the same reason `GitignoreFixer` never touches an *existing*
`.gitignore` either. "Ensure the path is in .gitignore" is therefore not
something this fixer's diff can do; it is the PR body's job to say so
explicitly, which it does.

Gated by `DELETE_ALLOWLIST` (`remediation/patch.py`), which only lists a
small, closed set of canonical root-level `.env`-shaped filenames. A
committed secret file at a nested path (`backend/.env`, `apps/api/.env.local`)
declines rather than being deleted -- the same "a static allowlist can only
ever hold exact, developer-reviewed strings" limit `DELETE_ALLOWLIST`'s own
comment explains.
"""
from __future__ import annotations

from datetime import datetime, timezone

from models import Finding, FixPlan
from remediation.base import Fixer
from remediation.patch import DELETE_ALLOWLIST, make_patch
from remediation.source import FileSource


class SecretEnvCommittedFixer(Fixer):
    slug = "secret-env-committed"
    display_name = "Remove the committed .env file"

    def handles(self, finding: Finding) -> bool:
        return finding.id.startswith("secret-env-committed-")

    async def plan(self, finding: Finding, files: FileSource) -> FixPlan | None:
        if not finding.file_path:
            return None
        if finding.file_path not in DELETE_ALLOWLIST:
            return None  # not one of the canonical root-level names -- decline rather than guess

        source = await files.get(finding.file_path)
        if source is None:
            return None  # already gone since the scan ran

        patch = make_patch(finding.file_path, "delete", source, None)
        return FixPlan(
            finding_key=finding.id,
            fixer_slug=self.slug,
            tier=2,
            summary=f"Remove the committed {finding.file_path} from the repository.",
            patches=[patch],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
