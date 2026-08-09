"use client";

import { useEffect, useState } from "react";
import { fetchAgents, fetchRepoAgents } from "@/lib/agents";
import type { AgentResult, TargetType } from "@/lib/api";

// Used only when the agent-list fetch fails — keeps the waiting state
// working even if the backend is momentarily unreachable at scan start.
const FALLBACK_NAMES: Record<TargetType, string[]> = {
  url: ["headers", "recon", "tls", "exposure", "dns", "api-security", "misconfig", "subdomain"],
  repo: ["repo-hygiene", "repo-secrets", "repo-dependencies", "repo-config", "repo-patterns"],
};

/* Screen 2 — "the set piece" (docs/DESIGN.md). Five glass panels, one per
   agent, that sit dim and waiting until that agent's *real* SSE result
   lands — at which point the panel remounts as its "done" self and plays
   the `materialize-in` entrance (globals.css). The stagger between panels
   is never scripted: it's whatever order and timing the real scan actually
   finished in, the same genuine per-agent timing A16 first surfaced as
   plain text. This is the step docs/DESIGN.md calls "where the animation
   budget goes" — every other screen in this app stays deliberately still. */
export function ScanProgress({
  agentResults,
  targetType = "url",
}: {
  agentResults: Record<string, AgentResult>;
  /** Which agent list to show — the five URL agents or the five repo agents.
      Defaults to "url" so every existing caller keeps working unchanged. */
  targetType?: TargetType;
}) {
  const [agentNames, setAgentNames] = useState<string[]>(FALLBACK_NAMES[targetType]);

  // Fetch the real agent list so adding a 6th agent to either registry is
  // enough — no frontend change needed. Falls back to FALLBACK_NAMES silently.
  useEffect(() => {
    const fetchList = targetType === "repo" ? fetchRepoAgents : fetchAgents;
    setAgentNames(FALLBACK_NAMES[targetType]);
    fetchList().then((agents) => {
      if (agents.length > 0) setAgentNames(agents.map((a) => a.name));
    });
  }, [targetType]);

  return (
    <div className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-4">
      {agentNames.map((name) => {
        const result = agentResults[name];

        return (
          // The key flips from "-waiting" to "-done" the instant a result
          // arrives. React treats that as a brand new element rather than
          // an update to the old one — it unmounts the waiting panel and
          // mounts a fresh "done" panel in its place, which is what makes
          // `materialize-in` (an animation that only plays on mount) fire
          // exactly once, exactly when the real data shows up.
          <div
            key={result ? `${name}-done` : `${name}-waiting`}
            className={`glass materialize-in px-4 py-4 ${
              result ? "" : "opacity-40"
            }`}
          >
            <p
              className={`font-mono text-[10px] uppercase tracking-[0.2em] ${
                result ? "" : "animate-pulse"
              }`}
            >
              {name}
            </p>

            {result ? (
              result.error ? (
                <p className="mt-2 font-mono text-[10px] leading-snug break-words">
                  {result.error}
                </p>
              ) : (
                <p className="mt-2 font-mono text-[10px] text-muted">
                  {result.findings.length} checks · {result.duration_ms}ms
                </p>
              )
            ) : (
              <p className="mt-2 font-mono text-[10px] text-muted">
                Waiting…
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
