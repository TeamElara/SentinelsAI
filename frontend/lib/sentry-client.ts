/* Starts browser error reporting. Only ever loaded through a dynamic import
   that is guarded by NEXT_PUBLIC_SENTRY_DSN, so a deploy without a DSN ships
   none of the SDK and sends nothing (docs/FRONTEND-SENTRY-DESIGN.md).

   Integrations are an allowlist, not the defaults minus a few: no breadcrumbs
   (they record the scan address in fetch calls), no request context, no
   tracing, no Replay (it copies findings off the page). */
import * as Sentry from "@sentry/browser";

import { ALLOWED_INTEGRATIONS, createGate, isExpectedApiError, scrubEvent } from "./sentry-scrub";

const MAX_REPORTS_PER_PAGE_LOAD = 10;

let started = false;

export function start(): void {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (started || !dsn) return;
  started = true;

  const underCap = createGate(MAX_REPORTS_PER_PAGE_LOAD);
  Sentry.init({
    dsn,
    tunnel: "/monitoring",
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT || "production",
    release: process.env.NEXT_PUBLIC_RELEASE || undefined,
    // SDK 11 replaced sendDefaultPii with dataCollection, and its defaults
    // collect user info, cookies, headers, query strings and local variables.
    // Everything is switched off here; scrubEvent is the second line of defence.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      stackFrameVariables: false,
      frameContextLines: 0,
    },
    // The default appends the request's host to "Failed to fetch" messages.
    enhanceFetchErrorMessages: false,
    maxBreadcrumbs: 0,
    tracesSampleRate: 0,
    // Only errors thrown by our own scripts; browser extensions and injected
    // scripts are not ours to report.
    allowUrls: [window.location.origin],
    integrations: (defaults) => defaults.filter((i) => ALLOWED_INTEGRATIONS.has(i.name)),
    beforeBreadcrumb: () => null,
    beforeSend(event, hint) {
      if (isExpectedApiError(hint?.originalException)) return null;
      if (!underCap()) return null;
      return scrubEvent(event);
    },
  });
}

export function capture(error: unknown): void {
  start();
  Sentry.captureException(error);
}
