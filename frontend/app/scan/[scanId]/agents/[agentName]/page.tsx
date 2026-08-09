"use client";

/* Per-agent detail page — /scan/[scanId]/agents/[agentName].

   Shows everything one agent produced: its purpose, what it checks, a verdict
   derived from the findings, the findings themselves with evidence, and timing.

   The real test for M8 (per PLAN-v2.md): temporarily add a 6th agent to
   registry.py, scan a site, and this page appears for that agent with zero
   frontend changes. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchAgents, fetchRepoAgents, fetchAgentResult } from "@/lib/agents";
import { fetchScan } from "@/lib/api";
import type { AgentInfo, AgentResult, SubdomainEntry } from "@/lib/api";
import { FindingRow } from "@/components/FindingRow";
import { SubdomainTable } from "@/components/SubdomainTable";
import { isProblem } from "@/lib/findings";

function getVerdict(result: AgentResult): "clean" | "issues_found" | "failed" {
  if (result.error) return "failed";
  if (result.findings.some(isProblem)) return "issues_found";
  return "clean";
}

const VERDICT_LABEL: Record<string, string> = {
  clean: "Clean",
  issues_found: "Issues found",
  failed: "Failed",
};

export default function AgentPage() {
  const { scanId, agentName } = useParams<{
    scanId: string;
    agentName: string;
  }>();

  const [info, setInfo] = useState<AgentInfo | null>(null);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  // Only the subdomain agent's page needs the full report — every other
  // agent's findings are entirely covered by `fetchAgentResult` above, so
  // fetching the whole scan for them would just be an unused extra request.
  const [subdomains, setSubdomains] = useState<SubdomainEntry[]>([]);

  useEffect(() => {
    Promise.all([
      // Fetch metadata (purpose, checks) alongside the result (findings,
      // timing) in parallel — the page needs both before it can render.
      // The URL param alone doesn't say which scan type this is, so try
      // the URL agent list first, then the repo one — "repo-secrets" etc.
      // never appears in fetchAgents(), only fetchRepoAgents().
      fetchAgents().then(async (agents) => {
        const match = agents.find((a) => a.name === agentName);
        if (match) return match;
        const repoAgents = await fetchRepoAgents();
        return repoAgents.find((a) => a.name === agentName) ?? null;
      }),
      fetchAgentResult(scanId, agentName),
    ])
      .then(([agentInfo, agentResult]) => {
        setInfo(agentInfo);
        setResult(agentResult);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));

    if (agentName === "subdomain") {
      fetchScan(scanId)
        .then((report) => setSubdomains(report.subdomains))
        .catch(() => setSubdomains([]));
    }
  }, [scanId, agentName]);

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-24">
        <p className="animate-pulse font-mono text-xs uppercase tracking-[0.35em] text-muted">
          Loading…
        </p>
      </div>
    );
  }

  if (notFound || !result) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-24">
        <div className="text-center">
          <p className="font-display text-2xl">Agent not found.</p>
          <Link
            href={`/scan/${scanId}`}
            className="glass mt-8 inline-block px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] transition-colors hover:bg-white/8"
          >
            Back to overview
          </Link>
        </div>
      </div>
    );
  }

  const verdict = getVerdict(result);
  const problems = result.findings.filter(isProblem);
  const passed = result.findings.filter((f) => !isProblem(f));

  // Collect all structured evidence items across every finding from this agent.
  // Each item is tagged with its finding's title so the reader knows which
  // finding it belongs to without re-reading the whole list.
  const allEvidence = result.findings.flatMap((f) =>
    (f.evidence_items ?? []).map((ev) => ({ ...ev, findingTitle: f.title })),
  );

  return (
    <article className="mx-auto w-full max-w-7xl px-6 py-20 sm:px-8">
      <header>
        <p className="font-mono text-xs uppercase tracking-[0.35em] text-muted">
          Agent
        </p>
        <h1 className="mt-4 font-display text-5xl sm:text-6xl lg:text-7xl">
          {info?.display_name ?? agentName}
        </h1>
        {info?.purpose && (
          // Capped short of the article's full width, same reasoning as the
          // overview page's Assessment paragraph: at 7xl an unbroken line of
          // prose runs past a comfortable length to read.
          <p className="mt-4 max-w-3xl leading-relaxed text-muted sm:text-lg lg:text-xl">
            {info.purpose}
          </p>
        )}

        {/* Ruled strip, same pattern as the overview page's severity counts —
            a shared left edge is what keeps three unrelated numbers reading
            as one set once there's enough width for them to drift apart. */}
        <dl className="mt-9 grid grid-cols-3 gap-x-6 gap-y-6 sm:inline-grid sm:auto-cols-max sm:grid-flow-col sm:gap-x-14">
          <div className="min-w-0 border-l border-rule pl-4 sm:pl-5">
            <dt className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
              Verdict
            </dt>
            <dd className="mt-2 font-display text-3xl leading-none sm:text-4xl">
              {VERDICT_LABEL[verdict]}
            </dd>
          </div>
          <div className="min-w-0 border-l border-rule pl-4 sm:pl-5">
            <dt className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
              Duration
            </dt>
            <dd className="mt-2 font-display text-3xl leading-none sm:text-4xl">
              {result.duration_ms}ms
            </dd>
          </div>
          <div className="min-w-0 border-l border-rule pl-4 sm:pl-5">
            <dt className="font-mono text-xs uppercase tracking-[0.2em] text-muted">
              Issues
            </dt>
            <dd
              className={`mt-2 font-display text-3xl leading-none sm:text-4xl ${
                problems.length === 0 ? "text-muted" : ""
              }`}
            >
              {problems.length}
            </dd>
          </div>
        </dl>
      </header>

      {result.error && (
        <section className="mt-10 border-l-2 border-critical pl-5">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-critical">
            Agent error
          </p>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted sm:text-base">
            {result.error}
          </p>
        </section>
      )}

      {info?.checks && info.checks.length > 0 && (
        <section className="mt-16">
          <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
            What this agent checks
          </h2>
          <ul className="mt-6 grid gap-x-10 gap-y-3 sm:grid-cols-2">
            {info.checks.map((check, i) => (
              <li
                key={i}
                className="flex gap-3 text-sm leading-relaxed text-muted sm:text-base"
              >
                <span className="mt-1.5 font-mono text-[10px] text-rule">—</span>
                {check}
              </li>
            ))}
          </ul>
        </section>
      )}

      {agentName === "subdomain" && <SubdomainTable entries={subdomains} />}

      {problems.length > 0 ? (
        <section className="mt-16">
          <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
            Issues
          </h2>
          <ul className="mt-7 max-w-3xl space-y-9">
            {problems.map((f) => (
              <FindingRow key={f.id} finding={f} scanId={scanId} />
            ))}
          </ul>
        </section>
      ) : (
        !result.error && (
          // No FindingRow means no "Fix with AI" button either — say so
          // explicitly, so a clean agent reads as "nothing to fix" rather
          // than "the fix feature is missing here."
          <section className="mt-16">
            <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
              Issues
            </h2>
            <p className="mt-4 text-sm text-muted sm:text-base">
              Every check passed here — nothing to fix.
            </p>
          </section>
        )
      )}

      {passed.length > 0 && (
        <p className="mt-10 max-w-3xl font-mono text-xs leading-relaxed text-muted sm:text-sm">
          <span className="uppercase tracking-[0.2em]">Passed — </span>
          {passed.map((f) => f.title).join(" · ")}
        </p>
      )}

      {allEvidence.length > 0 && (
        <section className="mt-16">
          <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
            Evidence
          </h2>
          <div className="mt-6 grid gap-4 sm:grid-cols-2">
            {allEvidence.map((ev, i) => (
              <div key={i} className="glass px-5 py-4">
                <p className="font-mono text-[9px] uppercase tracking-[0.25em] text-muted">
                  {ev.kind} — {ev.label}
                </p>
                <p className="mt-2 break-words font-mono text-xs leading-relaxed sm:text-sm">
                  {ev.content}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}
    </article>
  );
}
