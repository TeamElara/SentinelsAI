import { NextResponse } from "next/server";

function backendOrigin(): string {
  const origin = process.env.NEXT_PUBLIC_API_BASE;
  if (!origin || origin.startsWith("/")) {
    throw new Error("NEXT_PUBLIC_API_BASE must be the Render API origin on Vercel.");
  }
  return origin.replace(/\/$/, "");
}

function cookieValue(header: string | null, name: string): string | null {
  const match = header?.match(new RegExp(`(?:^|,\\s*)${name}=([^;]+)`));
  return match?.[1] ?? null;
}

/**
 * Starts OAuth from the Vercel origin itself. Keeping the short-lived CSRF
 * `state` cookie on this origin, rather than one set by Render mid-redirect,
 * means it's present on the way back regardless of any browser's cross-site
 * cookie policy.
 */
export async function GET() {
  const upstream = await fetch(`${backendOrigin()}/auth/github/login`, {
    redirect: "manual",
    cache: "no-store",
  });
  const destination = upstream.headers.get("location");
  const state = cookieValue(upstream.headers.get("set-cookie"), "sentinels_oauth_state");

  if (!destination || !state) {
    return NextResponse.json({ detail: "GitHub sign-in could not be started." }, { status: 502 });
  }

  const response = NextResponse.redirect(destination);
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set("sentinels_oauth_state", state, {
    httpOnly: true,
    maxAge: 600,
    path: "/",
    sameSite: "lax",
    secure: true,
  });
  return response;
}
