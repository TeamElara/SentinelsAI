"use client";

/* Shell for all /scan/[scanId]/* pages.

   Provides a minimal nav bar: a "Sentinels" link back to the scan launcher,
   and — when you're on an agent detail page — a breadcrumb back to the scan
   overview. All scan sub-pages sit inside this frame. */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { FloatingChatButton } from "@/components/chat/FloatingChatButton";
import { UsageBadge } from "@/components/UsageBadge";
import { fetchScan } from "@/lib/api";
import type { TargetType } from "@/lib/api";

export default function ScanLayout({ children }: { children: React.ReactNode }) {
  const { scanId } = useParams<{ scanId: string }>();
  const pathname = usePathname();

  // The Files tab (R12) only exists for repo scans, and target_type isn't in
  // the URL — every page under here already fetches the scan on its own
  // (agent/checklist pages, this file's sibling `files/page.tsx`), so one
  // more lightweight fetch just for the nav bar matches how the rest of this
  // app already works rather than introducing a shared cache for one field.
  const [targetType, setTargetType] = useState<TargetType | null>(null);
  useEffect(() => {
    fetchScan(scanId)
      .then((report) => setTargetType(report.target_type))
      .catch(() => {});
  }, [scanId]);

  const isAgentPage = pathname?.includes("/agents/");
  const isChecklistPage = pathname?.endsWith("/checklist");
  const isFilesPage = pathname?.endsWith("/files");
  const isChatPage = pathname?.endsWith("/chat");

  function navLink(label: string, href: string, active: boolean) {
    return (
      <Link
        href={href}
        className={`font-mono text-xs uppercase tracking-[0.2em] transition-colors hover:text-parchment ${
          active ? "text-parchment" : "text-muted"
        }`}
      >
        {label}
      </Link>
    );
  }

  return (
    <div className="flex min-h-full flex-col">
      <nav className="border-b border-rule px-6 py-4">
        {/* `justify-between` so the crumbs stay left and Settings sits at the
            right edge — it's a destination, not another step in this trail. */}
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-5">
          <div className="flex items-center gap-5">
          <Link
            href="/"
            className="font-mono text-xs uppercase tracking-[0.35em] text-muted transition-colors hover:text-parchment"
          >
            Sentinels
          </Link>

          <span className="font-mono text-xs text-rule">/</span>

          {isAgentPage ? (
            <>
              {navLink("Overview", `/scan/${scanId}`, false)}
              <span className="font-mono text-xs text-rule">/</span>
              <span className="font-mono text-xs text-parchment">Agent</span>
            </>
          ) : (
            <>
              {navLink("Overview", `/scan/${scanId}`, !isChecklistPage && !isFilesPage)}
              <span className="font-mono text-xs text-rule">·</span>
              {navLink("Checklist", `/scan/${scanId}/checklist`, isChecklistPage)}
              {targetType === "repo" && (
                <>
                  <span className="font-mono text-xs text-rule">·</span>
                  {navLink("Files", `/scan/${scanId}/files`, isFilesPage)}
                </>
              )}
            </>
          )}
          </div>

          <div className="flex items-center gap-5">
            {/* PLAN-v5 Stage E: this scan's own remediation history —
                everything Sentinels has recorded planning, opening, or
                verifying a fix for it. */}
            <Link
              href={`/audit?scan=${scanId}`}
              className="font-mono text-xs uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
            >
              Audit
            </Link>

            {/* PLAN-v5 Stage B: connecting a repository is what makes the fix
                flow on a finding row able to open a pull request, so the link to
                it lives where those findings are read. */}
            <Link
              href="/settings"
              className="font-mono text-xs uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
            >
              Settings
            </Link>
          </div>
        </div>
      </nav>

      <div className="mx-auto w-full max-w-3xl px-6"><UsageBadge /></div>
      {children}

      {!isChatPage && <FloatingChatButton scanId={scanId} />}
    </div>
  );
}
