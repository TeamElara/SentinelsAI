"use client";

import type { Finding } from "@/lib/api";
import { FixSuggestionPanel } from "@/components/fixes/FixSuggestionPanel";

/* One problem, as a line in an inspection record.

   Critical is the only severity that gets the accent colour, and it gets it in
   two places at once — the label and a left rule — so it's findable by scanning
   the left edge of the page without reading a word. Everything else is
   distinguished by weight and position, not hue (docs/DESIGN.md).

   M14 adds a "Get AI fix →" button for FAIL/WARN findings; pass-through
   findings don't get one (nothing to fix). */
export function FindingRow({
  finding,
  scanId,
}: {
  finding: Finding;
  scanId?: string;
}) {
  const isCritical = finding.severity === "Critical";
  // Only show the fix button when we have a scanId (legacy Report.tsx passes none).
  const showFix = !!scanId && (finding.status === "fail" || finding.status === "warn");
  // A finding only carries confidence when its evidence genuinely leaves room
  // for doubt (backend/models.py). Below 0.9 it gets said out loud, so a
  // "potential" finding can never be read as a confirmed one.
  const needsVerification = finding.confidence !== null && finding.confidence < 0.9;

  return (
    <li
      className={`border-l-2 pl-5 ${
        isCritical ? "border-critical" : "border-rule"
      }`}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className={`font-mono text-[10px] uppercase tracking-[0.2em] ${
            isCritical ? "text-critical" : "text-muted"
          }`}
        >
          {finding.severity}
        </span>
        {/* status is the other half of the picture: a WARN and a FAIL can share
            a severity, and the score deducts for both (see A6). */}
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
          {finding.status}
        </span>
        {needsVerification && (
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
            needs verification · {Math.round(finding.confidence! * 100)}%
          </span>
        )}
      </div>

      <h4 className="mt-2 text-lg leading-snug sm:text-xl">{finding.title}</h4>

      {/* Which host this is actually about. Only rendered when the backend set
          it, so every finding from the original five agents — all of which are
          about the scanned site itself — looks exactly as it did before. */}
      {finding.affected_url && (
        <p className="mt-1.5 break-all font-mono text-xs text-muted">
          {finding.affected_url}
        </p>
      )}

      {/* Every field below is optional in backend/models.py — it defaults to ""
          — so each is rendered only when there's something to render. */}
      {finding.description && (
        <p className="mt-1.5 text-sm leading-relaxed text-muted sm:text-base">
          {finding.description}
        </p>
      )}

      {/* The scan's own raw data, styled as an attached artifact rather than
          another line of prose (docs/DESIGN.md: "cert details, header dumps,
          DNS records styled as inspection evidence"). The bordered `glass`
          box and the "Evidence" label are what make it read as something
          lifted from the response, not written for the report. */}
      {finding.evidence && (
        <div className="glass mt-3 px-5 py-4">
          <p className="font-mono text-[9px] uppercase tracking-[0.25em] text-muted">
            Evidence
          </p>
          <p className="mt-2 font-mono text-xs leading-relaxed break-words sm:text-sm">
            {finding.evidence}
          </p>
        </div>
      )}

      {finding.remediation && (
        <p className="mt-3 text-sm leading-relaxed sm:text-base">
          <span className="text-muted">Fix — </span>
          {finding.remediation}
        </p>
      )}

      {finding.owasp && (
        <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.15em] text-muted">
          {finding.owasp}
        </p>
      )}

      {showFix && scanId && <FixSuggestionPanel scanId={scanId} findingKey={finding.id} />}
    </li>
  );
}
