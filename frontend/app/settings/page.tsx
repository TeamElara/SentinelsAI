"use client";

/* /settings — the page that was missing.

   PLAN-v5 Stage B's install callback has always redirected here
   (`{frontend}/settings?installed=...`), so until this file existed, connecting
   a repository ended on a 404 even though the backend write had succeeded.

   It does three things: says who you're signed in as, lists the repository
   grants Sentinels holds, and gives you a way to add or drop one. Both of the
   navigations that leave for github.com are plain <a> links, not fetches — a
   `fetch` can't hand a redirect to the browser's address bar. */

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  fetchInstallations,
  githubInstallUrl,
  logout,
  revokeInstallation,
  deleteAccount,
  type GitHubInstallation,
} from "@/lib/api";
import { useSession } from "@/lib/useSession";

const INSTALL_ERRORS: Record<string, string> = {
  state_mismatch: "That install link expired or was already used. Try again.",
  missing_installation: "GitHub didn't say which installation was created. Try again.",
  not_signed_in: "Your session ended before the install finished. Sign in and try again.",
  installation_lookup_failed:
    "GitHub wouldn't tell us which account that installation covers, so it wasn't saved.",
  authorization_required:
    "GitHub didn't confirm who you are during the install, so it wasn't saved. Try connecting again.",
  authorization_failed:
    "GitHub rejected the sign-in step of the install, so it wasn't saved. Try connecting again.",
  identity_mismatch:
    "The GitHub account that finished the install isn't the one you're signed in with, so it wasn't saved.",
  installation_not_yours:
    "That installation isn't one your GitHub account has access to, so it wasn't saved.",
  installation_taken:
    "Another Sentinels user already has that installation connected, so it wasn't saved.",
};

const SELECTION_LABEL: Record<string, string> = {
  all: "All repositories",
  selected: "Selected repositories",
};

/* The banner the install callback lands on. In its own component because
   `useSearchParams` forces the nearest boundary to render on the client only,
   and there's no reason for the whole page to pay that — same split the login
   page already uses. */
function InstallBanner() {
  const params = useSearchParams();
  const installed = params.get("installed");
  const error = params.get("install_error");

  if (error) {
    return (
      <p className="glass mt-6 border-critical/40 px-4 py-3 font-mono text-[10px] uppercase tracking-[0.2em] text-critical">
        {INSTALL_ERRORS[error] ?? "Connecting that repository failed. Try again."}
      </p>
    );
  }
  if (installed) {
    return (
      <p className="glass mt-6 px-4 py-3 font-mono text-[10px] uppercase tracking-[0.2em] text-parchment">
        Connected — {installed}
      </p>
    );
  }
  return null;
}

