"use client";

/* The scan form — take a URL, stream a scan, navigate to the report.

   Extracted from `app/url/page.tsx` when the landing page grew its own
   "paste a URL" section at the end of the hero. Both places need the SAME
   behaviour (real streaming scan, real per-agent progress, real error
   handling), and the one thing worse than duplicating this logic would be
   letting the two copies drift — a landing page whose input quietly stops
   matching what `/url` does is a bug nobody notices until a user hits it.

   A finished scan lives at its own permanent URL (/scan/<uuid>) rather than
   in this component's state. When `onDone` fires, we navigate there instead
   of rendering a report here. The scan is already saved to the database by
   the time `done` arrives, so the new page can fetch it immediately from
   `GET /scans/{id}` — hard-refresh works. */

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { streamScan, streamRepoScan, type AgentResult, type TargetType } from "@/lib/api";
import { UsageBadge } from "@/components/UsageBadge";
import { ScanProgress } from "@/components/ScanProgress";

type ScanLauncherProps = {
  /** Rendered above the form, and hidden while a scan is running so the
      progress list gets the whole screen. The two callers want different
      copy here, which is the only thing that actually differs between them. */
  children?: React.ReactNode;
  /** Shown under the form when idle. Same reasoning as `children`. */
  footnote?: React.ReactNode;
  autoFocus?: boolean;
  /** Field label. Omit and it stays screen-reader-only, as `/url` has it. */
  label?: React.ReactNode;
  placeholder?: string;
  /** Idle text on the submit button. The dialog calls it "Next", because
      there it is a step in a flow rather than the whole page's one action. */
  submitLabel?: string;
  /** "url" (default) streams via `/scan/stream`; "repo" streams via
      `/repo/stream` and shows the five repo agents in ScanProgress instead. */
  targetType?: TargetType;
};

export function ScanLauncher({
  children,
  footnote,
  autoFocus,
  label,
  placeholder = "example.com",
  submitLabel = "Inspect",
  targetType = "url",
}: ScanLauncherProps) {
  const router = useRouter();
  const stop = useRef<(() => void) | null>(null);
  useEffect(() => () => stop.current?.(), []);
  const [url, setUrl] = useState("");
  const [isScanning, setIsScanning] = useState(false);
  const [isWaking, setIsWaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Filled in one at a time as each "agent" SSE event arrives — this is what
  // makes the waiting state below real instead of a generic pulse.
  const [agentResults, setAgentResults] = useState<Record<string, AgentResult>>({});

  function handleSubmit(event: React.FormEvent) {
    // A <form> submit reloads the page by default — a full-page navigation that
    // would throw away every piece of state above. This cancels that.
    event.preventDefault();

    if (!url.trim() || isScanning) return;

    setError(null);
    setAgentResults({});
    setIsScanning(true);
    setIsWaking(false);

    const stream = targetType === "repo" ? streamRepoScan : streamScan;
    stop.current = stream(url, {
      onWaking: () => setIsWaking(true),
      onReady: () => setIsWaking(false),
      onAgent: (result) => {
        setAgentResults((prev) => ({ ...prev, [result.agent]: result }));
      },
      onDone: (finishedReport) => {
        // The scan is persisted — navigate to its permanent page.
        router.push(`/scan/${finishedReport.id}`);
      },
      onError: (message) => {
        setIsWaking(false);
        setError(message);
        setIsScanning(false);
      },
    });
  }

  return (
    <>
      {!isScanning && children}

      <form
        onSubmit={handleSubmit}
        className="mt-14 flex flex-col gap-4 sm:flex-row sm:items-end"
      >
        {/* The label and the input share a column so a VISIBLE label can sit
            above the field instead of beside it. With no `label` prop the
            label is `sr-only`, the column collapses to just the input, and
            the row looks exactly as it always did — the `flex-1` simply
            moved from the input up onto this wrapper. */}
        <div className="flex flex-1 flex-col gap-3">
          <label
            htmlFor="url"
            className={
              label
                ? "font-mono text-[11px] uppercase tracking-[0.32em] text-muted"
                : "sr-only"
            }
          >
            {label ?? "Address to inspect"}
          </label>
          <input
            id="url"
            name="url"
            type="text"
            placeholder={placeholder}
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            disabled={isScanning}
            autoFocus={autoFocus}
            className="w-full border-b border-rule bg-transparent pb-3 font-mono text-lg
                       outline-none transition-colors placeholder:text-muted/50
                       focus:border-parchment disabled:text-muted"
          />
        </div>
        <button
          type="submit"
          disabled={isScanning || !url.trim()}
          className="glass shrink-0 px-6 py-3 font-mono text-xs uppercase tracking-[0.2em]
                     transition-colors hover:bg-white/8 disabled:cursor-not-allowed
                     disabled:text-muted disabled:hover:bg-white/4"
        >
          {isScanning ? "Inspecting" : submitLabel}
        </button>
      </form>

      <UsageBadge kind={targetType === "repo" ? "repo_scan" : "url_scan"} />

      {isWaking && <p role="status" className="mt-8 animate-pulse text-sm text-muted">Waking up the scanner…</p>}
      {isScanning && !isWaking && <ScanProgress agentResults={agentResults} targetType={targetType} />}

      {error && (
        <div className="mt-10 border-l-2 border-critical pl-4">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-critical">
            Inspection failed
          </p>
          <p className="mt-2 text-sm text-muted">{error}</p>
        </div>
      )}

      {!isScanning && footnote}
    </>
  );
}
