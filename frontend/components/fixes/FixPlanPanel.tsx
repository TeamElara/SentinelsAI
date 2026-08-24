"use client";

/* A deterministic fix (PLAN-v5 Stage A) -- plain Python decided every byte
   of this diff, no model involved. Renders ABOVE FixSuggestionPanel when a
   finding has one; unlike that panel, checking here can come back with
   "no automatic fix", which just means the AI explanation below is this
   finding's only option -- not an error.

   Deliberately manual-trigger, same idle/loading/error shape as
   FixSuggestionPanel: fetching a plan means Sentinels re-reads the file from
   GitHub right now, and doing that automatically for every finding a repo
   scan's findings list renders would burn through GitHub's unauthenticated
   rate limit before a user looks at a single one. */

import { useEffect, useState } from "react";
import {
  ApiError,
  downloadFixBundle,
  fetchFixPlan,
  fetchInstallations,
  fetchScanRepoLink,
  linkScanRepo,
  saveFixPlan,
  unlinkScanRepo,
  type FixPlan,
  type GitHubInstallation,
  type ScanRepoLink,
} from "@/lib/api";
import { FixApplyPanel } from "@/components/fixes/FixApplyPanel";

interface Props {
  scanId: string;
  findingKey: string;
  // PLAN-v5 Stage D: a URL scan's header findings have no repository of
  // their own to patch until one is linked. Only URL-scan panels ever try
  // to render the inline link form below -- a repo scan's 400 means
  // something else went wrong, not "link a repository".
  linkable?: boolean;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "unavailable" }
  | { kind: "needs-link" }
  | { kind: "linking" }
  | { kind: "preview"; plan: FixPlan }
  | { kind: "saving"; plan: FixPlan }
  | { kind: "saved"; plan: FixPlan }
  | { kind: "error"; message: string };

const TIER_LABEL: Record<number, string> = {
  1: "Fix available",
  2: "Review required",
};

