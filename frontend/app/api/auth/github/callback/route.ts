import { NextRequest, NextResponse } from "next/server";

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
 * Completes OAuth on the frontend origin: forwards GitHub's callback to
 * Render server-to-server, then re-sets the session cookie Render issued as
 * a first-party cookie on this (Vercel) domain. Every later `/api/*` call
 * from the browser then carries a same-origin cookie, sidestepping
 * third-party-cookie blocking between the Vercel and Render domains.
 */
export async function GET(request: NextRequest) {
  const state = request.cookies.get("sentinels_oauth_state")?.value;
  const upstream = await fetch(
    `${backendOrigin()}/auth/github/callback${request.nextUrl.search}`,
    {
      redirect: "manual",
      cache: "no-store",
      headers: state ? { cookie: `sentinels_oauth_state=${state}` } : {},
    },
  );
  const destination =
    upstream.headers.get("location") ??
    new URL("/login?error=exchange_failed", request.url).toString();
  const session = cookieValue(upstream.headers.get("set-cookie"), "sentinels_session");

  const response = NextResponse.redirect(destination);
  response.headers.set("Cache-Control", "no-store");
  response.cookies.delete("sentinels_oauth_state");
  if (session) {
    response.cookies.set("sentinels_session", session, {
      httpOnly: true,
      maxAge: 60 * 60 * 24 * 14,
      path: "/",
      sameSite: "lax",
      secure: true,
    });
  }
  return response;
}
