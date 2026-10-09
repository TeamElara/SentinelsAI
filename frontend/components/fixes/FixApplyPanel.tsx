"use client";

/* The pull-request half of a deterministic fix (PLAN-v5 Stages B and C).

   `FixPlanPanel` above this shows the diff. This takes the same fix the rest of
   the way: dry run → open one pull request → you merge it → re-run the agent and
   show the real score delta.

   Two rules from CLAUDE.md shape the whole component:

   - **Always preview before pushing.** The first button runs a dry run. It
     performs every check the live call performs and writes nothing, so what you
     approve is exactly what gets pushed — not a guess at it.
   - **Sentinels never merges.** There is no merge button here and there is no
     merge endpoint behind it. Merging is a thing you do on GitHub, and until you
     do, Verify refuses with "not merged yet" rather than re-observing the old
     state of the repo and blaming the fixer.

   The whole panel only mounts once a plan exists (a user already clicked
   "Check for automatic fix"), so nothing here fetches on page load — the same
   rate-limit reasoning as `FixPlanPanel`'s manual trigger. */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  applyFix,
  fetchFixApplications,
  isApplyPreview,
  saveFixPlan,
  verifyFinding,
  type FixApplication,
  type FixApplyPreview,
  type VerificationResult,
} from "@/lib/api";

interface Props {
  scanId: string;
  findingKey: string;
}

/* Five separate pieces of state, each answering one question, rather than one
   big union. It matters here because a failed request must not erase what we
   already knew: a 409 from Verify is a message to show *next to* the pull
   request, not a reason to forget the pull request exists. */
type Phase = "loading" | "idle" | "working" | "ready";
type Notice = { kind: "error" | "info" | "access"; text: string };

// A state that means "this attempt is over" — a row in one of these is history,
// not something to offer buttons for.
const TERMINAL: FixApplication["state"][] = ["failed", "abandoned"];

const STATE_LABEL: Record<FixApplication["state"], string> = {
  planned: "Planned",
  pr_open: "Pull request open",
  merged: "Merged",
  verified: "Verified",
  failed: "Failed",
  abandoned: "Closed without merging",
};