export function FixPlanPanel({ scanId, findingKey, linkable = false }: Props) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [bundleError, setBundleError] = useState<string | null>(null);
  // Only ever populated for a linkable (URL-scan) panel, and only once
  // `check()` has actually confirmed a repository is linked -- fetching it
  // eagerly on mount would fire one request per finding's own panel before
  // anyone asked, the exact per-finding cost the module comment above says
  // this component avoids on purpose.
  const [repoLink, setRepoLink] = useState<ScanRepoLink | null>(null);
  const [unlinking, setUnlinking] = useState(false);
  const [unlinkError, setUnlinkError] = useState<string | null>(null);

  async function check() {
    setState({ kind: "loading" });
    try {
      const plan = await fetchFixPlan(scanId, findingKey);
      setState(plan ? { kind: "preview", plan } : { kind: "unavailable" });
      if (linkable) {
        fetchScanRepoLink(scanId).then(setRepoLink).catch(() => {});
      }
    } catch (err) {
      if (linkable && err instanceof ApiError && /linked repository/.test(err.message)) {
        setState({ kind: "needs-link" });
        return;
      }
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to check for an automatic fix.",
      });
    }
  }

  async function link(installationId: number, repo: string) {
    setState({ kind: "linking" });
    try {
      await linkScanRepo(scanId, installationId, repo);
      await check();
    } catch (err) {
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to link that repository.",
      });
    }
  }

  // Unlinking invalidates whatever this panel already checked -- the plan
  // (if any) was computed against a repository that's no longer linked, so
  // dropping back to "idle" is the honest state, not a stale "Fix
  // available" that a re-check would immediately contradict.
  async function unlink() {
    setUnlinking(true);
    setUnlinkError(null);
    try {
      await unlinkScanRepo(scanId);
      setRepoLink(null);
      setState({ kind: "idle" });
    } catch (err) {
      setUnlinkError(err instanceof Error ? err.message : "Couldn't unlink that repository.");
    } finally {
      setUnlinking(false);
    }
  }

  async function save(plan: FixPlan) {
    setState({ kind: "saving", plan });
    try {
      const saved = await saveFixPlan(scanId, findingKey);
      setState(saved ? { kind: "saved", plan: saved } : { kind: "unavailable" });
    } catch (err) {
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to save the fix plan.",
      });
    }
  }

  async function copyDiff(plan: FixPlan) {
    const text = plan.patches.map((p) => p.diff).join("\n");
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard permission denial is rare and non-critical here -- the
      // diff is already visible on screen to select by hand.
    }
  }

  async function handleDownloadBundle() {
    setBundleError(null);
    try {
      await downloadFixBundle(scanId);
    } catch {
      setBundleError("Couldn't download the patch bundle. Try again.");
    }
  }

  if (state.kind === "idle") {
    return (
      <button
        type="button"
        onClick={check}
        className="glass mt-4 mr-3 border-parchment/25 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10"
      >
        Check for automatic fix →
      </button>
    );
  }

  if (state.kind === "loading") {
    return (
      <div className="glass mt-4 px-4 py-3">
        <p className="animate-pulse font-mono text-[10px] uppercase tracking-[0.25em] text-muted">
          Reading the repo…
        </p>
      </div>
    );
  }

  if (state.kind === "needs-link" || state.kind === "linking") {
    return (
      <LinkRepoForm
        linking={state.kind === "linking"}
        onLink={link}
        onCancel={() => setState({ kind: "idle" })}
      />
    );
  }

  // No deterministic fixer for this finding -- a normal outcome, not an
  // error. Rendered as a small dismissible note rather than nothing at all,
  // so the click that got here doesn't feel like it went nowhere.
  if (state.kind === "unavailable") {
    return (
      <div className="mt-3 space-y-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
          No automatic fix for this finding — try the AI suggestion below.
        </p>
        {linkable && repoLink && (
          <UnlinkRepoLine
            repoLink={repoLink}
            unlinking={unlinking}
            error={unlinkError}
            onUnlink={unlink}
          />
        )}
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="glass mt-4 px-4 py-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-critical">
          {state.message}
        </p>
        <button
          type="button"
          onClick={() => setState({ kind: "idle" })}
          className="mt-2 font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
        >
          Dismiss
        </button>
      </div>
    );
  }

  const { plan } = state;

  return (
    <div className="glass mt-4 space-y-4 px-5 py-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[9px] uppercase tracking-[0.3em] text-muted">
          Deterministic fix
        </p>
        <span className="font-mono text-[9px] uppercase tracking-[0.25em] text-parchment">
          {TIER_LABEL[plan.tier] ?? "Fix available"}
        </span>
      </div>

      <p className="text-sm leading-relaxed">{plan.summary}</p>

      {plan.patches.map((patch) => (
        <DiffView key={patch.path} patch={patch} />
      ))}

      <div className="flex flex-wrap gap-3 pt-1">
        {state.kind === "preview" && (
          <button
            type="button"
            onClick={() => save(plan)}
            className="border border-parchment/25 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10"
          >
            Save fix plan
          </button>
        )}
        {state.kind === "saving" && (
          <p className="animate-pulse font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
            Saving…
          </p>
        )}
        {state.kind === "saved" && (
          <>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
              Saved — included in the fix bundle
            </p>
            <button
              type="button"
              onClick={handleDownloadBundle}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
            >
              Download patch bundle
            </button>
          </>
        )}
        {bundleError && (
          <p className="w-full font-mono text-[10px] uppercase tracking-[0.2em] text-critical">
            {bundleError}
          </p>
        )}
        <button
          type="button"
          onClick={() => copyDiff(plan)}
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
        >
          Copy diff
        </button>
      </div>

      {/* No GitHub write happens above this line -- "Save fix plan" only
          persists the plan so it survives a refresh and lands in the bundle.
          Said explicitly so "Save" never reads as "this just opened a pull
          request"; the panel below is where a write can actually happen, and
          it asks first. */}
      <p className="font-mono text-[8px] text-rule">
        Saving stores the plan. Nothing reaches GitHub until you approve the pull request below.
      </p>

      {linkable && repoLink && (
        <UnlinkRepoLine
          repoLink={repoLink}
          unlinking={unlinking}
          error={unlinkError}
          onUnlink={unlink}
        />
      )}

      {/* PLAN-v5 Stages B + C. Mounted only once a plan exists, so a findings
          list full of unfixable findings costs no requests. */}
      <FixApplyPanel scanId={scanId} findingKey={findingKey} />
    </div>
  );
}

/* PLAN-v5 Stage D: a header finding has no repository of its own, so the
   first time anyone checks for a fix on a URL scan, the backend says so
   (409/400 "linked repository") instead of a plan. This is the inline form
   that closes that gap -- pick an installation already granted, type the
   repo name under it, link, and immediately re-check. `owner` is never
   typed: it comes from whichever installation is selected. */
