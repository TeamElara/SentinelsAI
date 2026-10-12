/* Where browser error reports are relayed to. The browser posts to this
   origin's /monitoring, and next.config.ts forwards it to the Sentry project in
   the DSN. That keeps the CSP `connect-src 'self'` unchanged, and ad-blockers
   that block ingest.sentry.io don't hide crashes. The destination is fixed to
   one project, so this is not an open relay.

   A DSN looks like https://<key>@<host>/<project-id>. Returns null for
   anything else, which turns the relay off. */
export function sentryEnvelopeUrl(dsn: string | undefined): string | null {
  if (!dsn) return null;
  try {
    const parsed = new URL(dsn);
    const projectId = parsed.pathname.replace(/^\/+|\/+$/g, "");
    if (parsed.protocol !== "https:" || !/^\d+$/.test(projectId)) return null;
    return `https://${parsed.host}/api/${projectId}/envelope/`;
  } catch {
    return null;
  }
}
