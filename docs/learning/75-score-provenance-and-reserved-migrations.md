# C3: retain the scoring version

Migration 16 labels existing scores legacy-v1 without recalculating them. New
URL and repo scans persist canonical-v2. Report/history APIs expose the version;
the report displays it. Verification computes both before and after with the
current scorer and records that version alongside the original stored score
and its version.

Migration 15 belongs to Track A. Because independently reviewed branches can
arrive in either order, init_db now records applied versions individually.
Keep this runner when resolving Track A's db.py integration, then insert A's
15 into MIGRATIONS; it will run even if 16 has already run. Each migration and
its ledger entry execute in one transaction, preventing a partially applied
ALTER from being retried as though it never happened.

Tests cover v14 upgrades with old rows, new round trips, migration 15 arriving
after 16, repeat startup and rollback of a failed migration.
