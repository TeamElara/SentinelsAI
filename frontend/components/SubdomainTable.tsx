"use client";

/* The attack-surface table — every host the `subdomain` agent discovered,
   whether or not it turned up an issue. Rendered on that agent's own detail
   page only (PLAN-v4 §V8); every other agent's page is untouched.

   Sortable by issue count because that's the one column a reader actually
   wants ranked — everything else is context for a row once you've picked it,
   not something you'd sort a 25-row table by. */

import { useMemo, useState } from "react";
import type { SubdomainEntry } from "@/lib/api";

type SortDir = "desc" | "asc";

function schemeLabel(entry: SubdomainEntry): string {
  if (!entry.scheme) return "—";
  if (entry.tls_valid === null) return entry.scheme;
  return `${entry.scheme} · ${entry.tls_valid ? "valid" : "invalid"}`;
}

export function SubdomainTable({ entries }: { entries: SubdomainEntry[] }) {
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    const copy = [...entries];
    copy.sort((a, b) =>
      sortDir === "desc" ? b.issue_count - a.issue_count : a.issue_count - b.issue_count,
    );
    return copy;
  }, [entries, sortDir]);

  if (entries.length === 0) return null;

  return (
    <section className="mt-16">
      <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
        Subdomain inventory
      </h2>
      <p className="mt-2 font-mono text-xs text-muted sm:text-sm">
        {entries.length} host{entries.length === 1 ? "" : "s"} discovered
      </p>

      <div className="glass mt-6 overflow-x-auto">
        <table className="w-full min-w-[860px] border-collapse text-left">
          <thead>
            <tr className="border-b border-rule">
              {["Host", "Source", "Record", "HTTP", "TLS", "Server", "Redirects to"].map(
                (label) => (
                  <th
                    key={label}
                    className="whitespace-nowrap px-4 py-3 font-mono text-[10px] uppercase tracking-[0.2em] text-muted"
                  >
                    {label}
                  </th>
                ),
              )}
              <th className="px-4 py-3 text-right font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
                <button
                  type="button"
                  onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
                  className="uppercase tracking-[0.2em] transition-colors hover:text-parchment"
                >
                  Issues {sortDir === "desc" ? "↓" : "↑"}
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((entry) => (
              <tr
                key={entry.host}
                className="border-b border-rule last:border-b-0 hover:bg-white/4"
              >
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs sm:text-sm">
                  {entry.host}
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">
                  {entry.source}
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">
                  {entry.record_type} {entry.record_value}
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">
                  {entry.http_status ?? "—"}
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">
                  {schemeLabel(entry)}
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">
                  {entry.server ?? "—"}
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">
                  {entry.redirects_to ?? "—"}
                </td>
                <td
                  className={`whitespace-nowrap px-4 py-3 text-right font-mono text-xs sm:text-sm ${
                    entry.issue_count > 0 ? "text-critical" : "text-muted"
                  }`}
                >
                  {entry.issue_count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
