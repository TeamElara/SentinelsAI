"use client";

/* The scan dashboard — the main view once a scan has a permanent URL.

   M1-M2 persisted the scan; M6 gave it a shareable URL; M7 is this page.
   The hard-refresh test is the milestone's real verification: navigating to
   /scan/<uuid> in a fresh tab should show the full report because the data
   comes from the database, not from React state. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  downloadReportPdf,
  fetchFixSummary,
  fetchScan,
  type Finding,
  type FixSummary,
  type ScanReport,
} from "@/lib/api";
import { fetchAgents, fetchRepoAgents } from "@/lib/agents";
import type { AgentInfo } from "@/lib/api";
import { groupByCategory, HEADLINE_SEVERITIES } from "@/lib/findings";
import { useScrollDrift } from "@/lib/useScrollDrift";
import { ScoreRing } from "@/components/ScoreRing";
import { AgentCarousel } from "@/components/AgentCarousel";
import { Footer } from "@/components/Footer";

/* The single worst problem in the report, as a plain-English headline.
   `groupByCategory` already sorts worst category first and worst finding
   first within it (see lib/findings.ts), so the worst problem overall is
   just its first category's first problem — no separate ranking needed. */
function MainIssue({ finding, scanId }: { finding: Finding; scanId: string }) {
  return (
    <Link
      href={`/scan/${scanId}/agents/${finding.agent}`}
      className="glass flex-1 min-w-[22rem] px-7 py-5 transition-colors hover:bg-white/8"
    >
      <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
        Main issue
      </p>
      <p className="mt-2 text-xl leading-snug">{finding.title}</p>
      {finding.description && (
        <p className="mt-2 text-sm leading-relaxed text-muted line-clamp-2">
          {finding.description}
        </p>
      )}
    </Link>
  );
}

/* Readiness score + deployment status, both computed by the checklist
   evaluator (backend/checklist/evaluator.py) and both a shortcut into the
   full breakdown on the Checklist tab — this is a summary, not a
   replacement for it. */
function DeploymentBadge({
  status,
  readinessScore,
  scanId,
}: {
  status: string | null;
  readinessScore: number | null;
  scanId: string;
}) {
  if (status === null || readinessScore === null) return null;

  const color =
    status === "blocked"
      ? "text-critical"
      : status === "caution"
        ? "text-[#facc15]"
        : "text-[#4ade80]";

  const label =
    status === "blocked"
      ? "Blocked"
      : status === "caution"
        ? "Caution"
        : "Ready";

  return (
    <Link
      href={`/scan/${scanId}/checklist`}
      className="glass flex items-center gap-7 px-7 py-5 transition-colors hover:bg-white/8"
    >
      <div>
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
          Deployment
        </p>
        <p className={`mt-1.5 font-mono text-lg uppercase tracking-[0.15em] ${color}`}>
          {label}
        </p>
      </div>
      <div className="h-14 w-px bg-rule" />
      <div>
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
          Readiness
        </p>
        <p className="mt-1 font-display text-4xl leading-none">{readinessScore}</p>
      </div>
    </Link>
  );
}

/* PLAN-v5 Stages A-C's own badge — "N fixes available" links straight to the
   first one, the same one-click-away pattern DeploymentBadge uses for the
   checklist. Rendered only when there's at least one (see the caller): a
   badge reading "0 fixes available" would say nothing a clean scan doesn't
   already say better by having no badge there at all. */
function FixCountBadge({
  summary,
  scanId,
}: {
  summary: FixSummary;
  scanId: string;
}) {
  const href =
    summary.first_agent && summary.first_finding_key
      ? `/scan/${scanId}/agents/${summary.first_agent}`
      : `/scan/${scanId}/files`;

  return (
    <Link
      href={href}
      className="glass flex items-center gap-4 px-7 py-5 transition-colors hover:bg-white/8"
    >
      <div>
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
          Autofix
        </p>
        <p className="mt-1.5 font-display text-4xl leading-none">
          {summary.fixable_count}
        </p>
      </div>
      <p className="max-w-[9rem] text-sm leading-snug text-muted">
        {summary.fixable_count === 1
          ? "fix available →"
          : "fixes available →"}
      </p>
    </Link>
  );
}

