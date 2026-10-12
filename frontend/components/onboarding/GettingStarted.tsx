"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import {
  fetchScanSummaries,
  githubInstallUrl,
  type GitHubInstallation,
  type ScanSummary,
} from "@/lib/api";

/* First-run guide (Launch Plan 5.4). Three steps that end at a first fix, each
   ticked from what the account actually has rather than from a flag someone
   could forget to set. It goes away by itself once all three are true, so it
   never becomes permanent clutter.

   Step 3 says "Only select repositories": the App can be granted to every
   repository of an account, and a first-time user should start with one. */

type Step = { done: boolean; title: string; body: string; action?: React.ReactNode };

const ACTION_CLASSES =
  "glass mt-4 inline-block border-parchment/25 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10";

export function GettingStarted({
  installations,
}: {
  /** From the page that already loads them; null while loading. */
  installations: GitHubInstallation[] | null;
}) {
  const [scans, setScans] = useState<ScanSummary[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchScanSummaries()
      .then((rows) => {
        if (!cancelled) setScans(rows);
      })
      // The guide is a convenience. If the list can't load it simply stays
      // out of the way instead of showing a second error on the page.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (scans === null || installations === null) return null;

  const hasSiteScan = scans.some((s) => s.target_type === "url");
  const hasConnection = installations.some((i) => i.revoked_at === null);

  const steps: Step[] = [
    {
      done: true,
      title: "Sign in with GitHub",
      body: "Done. Your scans and connected repositories belong to this account only.",
    },
    {
      done: hasSiteScan,
      title: "Scan a website you own",
      body: "Paste its address and confirm you may test it. The scan only reads what is already public, and takes about a minute.",
      action: (
        <Link href="/url" className={ACTION_CLASSES}>
          Scan a website →
        </Link>
      ),
    },
    {
      done: hasConnection,
      title: "Connect one test repository and try a fix",
      body: "Install the Sentinels App and choose “Only select repositories”, then pick a repository you don't mind experimenting on. Sentinels shows each fix as an exact change and opens a pull request only after you approve it. It never merges.",
      action: (
        <a href={githubInstallUrl()} className={ACTION_CLASSES}>
          Connect a repository →
        </a>
      ),
    },
  ];

  if (steps.every((step) => step.done)) return null;
  const next = steps.findIndex((step) => !step.done);

  return (
    <section aria-labelledby="getting-started" className="mt-14 border border-rule px-6 py-7 sm:px-8">
      <h2
        id="getting-started"
        className="font-mono text-xs uppercase tracking-[0.3em] text-muted"
      >
        Getting started
      </h2>
      <ol className="mt-6 space-y-7">
        {steps.map((step, index) => (
          <li key={step.title} className="flex gap-4">
            <span
              aria-hidden="true"
              className={`mt-1 flex h-5 w-5 shrink-0 items-center justify-center border font-mono text-[10px] ${
                step.done ? "border-parchment/60 text-parchment" : "border-rule text-muted"
              }`}
            >
              {step.done ? "✓" : index + 1}
            </span>
            <div className="min-w-0">
              <p className={`text-lg leading-snug ${step.done ? "text-muted" : "text-parchment"}`}>
                {step.title}
                <span className="sr-only">{step.done ? " (done)" : " (not done yet)"}</span>
              </p>
              {(!step.done || index === next) && (
                <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted">{step.body}</p>
              )}
              {index === next && step.action}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
