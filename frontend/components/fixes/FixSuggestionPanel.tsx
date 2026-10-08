"use client";

/* AI-generated remediation advice for one finding.

   Six sections: why it exists, security impact, exploitation (conceptual),
   recommended fix, best practices, and framework examples. All rendered only
   when present — the LLM sometimes omits framework_examples for findings that
   aren't server-config related. */

import { useState } from "react";
import { fetchFix, type FixSuggestion } from "@/lib/api";

interface Props {
  scanId: string;
  findingKey: string;  // the finding slug, e.g. "missing-csp"
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "done"; fix: FixSuggestion }
  | { kind: "error"; message: string };

export function FixSuggestionPanel({ scanId, findingKey }: Props) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [regenerationError, setRegenerationError] = useState<string | null>(null);

  async function load(regenerate = false) {
    const previous = state.kind === "done" ? state.fix : null;
    setRegenerationError(null);
    setState({ kind: "loading" });
    try {
      const fix = await fetchFix(scanId, findingKey, regenerate);
      setState({ kind: "done", fix });
    } catch (err) {
      if (previous) {
        setState({ kind: "done", fix: previous });
        setRegenerationError(err instanceof Error ? err.message : "Regeneration is unavailable. Showing the saved suggestion.");
        return;
      }
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to load fix suggestion.",
      });
    }
  }

  if (state.kind === "idle") {
    // Deliberately larger and higher-contrast than the app's usual chrome
    // buttons (Download PDF, New scan, etc. — all `text-[10px]`) — this is
    // the one action on the whole findings list that actually does
    // something with the issue above it, not just navigate, so it needs to
    // read as the obvious next step rather than blend into the metadata.
    return (
      <button
        type="button"
        onClick={() => load()}
        className="glass mt-4 border-parchment/25 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10"
      >
        Fix with AI →
      </button>
    );
  }

  if (state.kind === "loading") {
    return (
      <div className="glass mt-4 px-4 py-3">
        <p className="animate-pulse font-mono text-[10px] uppercase tracking-[0.25em] text-muted">
          Generating fix…
        </p>
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

  const { fix } = state;

  return (
    <div className="glass mt-4 space-y-5 px-5 py-5">
      {regenerationError && <p role="status" className="text-xs text-critical">{regenerationError} Showing the saved suggestion.</p>}
      <div className="flex items-center justify-between">
        <p className="font-mono text-[9px] uppercase tracking-[0.3em] text-muted">
          AI fix suggestion
        </p>
        <button
          type="button"
          onClick={() => load(true)}
          className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
        >
          Regenerate
        </button>
      </div>

      <FixSection label="Why it exists" text={fix.why_it_exists} />
      <FixSection label="Security impact" text={fix.security_impact} />
      <FixSection label="How it could be abused" text={fix.exploitation} />
      <FixSection label="Recommended fix" text={fix.recommended_fix} />

      {fix.best_practices.length > 0 && (
        <div>
          <p className="font-mono text-[9px] uppercase tracking-[0.25em] text-muted">
            Best practices
          </p>
          <ul className="mt-2 space-y-1">
            {fix.best_practices.map((item, i) => (
              <li key={i} className="flex gap-2 text-sm leading-relaxed">
                <span className="mt-0.5 shrink-0 text-muted">—</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {Object.keys(fix.framework_examples).length > 0 && (
        <div>
          <p className="font-mono text-[9px] uppercase tracking-[0.25em] text-muted">
            Config examples
          </p>
          <div className="mt-2 space-y-3">
            {Object.entries(fix.framework_examples).map(([fw, code]) => (
              <div key={fw}>
                <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-muted">
                  {fw}
                </p>
                <pre className="mt-1 overflow-x-auto rounded border border-rule bg-transparent px-3 py-2 font-mono text-xs leading-relaxed">
                  {code}
                </pre>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="font-mono text-[8px] text-rule">
        Generated by {fix.model} · {fix.generated_at.slice(0, 10)}
      </p>
    </div>
  );
}

function FixSection({ label, text }: { label: string; text: string }) {
  if (!text) return null;
  return (
    <div>
      <p className="font-mono text-[9px] uppercase tracking-[0.25em] text-muted">{label}</p>
      <p className="mt-1.5 text-sm leading-relaxed">{text}</p>
    </div>
  );
}