export default function ScanPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const [report, setReport] = useState<ScanReport | null>(null);
  const [agentInfo, setAgentInfo] = useState<AgentInfo[]>([]);
  const [fixSummary, setFixSummary] = useState<FixSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const headerDriftRef = useScrollDrift<HTMLElement>(0.03, 32);

  useEffect(() => {
    fetchScan(scanId)
      .then(async (scan) => {
        setReport(scan);
        // The reel labels each panel with its agent's display name, category
        // and purpose — none of which are in the scan row, only in the
        // registry. Which registry depends on what was scanned.
        setAgentInfo(
          await (scan.target_type === "repo" ? fetchRepoAgents() : fetchAgents()),
        );
        // Only a repo scan can have a deterministic Fixer at all (PLAN-v5) —
        // skip the request for a URL scan rather than asking a question with
        // a guaranteed answer of zero.
        if (scan.target_type === "repo") {
          setFixSummary(await fetchFixSummary(scanId));
        }
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [scanId]);

  async function handleDownload() {
    if (!report) return;
    setIsExporting(true);
    setExportError(null);
    try {
      await downloadReportPdf(report);
    } catch {
      setExportError("Couldn't generate the PDF. Try again.");
    } finally {
      setIsExporting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-24">
        <p className="animate-pulse font-mono text-xs uppercase tracking-[0.35em] text-muted">
          Loading…
        </p>
      </div>
    );
  }

  if (notFound || !report) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-24">
        <div className="mx-auto max-w-md text-center">
          <p className="font-mono text-xs uppercase tracking-[0.35em] text-muted">
            Not found
          </p>
          <p className="mt-4 font-display text-2xl">
            This scan doesn&apos;t exist.
          </p>
          <Link
            href="/"
            className="glass mt-8 inline-block px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] transition-colors hover:bg-white/8"
          >
            New scan
          </Link>
        </div>
      </div>
    );
  }

  // Only computed for the "main issue" callout — the full findings list
  // itself lives on each agent's own detail page now, not here.
  const mainIssue: Finding | null =
    groupByCategory(report.findings)[0]?.problems[0] ?? null;

  return (
    <>
    <article className="mx-auto w-full max-w-7xl px-6 pt-20 sm:px-8">
      <header
        ref={headerDriftRef}
        // `flex-wrap` alone doesn't help here: it only wraps the ring onto
        // its own line once the row can't fit both items at their natural
        // width, not whenever the text column would prefer more room. Below
        // `sm` the two are stacked outright instead, so the text column
        // always gets the article's full width rather than whatever's left
        // beside a 160px ring.
        className="flex flex-col items-start gap-y-8 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-16 sm:gap-y-10"
      >
        <ScoreRing score={report.score} grade={report.grade} />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-4">
            <p className="font-mono text-sm uppercase tracking-[0.3em] text-muted">
              Inspection record
            </p>
            <div className="flex shrink-0 gap-3">
              <Link
                href="/"
                className="glass px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] transition-colors hover:bg-white/8"
              >
                New scan
              </Link>
              <button
                type="button"
                onClick={handleDownload}
                disabled={isExporting}
                className="glass px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] transition-colors hover:bg-white/8 disabled:cursor-not-allowed disabled:text-muted disabled:hover:bg-white/4"
              >
                {isExporting ? "Preparing…" : "Download PDF"}
              </button>
            </div>
          </div>

          <p className="mt-4 break-all font-mono text-base sm:text-xl lg:text-3xl">
            {report.url}
          </p>
          <p className="mt-3 font-mono text-sm text-muted">
            {report.scanned_at} · {report.duration_ms}ms
          </p>

          {exportError && (
            <p className="mt-2 font-mono text-sm text-critical">{exportError}</p>
          )}

          {/* A ruled strip rather than loose columns: at this width the four
              counts would otherwise drift apart and stop reading as one set.
              The leading rule on each gives them a shared left edge to sit
              against, which is also what fills the row out. */}
          <dl className="mt-9 grid grid-cols-2 gap-x-6 gap-y-7 sm:grid-cols-4 sm:gap-x-8">
            {HEADLINE_SEVERITIES.map((severity) => {
              const count = report.counts[severity] ?? 0;
              return (
                <div key={severity} className="min-w-0 border-l border-rule pl-4 sm:pl-5">
                  <dt className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
                    {severity}
                  </dt>
                  <dd
                    className={`mt-2 font-display text-4xl leading-none sm:text-5xl ${
                      severity === "Critical" && count > 0
                        ? "text-critical"
                        : count === 0
                          ? "text-muted"
                          : ""
                    }`}
                  >
                    {count}
                  </dd>
                </div>
              );
            })}
          </dl>
        </div>
      </header>

      {(mainIssue ||
        report.deployment_status !== null ||
        report.readiness_score !== null ||
        (fixSummary && fixSummary.fixable_count > 0)) && (
        <div className="mt-12 flex flex-wrap gap-5">
          {mainIssue && <MainIssue finding={mainIssue} scanId={scanId} />}
          <DeploymentBadge
            status={report.deployment_status}
            readinessScore={report.readiness_score}
            scanId={scanId}
          />
          {fixSummary && fixSummary.fixable_count > 0 && (
            <FixCountBadge summary={fixSummary} scanId={scanId} />
          )}
        </div>
      )}

      {report.summary && (
        <section className="mt-20">
          <h2 className="font-mono text-sm uppercase tracking-[0.3em] text-muted">
            Assessment
          </h2>
          {/* Capped short of the article's full width — at 7xl an unbroken
              paragraph would run past a comfortable line length and get
              harder to read, not easier. */}
          <p className="mt-6 max-w-4xl font-display text-xl leading-relaxed sm:text-2xl lg:text-3xl">
            {report.summary}
          </p>
        </section>
      )}

    </article>

    {/* Outside the article: AgentReel's panels used to bleed to the
        viewport's left edge, which needed to sit outside a centred max-width
        column. AgentCarousel centres itself instead, but staying outside the
        article keeps this a two-line swap rather than a layout change.
        Clicking a card opens a popup with a link to that agent's own detail
        page — this page stays a summary, the depth lives one click away. */}
    <AgentCarousel agents={report.agents} info={agentInfo} scanId={scanId} />
    <Footer />
    </>
  );
}