function LinkRepoForm({
  linking,
  onLink,
  onCancel,
}: {
  linking: boolean;
  onLink: (installationId: number, repo: string) => void;
  onCancel: () => void;
}) {
  const [installations, setInstallations] = useState<GitHubInstallation[] | null>(null);
  const [installationId, setInstallationId] = useState<number | null>(null);
  const [repo, setRepo] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    fetchInstallations()
      .then((found) => {
        setInstallations(found);
        setInstallationId(found[0]?.installation_id ?? null);
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Couldn't load connected repositories."));
  }, []);

  return (
    <div className="glass mt-4 space-y-3 px-5 py-5">
      <p className="font-mono text-[9px] uppercase tracking-[0.3em] text-muted">
        Link a repository
      </p>
      <p className="text-sm leading-relaxed">
        This site&apos;s security headers live in its deployment config, not on this scan. Link the
        repository that serves it to check for an automatic fix.
      </p>

      {loadError && (
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-critical">{loadError}</p>
      )}

      {installations !== null && installations.length === 0 && (
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
          No connected repositories yet — connect one from{" "}
          <a href="/settings" className="underline hover:text-parchment">
            Settings
          </a>{" "}
          first.
        </p>
      )}

      {installations !== null && installations.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={installationId ?? ""}
            onChange={(e) => setInstallationId(Number(e.target.value))}
            className="glass border-parchment/25 bg-transparent px-3 py-2 font-mono text-xs text-parchment"
          >
            {installations.map((i) => (
              <option key={i.installation_id} value={i.installation_id}>
                {i.account_login}
              </option>
            ))}
          </select>
          <input
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="repo-name"
            className="glass border-parchment/25 bg-transparent px-3 py-2 font-mono text-xs text-parchment placeholder:text-muted"
          />
          <button
            type="button"
            disabled={linking || !installationId || !repo.trim()}
            onClick={() => installationId && onLink(installationId, repo.trim())}
            className="border border-parchment/25 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10 disabled:opacity-40"
          >
            {linking ? "Linking…" : "Link repository"}
          </button>
        </div>
      )}

      <button
        type="button"
        onClick={onCancel}
        className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
      >
        Cancel
      </button>
    </div>
  );
}

/* The other end of LinkRepoForm above -- once `check()` has confirmed which
   repository is linked, this is the one place in the UI that can undo it
   (PLAN-v5 Stage D's `unlinkScanRepo` was plumbed through Stage E but never
   wired to a control until now). Unlinking here doesn't remove the
   installation itself -- that's Settings' job -- only this scan's bridge
   to it. */
function UnlinkRepoLine({
  repoLink,
  unlinking,
  error,
  onUnlink,
}: {
  repoLink: ScanRepoLink;
  unlinking: boolean;
  error: string | null;
  onUnlink: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
        Linked to {repoLink.owner}/{repoLink.repo}
      </p>
      <button
        type="button"
        disabled={unlinking}
        onClick={onUnlink}
        className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-critical disabled:opacity-40"
      >
        {unlinking ? "Unlinking…" : "Unlink repository"}
      </button>
      {error && (
        <p className="w-full font-mono text-[10px] uppercase tracking-[0.2em] text-critical">
          {error}
        </p>
      )}
    </div>
  );
}

function DiffView({ patch }: { patch: FixPlan["patches"][number] }) {
  const lines = patch.diff.split("\n").filter((line) => line.length > 0);
  return (
    <div>
      <p className="font-mono text-[9px] uppercase tracking-[0.25em] text-muted">
        {patch.action} · {patch.path}
      </p>
      <pre className="mt-2 overflow-x-auto rounded border border-rule bg-transparent px-3 py-3 font-mono text-xs leading-relaxed">
        {lines.map((line, i) => (
          <div
            key={i}
            className={
              line.startsWith("+") && !line.startsWith("+++")
                ? "text-parchment"
                : line.startsWith("-") && !line.startsWith("---")
                  ? "text-muted line-through decoration-muted/40"
                  : line.startsWith("@@")
                    ? "text-muted"
                    : "text-rule"
            }
          >
            {line}
          </div>
        ))}
      </pre>
    </div>
  );
}
