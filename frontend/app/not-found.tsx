import Link from "next/link";

import { ISSUES_URL } from "@/lib/links";

/* The page for an address that doesn't exist, or a scan that isn't yours: the
   backend answers both the same way on purpose, so this page doesn't try to say
   which one it was. */
export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
      <p className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted">404</p>
      <h1 className="font-display text-4xl text-parchment sm:text-5xl">Nothing here</h1>
      <p className="max-w-sm text-sm leading-relaxed text-muted">
        That page doesn&apos;t exist, or it&apos;s a report that belongs to another account.
        Check the address, or head back and start a new scan.
      </p>
      <div className="flex flex-wrap items-center justify-center gap-6">
        <Link
          href="/"
          className="glass border-parchment/25 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10"
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
