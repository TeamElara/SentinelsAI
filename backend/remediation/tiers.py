"""ID-prefix -> fixability tier lookup (docs/PLAN-v5.md's "Fixability tiers"
table). Tier is a property of the *finding type*, not of whether a Fixer
happens to exist for it -- most tier-3 and every tier-4 finding never gets
a Fixer at all; this table is what a UI badge reads even then.

| Tier | Meaning                              | UI reads              |
|------|---------------------------------------|------------------------|
| 1    | Deterministic, safe to PR             | Fix available          |
| 2    | Generated, human must check           | Review required        |
| 3    | We can say what to do, can't do it    | Manual action required |
| 4    | Never auto-fix                        | Suggestion only        |
"""
from __future__ import annotations

from models import Finding

# Only these two tiers ever get a FixPlan -- see remediation/patch.py's
# validate_plan(), which enforces this as defense in depth even though no
# tier-3/4 finding has a registered Fixer today.
PLANNABLE_TIERS = (1, 2)

# Findings whose `Finding.id` is one exact, fixed string.
_EXACT_TIER: dict[str, int] = {
    "gitignore-present": 1,
    "repo-readme-present": 1,
    "repo-env-example-present": 1,
    # Phase 1: a Fixer now exists for both (LicenseFixer, CiScaffoldFixer,
    # remediation/scaffolding.py) that only ever materializes a choice the
    # repo already made for itself -- a declared SPDX license id, or the
    # existing npm scripts a package.json already defines -- never a guess
    # of its own, so tier 1 (not the review-required tier 2 a fixer that
    # picks its own default gets) is correct, same reasoning as
    # `repo-readme-present`/`repo-env-example-present` above.
    "repo-license-present": 1,
    "repo-ci-configured": 1,
    # PLAN-v5 Stage D: corrected from the unlisted default (tier 4) once a
    # Fixer (`remediation/headers_fix.py`) actually exists for these --
    # review-required, since a header value this fixer writes is a
    # reasonable default, not the site's own maintainer's choice.
    "missing-csp": 2,
    "missing-hsts": 2,
    "missing-x-content-type-options": 2,
    "missing-x-frame-options": 2,
    "api-cors-permissive": 2,
    "sensitive-response-cacheable": 2,
    "server-version-disclosed": 2,
    "risky-http-methods": 2,
    "spf-record": 3,
    "dmarc-record": 3,
    "dir-listing": 3,
    "env-file-exposed": 3,
    "git-directory-exposed": 3,
    "backup-file-exposed": 3,
    "setup-page-exposed": 3,
    "subdomain-takeover-potential": 4,
    "subdomain-dangling-dns": 4,
}

# Findings whose `Finding.id` carries a dynamic suffix (a file slug, a line
# number). Checked with `str.startswith`, in no particular priority order --
# none of these prefixes overlap.
_PREFIX_TIER: list[tuple[str, int]] = [
    ("ci-unpinned-action-", 1),
    ("docker-root-user-", 2),
    ("dependency-", 2),
    ("docker-latest-tag-", 2),
    ("secret-env-committed-", 2),
    ("ci-pull-request-target-", 2),
    ("tls-", 3),
    ("pattern-", 4),
]


def tier_for(finding: Finding) -> int:
    """4 (suggestion-only) is the default for anything not explicitly
    listed above -- an unrecognized finding is never silently treated as
    auto-fixable (CLAUDE.md: "confidence is stated, never implied")."""
    if finding.confidence is not None:
        return 4
    if finding.id.endswith("-scan-partial"):
        return 4
    if finding.id in _EXACT_TIER:
        return _EXACT_TIER[finding.id]
    for prefix, tier in _PREFIX_TIER:
        if finding.id.startswith(prefix):
            return tier
    return 4
