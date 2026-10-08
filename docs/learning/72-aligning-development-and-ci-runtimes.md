# One runtime baseline

The launch baseline is Python 3.13 and Node 24 LTS. CI, backend/.python-version,
frontend/.nvmrc and package engines now agree with the running instructions.
Use npm ci to preserve the dependency lock; declaring engines is not a reason
to regenerate unrelated package metadata.

CI runs on task branches and all pull requests, including stacked task PRs.
Only read access to repository contents is granted to the workflow. The
backend still runs the full offline suite; frontend lint and production build
remain required. Render/Vercel runtime settings must also be checked before
deploying, since a source declaration does not prove the hosting runtime.
# Dependency patch check

Next.js and eslint-config-next now use 16.3.8. The lockfile also updates sharp
and source-map-js to their patched releases. `npm audit --omit=dev` must remain
clean before release. The remaining braces advisory is in the ESLint tooling
chain; the registry has no patched braces release. Do not downgrade Next's
ESLint configuration to 14.x just to silence the audit. CI lints only trusted
repository patterns, and this dependency is excluded from production installs.
Track https://github.com/advisories/GHSA-vfj7-8cjw-p6xm for a compatible patch.

The backend production audit also identified two PyJWT 2.14.0 advisories.
Upgrade to 2.15.1, retain the existing JWT tests, and run pip-audit against
requirements.txt in CI alongside the frontend production dependency audit.
