"use client";

/* PLAN-v5 Stage 0's front door. No form, no password field — the only
   action here is a link to `GET /auth/github/login`, which is a full page
   navigation (not a fetch) because it ends in a redirect to github.com;
   `fetch` can't hand a redirect off to the browser's address bar the way a
   plain <a> can. */

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { githubLoginUrl } from "@/lib/api";

const ERROR_MESSAGES: Record<string, string> = {
  state_mismatch: "That sign-in link expired or was already used. Try again.",
  missing_code: "GitHub didn't send back an authorization code. Try again.",
  exchange_failed: "GitHub rejected the sign-in attempt. Try again.",
  identity_failed: "Couldn't read your GitHub profile. Try again.",
  not_invited: "Sentinels is invite-only during the beta. Ask the team for an invite.",
  suspended: "This account has been suspended.",
  server_not_configured:
    "Sign-in isn't configured on this server yet (missing SENTINELS_SESSION_SECRET).",
};

function LoginError() {
  const params = useSearchParams();
  const error = params.get("error");
  if (!error) return null;
  return (
    <p className="glass mt-6 max-w-sm px-4 py-3 text-center font-mono text-[10px] uppercase tracking-[0.2em] text-critical">
      {ERROR_MESSAGES[error] ?? "Sign-in failed. Try again."}
    </p>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
      <h1 className="font-display text-4xl text-parchment">Sentinels</h1>
      <p className="max-w-sm text-sm leading-relaxed text-muted">
        Sign in with GitHub to run scans and, once connected, open pull
        requests for fixes on repositories you authorize.
      </p>
      <a
        href={githubLoginUrl()}
        className="glass border-parchment/25 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10"
      >
        Sign in with GitHub →
      </a>
      <p className="max-w-sm font-mono text-[10px] uppercase leading-relaxed tracking-[0.2em] text-muted">
        By signing in you agree to the{" "}
        <Link href="/terms" className="underline decoration-rule hover:text-parchment">
          Terms
        </Link>{" "}
        and have read the{" "}
        <Link href="/privacy" className="underline decoration-rule hover:text-parchment">
          Privacy
        </Link>{" "}
        notice.
      </p>
      <Suspense fallback={null}>
        <LoginError />
      </Suspense>
    </main>
  );
}
