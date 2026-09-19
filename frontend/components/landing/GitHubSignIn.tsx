"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchMe, githubLoginUrl, type SessionUser } from "@/lib/api";

const CLASSES =
  "fixed right-5 top-5 z-50 border border-parchment/35 bg-black/75 px-4 py-2 " +
  "font-mono text-[10px] uppercase tracking-[0.18em] text-parchment " +
  "backdrop-blur transition-colors hover:bg-white/15 sm:right-8 sm:top-8";

/** Landing-page account chip: sign-in link when signed out, the session's
    GitHub login (linking to Settings) once signed in. Sign-in lands back on
    this page, so without this there's no visible sign it worked. */
export function GitHubSignIn() {
  const [user, setUser] = useState<SessionUser | null>(null);

  useEffect(() => {
    fetchMe().then(setUser).catch(() => setUser(null));
  }, []);

  if (user) {
    return (
      <Link href="/settings" className={CLASSES}>
        @{user.github_login} · Settings
      </Link>
    );
  }

  return (
    <a href={githubLoginUrl()} className={CLASSES}>
      Sign in with GitHub
    </a>
  );
}
