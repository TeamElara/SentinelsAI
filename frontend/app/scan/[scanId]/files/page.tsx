"use client";

/* File-tree browser for a repo scan — /scan/[scanId]/files.

   R12, the last milestone in docs/PLAN-v3.md. Every file the repo scan
   walked, as a collapsible tree; each node badged with its finding count;
   selecting a file lists its findings with the same FindingRow +
   "Fix with AI" every other page already uses — nothing new needed there. */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchScan, fetchScanFiles } from "@/lib/api";
import type { Finding, RepoFileEntry } from "@/lib/api";
import { buildFileTree } from "@/lib/fileTree";
import { FileTreeView } from "@/components/files/FileTreeView";
import { FindingRow } from "@/components/FindingRow";

export default function FilesPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const [files, setFiles] = useState<RepoFileEntry[] | null>(null);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchScanFiles(scanId), fetchScan(scanId)])
      .then(([fileEntries, report]) => {
        setFiles(fileEntries);
        setFindings(report.findings);
      })
      .finally(() => setLoading(false));
  }, [scanId]);

  // Rebuilding the tree is a full walk of the file list — memoized so typing
  // in some future search box, or any other state change on this page,
  // doesn't redo it on every render, only when `files` actually changes.
  const tree = useMemo(() => buildFileTree(files ?? []), [files]);
  const selectedFindings = useMemo(
    () => findings.filter((f) => f.file_path === selectedPath),
    [findings, selectedPath],
  );

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-24">
        <p className="animate-pulse font-mono text-xs uppercase tracking-[0.35em] text-muted">
          Loading…
        </p>
      </div>
    );
  }

  if (!files || files.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 py-24">
        <div className="mx-auto max-w-md text-center">
          <p className="font-mono text-xs uppercase tracking-[0.35em] text-muted">
            Not available
          </p>
          <p className="mt-4 font-display text-2xl">No file tree for this scan.</p>
          <Link
            href={`/scan/${scanId}`}
            className="glass mt-8 inline-block px-6 py-3 font-mono text-xs uppercase tracking-[0.2em] transition-colors hover:bg-white/8"
          >
            Overview
          </Link>
        </div>
      </div>
    );
  }

  return (
    <article className="mx-auto w-full max-w-7xl px-6 py-20 sm:px-8">
      <header className="flex flex-wrap items-start justify-between gap-x-8 gap-y-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
            File tree
          </p>
          <h1 className="mt-3 font-display text-3xl sm:text-4xl lg:text-5xl">
            Repository files
          </h1>
          <p className="mt-2 font-mono text-xs text-muted sm:text-sm">
            {files.length} files scanned
          </p>
        </div>
        <Link
          href={`/scan/${scanId}`}
          className="glass px-4 py-2 font-mono text-[10px] uppercase tracking-[0.2em] transition-colors hover:bg-white/8"
        >
          Overview
        </Link>
      </header>

      {/* The tree column stays a fixed, modest width — a file tree gains
          nothing from stretching, it just puts more distance between a name
          and its indent level. The findings column is what actually gets the
          benefit of the wider page. */}
      <div className="mt-12 grid gap-10 sm:grid-cols-[minmax(0,260px)_1fr]">
        <div className="glass max-h-[70vh] overflow-y-auto px-4 py-4">
          <FileTreeView root={tree} selectedPath={selectedPath} onSelect={setSelectedPath} />
        </div>

        <div>
          {!selectedPath ? (
            <p className="text-sm text-muted sm:text-base">
              Select a file to see its findings.
            </p>
          ) : (
            <div>
              <p className="break-words font-mono text-xs text-muted sm:text-sm">
                {selectedPath}
              </p>
              {selectedFindings.length === 0 ? (
                <p className="mt-4 text-sm text-muted sm:text-base">
                  No findings recorded for this file. Review agent coverage for limitations.
                </p>
              ) : (
                <ul className="mt-6 max-w-3xl space-y-9">
                  {selectedFindings.map((f) => (
                    <FindingRow key={f.id} finding={f} scanId={scanId} isRepoScan />
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
