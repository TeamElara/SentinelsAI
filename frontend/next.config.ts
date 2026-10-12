import type { NextConfig } from "next";

import { sentryEnvelopeUrl } from "./lib/sentry-tunnel";

/* Security headers for every response this frontend serves (Launch Plan C11).

   Check deployed response headers and browser behavior directly; a scanner
   score is only supplementary evidence. What each header does, in order:

   - Content-Security-Policy: only this origin may supply scripts, styles,
     images, fonts and connections, nothing may be embedded as <object>, and
     nobody may frame the site (`frame-ancestors 'none'`). Forms may only post
     back to this origin, and `<base>` can't be rewritten to redirect relative
     URLs.
   - Strict-Transport-Security: browsers that have seen the site over HTTPS
     refuse to use plain HTTP for it for two years. (Ignored over http://, so
     it does nothing on localhost.)
   - X-Content-Type-Options: browsers don't guess a file's type, so an uploaded
     or reflected file can't be run as a script.
   - Referrer-Policy: other sites learn which site you came from, not which page
     (and so not a scan id in the path).
   - X-Frame-Options: the older twin of `frame-ancestors`, for browsers that
     don't read the CSP.
   - Permissions-Policy: no camera, microphone, location or payment prompts,
     because nothing here uses them.

   KNOWN LIMIT: `script-src` allows 'unsafe-inline'. A nonce-based policy would
   not, but nonces need every page rendered per request, and this app has
   statically generated pages. Next's own scripts are inline, so without a nonce
   they would be blocked. Until that trade is made on purpose, an injected
   inline script would still run; `frame-ancestors`,
   `object-src`, `base-uri`, `form-action` and `connect-src` still hold. */

const isDev = process.env.NODE_ENV === "development";

// Mirrors API_BASE in lib/api.ts. With an https API (the deployed setup) every
// call goes through this origin's /api rewrite, so 'self' is enough. Otherwise
// (local development, `next start` on a laptop) the browser talks straight to
// the API on another port, and that origin has to be allowed to connect.
function directApiOrigin(): string {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
  if (apiBase.startsWith("https://") || apiBase.startsWith("/")) return "";
  try {
    return new URL(apiBase).origin;
  } catch {
    return "";
  }
}

const contentSecurityPolicy = [
  "default-src 'self'",
  // React refreshes with eval in dev only; production needs none.
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${directApiOrigin()}`.trim(),
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

const nextConfig: NextConfig = {
  env: {
    // Next only inlines a NEXT_PUBLIC_ variable that is set. Naming it here
    // makes an unset DSN the constant "", so `if (process.env.NEXT_PUBLIC_SENTRY_DSN)`
    // is removed at build time and a deploy without a DSN ships none of the SDK.
    NEXT_PUBLIC_SENTRY_DSN: process.env.NEXT_PUBLIC_SENTRY_DSN ?? "",
    // The deployed commit, read by the browser SDK as its release. Vercel sets
    // VERCEL_GIT_COMMIT_SHA at build time; it is not a secret.
    NEXT_PUBLIC_RELEASE: process.env.VERCEL_GIT_COMMIT_SHA ?? "",
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async rewrites() {
    // NEXT_PUBLIC_API_BASE is set in Vercel to the Render API's origin (e.g.
    // https://sentinels-api.onrender.com). This rewrite is what makes
    // API_BASE = "/api" in lib/api.ts actually reach that backend — the
    // browser only ever talks to this same origin, and Next's server relays
    // the request (cookies included) to Render behind the scenes.
    const backendOrigin = process.env.NEXT_PUBLIC_API_BASE;
    const rules: { source: string; destination: string }[] = [];

    // Browser error reports go to Sentry through this origin, so the CSP's
    // connect-src stays 'self'. Off unless a valid DSN is set (lib/sentry-tunnel.ts).
    const sentryUrl = sentryEnvelopeUrl(process.env.NEXT_PUBLIC_SENTRY_DSN);
    if (sentryUrl) rules.push({ source: "/monitoring", destination: sentryUrl });

    if (backendOrigin && !backendOrigin.startsWith("/")) {
      rules.push({
        source: "/api/:path*",
        destination: `${backendOrigin.replace(/\/$/, "")}/:path*`,
      });
    }
    return rules;
  },
};

export default nextConfig;
