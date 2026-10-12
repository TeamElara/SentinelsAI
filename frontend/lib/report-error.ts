/* Send an error caught by a render boundary (app/error.tsx, global-error.tsx)
   to error reporting. Does nothing unless NEXT_PUBLIC_SENTRY_DSN was set when
   the site was built, and never throws: reporting a crash must not cause one. */
export function reportError(error: unknown): void {
  if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;
  import("./sentry-client").then((m) => m.capture(error)).catch(() => {});
}
