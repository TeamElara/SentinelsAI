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
 *
 * This is also where GitHub lands after an App *install*: with "Request user
 * authorization (OAuth) during installation" on, the Setup URL is disabled and
 * the callback URL gets `installation_id` instead. Render tells the two apart,
 * but it can only finish an install if it receives the cookies that tie the
 * redirect to this browser: the signed-in session and the install state. A
 * sign-in forwards only its own state cookie, as before.
 */
export async function GET(request: NextRequest) {
  const installing = request.nextUrl.searchParams.has("installation_id");
  const forwarded = (installing
    ? ["sentinels_install_state", "sentinels_session"]
    : ["sentinels_oauth_state"]
  )
    .map((name) => [name, request.cookies.get(name)?.value] as const)
    .filter(([, value]) => value)
    .map(([name, value]) => `${name}=${value}`);
  const upstream = await fetch(
    `${backendOrigin()}/auth/github/callback${request.nextUrl.search}`,
    {
      redirect: "manual",
      cache: "no-store",
      headers: forwarded.length ? { cookie: forwarded.join("; ") } : {},
    },
  );
  const destination =
    upstream.headers.get("location") ??
    new URL("/login?error=exchange_failed", request.url).toString();
  const session = cookieValue(upstream.headers.get("set-cookie"), "sentinels_session");

  const response = NextResponse.redirect(destination);
  response.headers.set("Cache-Control", "no-store");
  response.cookies.delete("sentinels_oauth_state");
  if (installing) response.cookies.delete("sentinels_install_state");
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
