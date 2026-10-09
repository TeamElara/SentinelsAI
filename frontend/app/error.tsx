"use client";

import { useEffect } from "react";
import Link from "next/link";

import { ISSUES_URL } from "@/lib/links";

/* Shown when something throws while a page renders. In production Next hides
   the real message from the browser (it may hold details that shouldn't leave
   the server) and passes a `digest`, a short reference that matches the line in
   the server log, so that is what a person can quote when they report it.

   This Next version names the recovery function `retry`, not `reset`. */
export default function ErrorPage({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
      <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted">Error</p>
      <h1 className="font-display text-4xl text-parchment sm:text-5xl">That didn&apos;t work</h1>
      <p className="max-w-sm text-sm leading-relaxed text-muted">
        Something went wrong on our side while loading this page. Your scans are safe. Try
        again, and if it keeps happening, report it and quote the reference below.
      </p>
      {error.digest && (
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
          Reference {error.digest}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-center gap-6">
        <button
          type="button"
          onClick={() => retry()}
          className="glass border-parchment/25 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10"
        >
          Try again
        </button>
        <Link
          href="/"
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted underline decoration-rule transition-colors hover:text-parchment"
        >
          Back to the start
        </Link>
        <a
          href={ISSUES_URL}
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted underline decoration-rule transition-colors hover:text-parchment"
        >
          Report a problem
        </a>
      </div>
    </main>
  );
}
