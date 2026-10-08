"""Deleting an account (Launch Plan A7).

What "delete my account" means, so it can be stated in the privacy notice and
tested:

**Deleted, in one transaction**
* the user row, and with it their sessions, their GitHub installation records
  and their scan-to-repository links (cascading foreign keys);
* every scan they own, and with it everything hanging off a scan: agent runs,
  findings and evidence, checklist answers, AI fix suggestions, chat messages,
  repository file listings, discovered subdomains, fix plans and fix
  applications (cascading foreign keys).

**Kept, detached from the person**
* `audit_log` rows. They record what Sentinels did (a scan someone said they
  could test, a pull request it opened on GitHub), and the pull requests
  themselves stay on GitHub whatever happens here, so the record of who
  caused them to exist stays too, as an anonymous row: `user_id` is cleared,
  and `scan_id` is cleared by its own foreign key when the scan goes. The
  `detail` text (a URL, a repository, a PR number) is left as written.

**Not in this database's reach**
* the App itself, which stays installed on GitHub until the user removes it
  there (the account page says so);
* pull requests already opened on GitHub;
* host-level backups, which age out on the host's own schedule.

Scans taken before accounts existed have no `user_id`, belong to nobody, and
are untouched.
"""
from __future__ import annotations

from db import get_connection


def delete_account(user_id: int) -> dict[str, int]:
    """Delete one user and everything they own. Returns what went, by kind.

    All or nothing: one connection, one commit, so a failure half way leaves
    the account exactly as it was rather than half deleted.
    """
    conn = get_connection()
    try:
        counts = {
            "scans": conn.execute(
                "SELECT COUNT(*) AS n FROM scans WHERE user_id = ?", (user_id,)
            ).fetchone()["n"],
            "installations": conn.execute(
                "SELECT COUNT(*) AS n FROM github_installations WHERE user_id = ?", (user_id,)
            ).fetchone()["n"],
            "audit_rows_kept": conn.execute(
                "SELECT COUNT(*) AS n FROM audit_log WHERE user_id = ?", (user_id,)
            ).fetchone()["n"],
        }
        conn.execute("UPDATE audit_log SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM scans WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
