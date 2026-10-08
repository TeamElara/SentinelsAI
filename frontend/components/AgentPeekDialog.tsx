"use client";

/* The popup a carousel card opens into — modelled directly on
   components/landing/scan-select/ScanDialog.tsx, which already solves the
   scrim, the blur, Escape, focus-return and the scroll lock correctly.
   Re-deriving those here would just be a second copy that drifts from the
   first one over time.

   It is deliberately a SUMMARY, not the full agent report: verdict,
   duration, issue count, and the top three problems, then a link out to
   /scan/[scanId]/agents/[agentName] — the page that already renders every
   finding, its evidence, and "what this agent checks". Duplicating that
   page inside a popup would mean two places to keep in sync; this way
   there's still exactly one. */

import { useEffect, useRef } from "react";
import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { AgentInfo, AgentResult } from "@/lib/api";
import { isProblem } from "@/lib/findings";
import { EXPO_OUT } from "@/components/landing/scan-select/motion";

function verdict(result: AgentResult): string {
  if (result.error) return "Failed";
  if (result.findings.some(isProblem)) return "Issues found";
  return result.coverage_status === "completed" ? "No issues observed" : "Incomplete";
}

export function AgentPeekDialog({
  result,
  info,
  scanId,
  onClose,
}: {
  result: AgentResult | null;
  info: AgentInfo | undefined;
  scanId: string;
  onClose: () => void;
}) {
  const reduceMotion = useReducedMotion();
  const panelRef = useRef<HTMLDivElement>(null);
  const returnFocusTo = useRef<HTMLElement | null>(null);
  const open = result !== null;

  useEffect(() => {
    if (!open) return;

    returnFocusTo.current = document.activeElement as HTMLElement | null;
    panelRef.current?.querySelector<HTMLElement>("a, button")?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      returnFocusTo.current?.focus();
    };
  }, [open, onClose]);

  const problems = result?.findings.filter(isProblem) ?? [];

  return (
    <AnimatePresence>
      {open && result && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center px-6"
          initial="enter"
          animate="settled"
          exit="leave"
        >
          <motion.button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="absolute inset-0 h-full w-full cursor-default bg-black/80 backdrop-blur-sm"
            variants={{
              enter: { opacity: 0 },
              settled: { opacity: 1, transition: { duration: 0.3 } },
              leave: { opacity: 0, transition: { duration: 0.2 } },
            }}
          />

          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={info?.display_name ?? result.agent}
            className="relative w-full max-w-lg border border-white/10 bg-ink p-8 sm:p-10"
            variants={{
              enter: { opacity: 0, y: reduceMotion ? 0 : 24, scale: reduceMotion ? 1 : 0.98 },
              settled: {
                opacity: 1,
                y: 0,
                scale: 1,
                transition: { duration: reduceMotion ? 0.15 : 0.45, ease: EXPO_OUT },
              },
              leave: {
                opacity: 0,
                y: reduceMotion ? 0 : -12,
                scale: reduceMotion ? 1 : 0.99,
                transition: { duration: 0.2, ease: "easeIn" },
              },
            }}
          >
            <button
              type="button"
              onClick={onClose}
              className="absolute right-6 top-6 font-mono text-[10px] uppercase
                         tracking-[0.28em] text-muted transition-colors hover:text-parchment
                         focus-visible:text-parchment focus-visible:outline-none"
            >
              Close
            </button>

            <p className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
              Agent
            </p>
            <h2 className="mt-2 font-display text-3xl leading-tight">
              {info?.display_name ?? result.agent}
            </h2>
            {info?.purpose && (
              <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted">
                {info.purpose}
              </p>
            )}

            <dl className="mt-7 grid grid-cols-3 gap-x-4 border-t border-rule pt-6">
              <div>
                <dt className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
                  Verdict
                </dt>
                <dd
                  className={`mt-1.5 font-mono text-sm uppercase tracking-[0.1em] ${
                    result.error ? "text-critical" : ""
                  }`}
                >
                  {verdict(result)}
                </dd>
              </div>
              <div>
                <dt className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
                  Duration
                </dt>
                <dd className="mt-1.5 font-mono text-sm">{result.duration_ms}ms</dd>
              </div>
              <div>
                <dt className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
                  Issues
                </dt>
                <dd className="mt-1.5 font-mono text-sm">{problems.length}</dd>
              </div>
            </dl>

            {result.error && (
              <p className="mt-6 border-l-2 border-critical pl-3 text-sm text-critical">
                {result.error}
              </p>
            )}

            {!result.error && problems.length > 0 && (
              <ul className="mt-6 space-y-2 border-t border-rule pt-6">
                {problems.slice(0, 3).map((finding) => (
                  <li key={finding.id} className="text-sm leading-relaxed text-parchment/85">
                    · {finding.title}
                  </li>
                ))}
                {problems.length > 3 && (
                  <li className="font-mono text-xs text-muted">
                    +{problems.length - 3} more
                  </li>
                )}
              </ul>
            )}

            {!result.error && problems.length === 0 && (
              <p className="mt-6 border-t border-rule pt-6 text-sm text-muted">
                No issues observed. Coverage: {result.coverage_status ?? "unavailable"}. Review the full report for limitations.
              </p>
            )}

            <Link
              href={`/scan/${scanId}/agents/${result.agent}`}
              className="glass mt-7 inline-block px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] transition-colors hover:bg-white/8"
            >
              View full report →
            </Link>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export default AgentPeekDialog;