export default function SettingsPage() {
  const router = useRouter();
  const { user, loading: sessionLoading } = useSession();

  const [installations, setInstallations] = useState<GitHubInstallation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [typedLogin, setTypedLogin] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleted, setDeleted] = useState(false);

  // Called from event handlers (after a disconnect), never from the effect
  // below — see the comment there.
  const load = useCallback(async () => {
    try {
      setInstallations(await fetchInstallations());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load connected repositories.");
    }
  }, []);

  useEffect(() => {
    // The session hook answers first; only then is there any point asking for
    // this user's installations. Signed out, lib/api's shared 401 handler has
    // already bounced to /login — this redirect is for the case where
    // `fetchMe` simply said "nobody", which returns null without a 401 path.
    if (sessionLoading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    // Deliberately `.then(...)` rather than awaiting `load()`: state may only
    // be set from a *callback* an effect subscribes to, never in the effect's
    // own body, or React re-renders in a cascade (the react-hooks/
    // set-state-in-effect rule). Same shape `lib/useSession.ts` uses.
    let cancelled = false;
    fetchInstallations()
      .then((rows) => {
        if (!cancelled) setInstallations(rows);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Couldn't load connected repositories.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionLoading, user, router]);

  async function disconnect(installation: GitHubInstallation) {
    setBusyId(installation.installation_id);
    try {
      await revokeInstallation(installation.installation_id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't disconnect that installation.");
    } finally {
      setBusyId(null);
    }
  }

  async function signOut() {
    await logout();
    router.replace("/login");
  }

  async function removeAccount() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteAccount(typedLogin);
      setDeleted(true);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Couldn't delete the account.");
    } finally {
      setDeleting(false);
    }
  }

  const live = (installations ?? []).filter((i) => i.revoked_at === null);
  const revoked = (installations ?? []).filter((i) => i.revoked_at !== null);

  if (deleted) {
    return (
      <main className="mx-auto w-full max-w-3xl px-6 py-20 sm:px-8">
        <h1 className="font-display text-5xl sm:text-6xl">Account deleted</h1>
        <p className="mt-6 max-w-2xl text-sm leading-relaxed text-muted sm:text-base">
          Your scans, reports, chat history and connected-repository records are gone from
          Sentinels. Pull requests Sentinels already opened stay on GitHub, and the Sentinels
          App stays installed there until you remove it: open your GitHub settings, then
          Applications, then Installed GitHub Apps, and uninstall Sentinels from each account.
        </p>
        <Link
          href="/"
          className="glass mt-10 inline-block px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10"
        >
          Back to the start
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-6 py-20 sm:px-8">
      <div className="flex items-center justify-between">
        <Link
          href="/"
          className="font-mono text-xs uppercase tracking-[0.35em] text-muted transition-colors hover:text-parchment"
        >
          Sentinels
        </Link>
        {/* PLAN-v5 Stage E: the audit browser's other entry point besides the
            per-scan nav — Settings and Audit are the two account-wide pages,
            so each links to the other. */}
        <Link
          href="/audit"
          className="font-mono text-xs uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
        >
          Audit
        </Link>
      </div>

      <h1 className="mt-8 font-display text-5xl sm:text-6xl">Settings</h1>

      {user && (
        <p className="mt-4 font-mono text-xs uppercase tracking-[0.2em] text-muted">
          Signed in as {user.github_login}
          <button
            type="button"
            onClick={signOut}
            className="ml-4 uppercase tracking-[0.2em] text-muted underline decoration-rule transition-colors hover:text-parchment"
          >
            Sign out
          </button>
        </p>
      )}

      <Suspense fallback={null}>
        <InstallBanner />
      </Suspense>

      <section className="mt-14">
        <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
          Connected repositories
        </h2>

        <p className="mt-5 max-w-2xl text-sm leading-relaxed text-muted sm:text-base">
          Connecting an account lets Sentinels open pull requests on it. It can create a
          branch named <span className="font-mono text-xs text-parchment">sentinels/…</span>{" "}
          and open one pull request against it — nothing else. It never pushes to your
          default branch, never merges, and never force-pushes. Every change is shown to
          you as a diff and approved by you before it is written.
        </p>

        {error && (
          <p className="glass mt-6 px-4 py-3 font-mono text-[10px] uppercase tracking-[0.2em] text-critical">
            {error}
          </p>
        )}

        {installations === null ? (
          <p className="mt-8 animate-pulse font-mono text-[10px] uppercase tracking-[0.25em] text-muted">
            Loading…
          </p>
        ) : live.length === 0 ? (
          <p className="mt-8 text-sm text-muted">
            No repositories connected yet. Scans work without this — it&apos;s only needed
            to open a pull request for a fix.
          </p>
        ) : (
          <ul className="mt-8 space-y-5">
            {live.map((installation) => (
              <li
                key={installation.installation_id}
                className="flex flex-wrap items-baseline justify-between gap-3 border-l-2 border-rule pl-5"
              >
                <div>
                  <p className="text-lg leading-snug">{installation.account_login}</p>
                  <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
                    {SELECTION_LABEL[installation.repo_selection] ?? installation.repo_selection}
                    {" · connected "}
                    {new Date(installation.created_at).toLocaleDateString()}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => disconnect(installation)}
                  disabled={busyId === installation.installation_id}
                  className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-critical disabled:opacity-40"
                >
                  {busyId === installation.installation_id ? "Disconnecting…" : "Disconnect"}
                </button>
              </li>
            ))}
          </ul>
        )}

        <a
          href={githubInstallUrl()}
          className="glass mt-10 inline-block border-parchment/25 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10"
        >
          {live.length === 0 ? "Connect a repository →" : "Connect another →"}
        </a>

        {revoked.length > 0 && (
          <p className="mt-8 font-mono text-[10px] uppercase tracking-[0.2em] text-rule">
            Previously connected — {revoked.map((i) => i.account_login).join(" · ")}
          </p>
        )}

        {/* Said plainly, because "Disconnect" above cannot possibly mean more
            than this: Sentinels stops using the grant, GitHub still lists the
            App as installed until the user removes it there. */}
        <p className="mt-10 max-w-2xl font-mono text-[9px] leading-relaxed text-rule">
          Disconnecting stops Sentinels using an installation immediately. It does not
          uninstall the App on GitHub — do that from your GitHub settings if you want the
          grant gone on their side too.
        </p>
      </section>

      <section className="mt-20 border-t border-rule pt-10">
        <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
          Delete account
        </h2>
        <p className="mt-5 max-w-2xl text-sm leading-relaxed text-muted sm:text-base">
          This deletes your scans and reports, findings, chat history, planned fixes, and
          your connected-repository records. It can&apos;t be undone. A short record that a
          scan or a pull request happened is kept without your name on it. Pull requests
          already opened stay on GitHub, and you remove the Sentinels App from your GitHub
          settings yourself.
        </p>

        {!confirmingDelete ? (
          <button
            type="button"
            onClick={() => setConfirmingDelete(true)}
            className="mt-8 font-mono text-[10px] uppercase tracking-[0.2em] text-muted underline decoration-rule transition-colors hover:text-critical"
          >
            Delete my account…
          </button>
        ) : (
          <div className="mt-8 max-w-md">
            <label
              htmlFor="confirm-login"
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted"
            >
              Type your GitHub login ({user?.github_login}) to confirm
            </label>
            <input
              id="confirm-login"
              type="text"
              value={typedLogin}
              onChange={(event) => setTypedLogin(event.target.value)}
              autoComplete="off"
              disabled={deleting}
              className="mt-3 w-full border-b border-rule bg-transparent pb-2 font-mono text-base outline-none focus:border-parchment disabled:text-muted"
            />
            {deleteError && (
              <p className="mt-4 font-mono text-[10px] uppercase tracking-[0.2em] text-critical">
                {deleteError}
              </p>
            )}
            <div className="mt-6 flex gap-6">
              <button
                type="button"
                onClick={removeAccount}
                disabled={
                  deleting ||
                  typedLogin.trim().toLowerCase() !== (user?.github_login ?? "").toLowerCase()
                }
                className="font-mono text-[10px] uppercase tracking-[0.2em] text-critical transition-opacity disabled:opacity-40"
              >
                {deleting ? "Deleting…" : "Delete everything"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmingDelete(false);
                  setTypedLogin("");
                  setDeleteError(null);
                }}
                disabled={deleting}
                className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
