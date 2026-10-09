"use client";

import { useEffect } from "react";

import { ISSUES_URL } from "@/lib/links";

/* The last line of defence: it replaces the root layout when the layout itself
   throws, so it brings its own <html> and <body>, and none of the site's CSS or
   fonts are available. Hence plain inline styles and a system font. */
export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "1.25rem",
          padding: "1.5rem",
          textAlign: "center",
          background: "#0a0a0a",
          color: "#e9e4d8",
          fontFamily: "system-ui, sans-serif",
        }}
      >
        <title>Sentinels — something went wrong</title>
        <h1 style={{ margin: 0, fontWeight: 400 }}>That didn&apos;t work</h1>
        <p style={{ margin: 0, maxWidth: "24rem", lineHeight: 1.6, color: "#9a968c" }}>
          Sentinels hit an unexpected problem and couldn&apos;t load. Your scans are safe.
          Try again, and if it keeps happening, report it
          {error.digest ? ` and quote the reference ${error.digest}` : ""}.
        </p>
        <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap", justifyContent: "center" }}>
          <button
            type="button"
            onClick={() => retry()}
            style={{
              background: "transparent",
              color: "inherit",
              border: "1px solid #5a574f",
              padding: "0.6rem 1.2rem",
              cursor: "pointer",
              font: "inherit",
            }}
          >
            Try again
          </button>
          <a href={ISSUES_URL} style={{ color: "#9a968c" }}>
            Report a problem
          </a>
        </div>
      </body>
    </html>
  );
}