export function FixApplyPanel({ scanId, findingKey }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [application, setApplication] = useState<FixApplication | null>(null);
  const [preview, setPreview] = useState<FixApplyPreview | null>(null);
  const [verification, setVerification] = useState<VerificationResult | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  const adopt = useCallback((row: FixApplication) => {
    setApplication(row);
    // A verified row carries its own evidence, so a page refresh shows the
    // delta again without re-running the agent.
    if (row.verification) setVerification(row.verification);
  }, []);

  /* On mount: has this finding already been through the flow? One request for
     the whole scan's history, filtered down to this finding — and the backend
     re-reads any still-open pull request from GitHub while answering, so
     `state` here is what GitHub says right now. */
  useEffect(() => {
    let cancelled = false;
    fetchFixApplications(scanId)
      .then((rows) => {
        if (cancelled) return;
        const mine = rows
          .filter((row) => row.finding_key === findingKey && !TERMINAL.includes(row.state))
          .pop();
        if (mine) adopt(mine);
        setPhase(mine ? "ready" : "idle");
      })
      .catch(() => {
        // History being unreadable is not a reason to hide the flow — the
        // apply call itself re-checks idempotency server-side and will hand
        // back the existing pull request rather than opening a second one.
        if (!cancelled) setPhase("idle");
      });
    return () => {
      cancelled = true;
    };
  }, [scanId, findingKey, adopt]);

  function fail(err: unknown, fallback: string) {
    if (err instanceof ApiError && err.status === 403) {
      setNotice({ kind: "access", text: err.message });
    } else {
      setNotice({
        kind: err instanceof ApiError && err.status === 409 ? "info" : "error",
        text: err instanceof Error ? err.message : fallback,
      });
    }
  }

  async function runDryRun() {
    setNotice(null);
    setPhase("working");
    try {
      // The apply path refuses a plan that was never saved (rule 6: what gets
      // pushed must be something a person had the chance to look at). The diff
      // is already on screen above, and a dry-run preview follows this line
      // before anything can be written, so saving here adds no risk — it just
      // spares a second button.
      await saveFixPlan(scanId, findingKey);
      const result = await applyFix(scanId, [findingKey], true);
      if (isApplyPreview(result)) {
        setPreview(result);
        setPhase("ready");
        return;
      }
      // Not a preview: this finding already had an open pull request, and the
      // backend handed that one back instead of opening a second.
      if (result.applications[0]) adopt(result.applications[0]);
      setNotice({ kind: "info", text: "This finding already has an open pull request." });
      setPhase("ready");
    } catch (err) {
      fail(err, "Couldn't check what would be pushed.");
      setPhase("idle");
    }
  }

  async function openPullRequest() {
    setNotice(null);
    setPhase("working");
    try {
      const result = await applyFix(scanId, [findingKey], false);
      if (isApplyPreview(result)) {
        // Cannot happen — a live apply returns a result — but typed unions
        // don't care what "cannot happen", and neither should this branch.
        setNotice({ kind: "error", text: "The server returned a preview for a live apply." });
        setPhase("ready");
        return;
      }
      setPreview(null);
      if (result.applications[0]) adopt(result.applications[0]);
      if (result.already_applied) {
        setNotice({ kind: "info", text: "This finding already had a pull request open." });
      }
      setPhase("ready");
    } catch (err) {
      fail(err, "Couldn't open the pull request.");
      setPhase("ready");
    }
  }

  async function verify() {
    setNotice(null);
    setPhase("working");
    try {
      const result = await verifyFinding(scanId, findingKey);
      setVerification(result);
      if (application) {
        setApplication({ ...application, state: "verified", verification: result });
      }
      setPhase("ready");
    } catch (err) {
      fail(err, "Couldn't verify the fix.");
      setPhase("ready");
    }
  }

  if (phase === "loading") {
    return (
      <p className="mt-4 animate-pulse font-mono text-[10px] uppercase tracking-[0.25em] text-muted">
        Checking fix history…
      </p>
    );
  }

  return (
    <div className="mt-5 border-t border-rule pt-5">
      <p className="font-mono text-[9px] uppercase tracking-[0.3em] text-muted">
        Pull request
      </p>

      {notice && <NoticeLine notice={notice} />}

      {/* --- nothing opened yet ------------------------------------------- */}
      {!application && !preview && (
        <div className="mt-3 flex flex-wrap items-center gap-4">
          <button
            type="button"
            onClick={runDryRun}
            disabled={phase === "working"}
            className="border border-parchment/25 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10 disabled:opacity-40"
          >
            {phase === "working" ? "Checking…" : "Preview pull request →"}
          </button>
          {/* A fix doesn't have to be ours. Verifying re-reads the repository
              either way, so someone who patched this by hand can still get the
              real before/after — there's just no pull request of ours to close
              out, and the result says so. */}
          <button
            type="button"
            onClick={verify}
            disabled={phase === "working"}
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment disabled:opacity-40"
          >
            {phase === "working" ? "Re-reading the repo…" : "Already fixed it yourself? Verify →"}
          </button>
        </div>
      )}

      {/* A verification with no pull request behind it renders here, since the
          block below only exists once there's an application. */}
      {!application && !preview && verification && <VerificationView result={verification} />}

      {/* --- dry run came back: exactly what would be pushed --------------- */}
      {preview && !application && (
        <div className="mt-4 space-y-3">
          <dl className="space-y-1.5 font-mono text-[10px] uppercase tracking-[0.15em] text-muted">
            <Row label="Repository" value={preview.repo} />
            <Row label="Branch" value={`${preview.branch} → ${preview.base_branch}`} />
            <Row label="Files" value={preview.patches.map((p) => p.path).join(" · ")} />
          </dl>

          <p className="text-sm leading-relaxed">{preview.pr_title}</p>

          <details className="group">
            <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment">
              Read the pull request body
            </summary>
            <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded border border-rule px-3 py-3 font-mono text-[11px] leading-relaxed text-muted">
              {preview.pr_body}
            </pre>
          </details>

          <div className="flex flex-wrap items-center gap-4 pt-1">
            <button
              type="button"
              onClick={openPullRequest}
              disabled={phase === "working"}
              className="border border-parchment/25 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10 disabled:opacity-40"
            >
              {phase === "working" ? "Opening…" : "Open pull request"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPreview(null);
                setNotice(null);
                setPhase("idle");
              }}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
            >
              Cancel
            </button>
          </div>

          <p className="font-mono text-[8px] text-rule">
            Nothing has been written yet. This is a dry run — every check has already
            passed, and the live call pushes exactly these files.
          </p>
        </div>
      )}

      {/* --- a pull request exists ---------------------------------------- */}
      {application && (
        <div className="mt-4 space-y-3">
          <dl className="space-y-1.5 font-mono text-[10px] uppercase tracking-[0.15em] text-muted">
            <Row label="State" value={STATE_LABEL[application.state]} />
            {application.branch && <Row label="Branch" value={application.branch} />}
          </dl>

          {application.pr_url && (
            <a
              href={application.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block font-mono text-[10px] uppercase tracking-[0.2em] text-parchment underline decoration-rule transition-colors hover:decoration-parchment"
            >
              {application.pr_number ? `Pull request #${application.pr_number}` : "Pull request"} ↗
            </a>
          )}

          {application.state === "pr_open" && (
            <p className="text-sm leading-relaxed text-muted">
              Review it and merge it on GitHub. Sentinels never merges its own pull
              request — once it&apos;s in, come back and verify.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-4 pt-1">
            <button
              type="button"
              onClick={verify}
              disabled={phase === "working"}
              className="border border-parchment/25 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10 disabled:opacity-40"
            >
              {phase === "working"
                ? "Re-reading the repo…"
                : verification
                  ? "Verify again"
                  : "Verify fix →"}
            </button>
          </div>

          {verification && <VerificationView result={verification} />}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-wrap gap-x-3">
      <dt className="text-rule">{label}</dt>
      <dd className="break-all normal-case tracking-normal text-muted">{value}</dd>
    </div>
  );
}

function NoticeLine({ notice }: { notice: Notice }) {
  if (notice.kind === "access") {
    return (
      <div className="glass mt-3 px-4 py-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-critical">
          {notice.text}
        </p>
        <Link
          href="/settings"
          className="mt-2 inline-block font-mono text-[10px] uppercase tracking-[0.2em] text-parchment underline decoration-rule transition-colors hover:decoration-parchment"
        >
          Connect the repository →
        </Link>
      </div>
    );
  }
  return (
    <p
      className={`mt-3 font-mono text-[10px] uppercase tracking-[0.2em] ${
        notice.kind === "error" ? "text-critical" : "text-muted"
      }`}
    >
      {notice.text}
    </p>
  );
}

/* What the re-run actually saw. Every number here came from re-running one
   agent against the repository and calling the same deterministic scorer
   twice — no model computed any part of it, and a fix that didn't work says so
   in exactly the same place a working one does. */
function VerificationView({ result }: { result: VerificationResult }) {
  const improved = result.delta > 0;
  return (
    <div className="glass mt-4 space-y-3 px-4 py-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-mono text-[9px] uppercase tracking-[0.3em] text-muted">
          Verified
        </span>
        <span
          className={`font-mono text-[10px] uppercase tracking-[0.2em] ${
            result.target_fixed ? "text-parchment" : "text-critical"
          }`}
        >
          {result.target_fixed ? "Fail → Pass" : "Still failing"}
        </span>
      </div>

      <div className="flex flex-wrap items-baseline gap-x-4">
        <span className="font-display text-3xl leading-none text-muted">{result.before}</span>
        <span className="font-mono text-xs text-rule">→</span>
        <span className="font-display text-4xl leading-none">{result.after}</span>
        <span
          className={`font-mono text-[10px] uppercase tracking-[0.2em] ${
            improved ? "text-parchment" : "text-muted"
          }`}
        >
          {improved ? `+${result.delta}` : result.delta === 0 ? "no change" : `${result.delta}`}
        </span>
      </div>

      {result.fixed.length > 0 && (
        <p className="font-mono text-[10px] leading-relaxed text-muted">
          <span className="uppercase tracking-[0.2em] text-rule">No longer reported — </span>
          {result.fixed.join(" · ")}
        </p>
      )}
      {result.still_failing.length > 0 && (
        <p className="font-mono text-[10px] leading-relaxed text-muted">
          <span className="uppercase tracking-[0.2em] text-rule">Still reported — </span>
          {result.still_failing.join(" · ")}
        </p>
      )}

      <p className="font-mono text-[8px] leading-relaxed text-rule">
        The {result.agent} agent was re-run against {result.ref} and its findings replaced
        for this comparison. Both scores use {result.scorer_version ?? "legacy-v1"}.
        The original scan remains {result.stored_score ?? "unchanged"} with scorer {result.stored_scorer_version ?? "legacy-v1"}.
        {!result.recorded && " No fix application was recorded for this finding, so nothing was closed out."}
      </p>
    </div>
  );
}
