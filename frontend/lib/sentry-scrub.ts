/* What a browser error report may carry, and nothing else (docs/FRONTEND-SENTRY-DESIGN.md).

   A page in this app shows the addresses people scan, what the scans found,
   repository names and file contents. An error report that quoted any of that
   would be a data leak with a dashboard, and `sendDefaultPii: false` does not
   prevent it: exception messages, URLs and stack-frame text all reach Sentry.

   So this is a whitelist, the same shape as backend/observability.py: it
   builds a new event from the few fields that are safe and drops everything
   else, including fields a later SDK version adds. What survives is the
   error's type, where in Sentinels' own code it happened, which page route it
   was (ids replaced) and the software version.

   Pure functions with no imports, so `node --test` can run them directly. */

const MAX_TEXT = 300;

/** The only SDK integrations that run. An allowlist, so a default added in a
 *  later SDK version stays off. Names are the SDK's own (a test checks each one
 *  exists in the installed version, since a renamed integration would silently
 *  stop running: v11 calls the URL filter "EventFilters", not "InboundFilters"). */
export const ALLOWED_INTEGRATIONS = new Set([
  "EventFilters",
  "FunctionToString",
  "LinkedErrors",
  "Dedupe",
  "GlobalHandlers",
]);

const URL_RE = /[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^\s'"<>)\]}]+/g;
const PEM_RE = /-----BEGIN [A-Z ]+-----[\s\S]*?-----END [A-Z ]+-----/g;
const SECRET_RE =
  /(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|gsk_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*|(?:bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,})/gi;
const UUID_RE = /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g;
const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.-]+/g;
const IPV4_RE = /\b\d{1,3}(?:\.\d{1,3}){3}\b/g;
// Any run of hex digits and colons with at least two colons; confirmed below so
// a clock time such as 12:30:45 is left alone.
const IPV6_CANDIDATE_RE = /(?<![\w:])[0-9a-fA-F:.]*:[0-9a-fA-F:.]*:[0-9a-fA-F:.]*(?![\w:])/g;
// A bare hostname such as "victim-site.example": an error like "could not reach
// victim-site.example" carries the scanned site without any scheme. Names that
// are really files (main.js, package.json) stay readable.
const HOST_RE = /\b(?:[A-Za-z0-9-]+\.)+([A-Za-z]{2,})\b/g;
const FILE_SUFFIXES = new Set([
  "py", "pyc", "json", "toml", "txt", "md", "yml", "yaml", "db", "pem", "js", "mjs",
  "cjs", "ts", "tsx", "jsx", "css", "html", "lock", "cfg", "ini", "conf", "log", "sql",
  "env", "map",
]);

function looksLikeIpv6(candidate: string): boolean {
  if (!candidate.includes("::") && candidate.split(":").length < 8) return false;
  return /^[0-9a-fA-F:.]+$/.test(candidate) && !/:::/.test(candidate);
}

/** A string with addresses, secrets, ids and e-mail addresses replaced. */
export function scrubText(value: unknown): string {
  let text = String(value ?? "");
  text = text.replace(PEM_RE, "<secret>");
  text = text.replace(SECRET_RE, "<secret>");
  text = text.replace(URL_RE, "<url>");
  text = text.replace(EMAIL_RE, "<email>");
  text = text.replace(UUID_RE, "<id>");
  text = text.replace(IPV4_RE, "<ip>");
  text = text.replace(IPV6_CANDIDATE_RE, (m) => (looksLikeIpv6(m) ? "<ip>" : m));
  text = text.replace(HOST_RE, (m, suffix: string) =>
    FILE_SUFFIXES.has(suffix.toLowerCase()) ? m : "<host>",
  );
  return text.slice(0, MAX_TEXT);
}

/** The path of a URL with no host, query string, hash or ids. */
export function scrubPath(url: unknown): string | undefined {
  if (typeof url !== "string" || !url) return undefined;
  let path: string;
  try {
    path = new URL(url, "http://placeholder.invalid").pathname || "/";
  } catch {
    return undefined;
  }
  return path.replace(UUID_RE, ":id").slice(0, MAX_TEXT);
}

interface RawFrame {
  filename?: unknown;
  function?: unknown;
  lineno?: unknown;
  colno?: unknown;
  in_app?: unknown;
}
interface RawException {
  type?: unknown;
  value?: unknown;
  mechanism?: unknown;
  stacktrace?: { frames?: RawFrame[] };
}
interface RawEvent {
  exception?: { values?: RawException[] };
  message?: unknown;
  request?: { url?: unknown };
  transaction?: unknown;
  [key: string]: unknown;
}

function scrubFrames(stacktrace: { frames?: RawFrame[] } | undefined) {
  if (!stacktrace) return undefined;
  return {
    frames: (stacktrace.frames ?? []).map((frame) => ({
      // The file is one of our own bundle chunks; only its path is kept.
      filename: scrubPath(frame.filename),
      function: typeof frame.function === "string" ? frame.function.slice(0, 120) : undefined,
      lineno: frame.lineno,
      colno: frame.colno,
      in_app: frame.in_app,
    })),
  };
}

/** The report that is allowed to leave: only whitelisted fields. */
export function scrubEvent<T extends object>(input: T): T {
  const event = input as RawEvent;
  const clean: Record<string, unknown> = {};
  for (const key of ["event_id", "timestamp", "level", "platform", "release", "environment", "sdk"]) {
    if (key in event) clean[key] = event[key];
  }

  const values = event.exception?.values;
  if (Array.isArray(values)) {
    clean.exception = {
      values: values.map((entry) => ({
        type: entry.type,
        value: scrubText(entry.value ?? ""),
        stacktrace: scrubFrames(entry.stacktrace),
        mechanism: entry.mechanism,
      })),
    };
  }
  if (event.message) clean.message = scrubText(event.message);

  const route = scrubPath(event.request?.url ?? event.transaction);
  if (route) clean.request = { url: route };
  return clean as T;
}

/** Errors the product expects and shows to the person: a refused scan, a quota,
 *  a missing sign-in. They describe the person's input, not a bug. */
export function isExpectedApiError(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  return (
    error instanceof Error &&
    error.name === "ApiError" &&
    typeof status === "number" &&
    status >= 400 &&
    status < 500
  );
}

/** Lets `max` reports through per page load, so an error loop cannot flood the quota. */
export function createGate(max: number): () => boolean {
  let seen = 0;
  return () => {
    seen += 1;
    return seen <= max;
  };
}
