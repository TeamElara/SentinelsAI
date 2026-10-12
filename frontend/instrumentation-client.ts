/* Runs in the browser before the app starts (Next's instrumentation-client
   hook). It switches on the global error handlers, so an uncaught exception or
   rejected promise is reported. With no DSN the branch is removed at build time
   and none of the SDK is shipped. */
if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
  import("./lib/sentry-client").then((m) => m.start()).catch(() => {});
}
