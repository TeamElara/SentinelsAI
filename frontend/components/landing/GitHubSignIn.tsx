"use client";

import { githubLoginUrl } from "@/lib/api";

/** First-party OAuth entry point for the landing page. */
export function GitHubSignIn() {
  return (
    <a
      href={githubLoginUrl()}
      className="fixed right-5 top-5 z-50 border border-parchment/35 bg-black/75 px-4 py-2
                 font-mono text-[10px] uppercase tracking-[0.18em] text-parchment
                 backdrop-blur transition-colors hover:bg-white/15 sm:right-8 sm:top-8"
    >
      Sign in with GitHub
    </a>
  );
}
