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
