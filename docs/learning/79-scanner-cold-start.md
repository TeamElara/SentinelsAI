# C9 — scanner cold start

ScanLauncher calls the shared health preflight exposed by lib/api.ts before
opening either URL or repository streams. Each probe has a three-second
deadline. A failed first probe shows “Waking up the scanner…” in a live status
region; probes then start at five-second intervals. Ready health removes the
message and starts the original scan once.

Closing the dialog or leaving the launcher aborts health waits and the browser
stream. Waiting stops after two minutes with a useful retry message. It does
not create scans or charge daily allowance while the backend wakes up.
Backend stream reconnects retain their original request ID from C7.

Four deterministic clock tests cover immediate health, the three-second
deadline and five-second cadence, cancellation, and bounded failure. The four
stream transport tests also pass with the health preflight enabled. These
tests simulate a sleeping backend; a real Render sleep/wake check remains a
deployment gate because no Render access is configured in this workspace.
