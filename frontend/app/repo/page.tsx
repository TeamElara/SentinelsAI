"use client";

/* The repository launcher — the route the GitHub card leads to.

   The repo-side sibling of `app/url/page.tsx`: same shell, same
   `ScanLauncher`, only `targetType="repo"` differs — which is what tells
   `ScanLauncher` to stream from `/repo/stream` instead of `/scan/stream`
   and `ScanProgress` to show the five repo agents instead of the five URL
   agents. `docs/PLAN-v3.md` R11 reserves this path deliberately top-level
   (`/repo`, not `/scan/repo`, which the `/scan/[scanId]` dynamic segment
   would swallow). The landing page's GitHub card already points here and
   needed zero changes when this landed. */

import Link from "next/link";
import { ScanLauncher } from "@/components/ScanLauncher";

export default function RepoScanPage() {
  return (
    <section className="flex flex-1 items-center justify-center px-6 py-24">
      <div className="mx-auto w-full max-w-3xl">
        <Link
          href="/"
          className="font-mono text-xs uppercase tracking-[0.35em] text-muted transition-colors hover:text-parchment"
        >
          Sentinels
        </Link>

        <ScanLauncher
          targetType="repo"
          placeholder="github.com/owner/repo"
          footnote={
            <p className="mt-16 max-w-md font-mono text-xs leading-relaxed text-muted">
              Passive inspection only. Sentinels reads a public repository&apos;s
              files — it never executes anything it finds, and never writes
              back. Secrets are reported masked, never in full.
            </p>
          }
        >
          <h1 className="mt-12 font-display text-5xl leading-[1.08] sm:text-6xl">
            Every commit leaves
            <br />a record.
          </h1>

          <p className="mt-7 max-w-md leading-relaxed text-muted">
            Sentinels reads it — secrets, vulnerable dependencies, insecure
            configuration and risky code patterns — and issues a graded
            readiness report for a public GitHub repository.
          </p>
        </ScanLauncher>
      </div>
    </section>
  );
}
