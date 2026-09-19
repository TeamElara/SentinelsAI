"use client";

/* The scan page's closing section — structurally lifted from
   roshan-sahu.com's footer (the reference the user asked to match), with its
   content swapped for Sentinels' own.

   The five index entries are real buttons that open an explanatory panel
   rather than links to pages that don't exist. "New scan" is the exception:
   it goes somewhere real, so it stays a link.

   The contact and social entries are still inert placeholder text on purpose
   — the brief was to keep the reference's layout without wiring anything up. */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

interface Panel {
  label: string;
  lead: string;
  sections: { heading: string; items: string[] }[];
}

/* One entry per footer index item. Written to be true to what the tool
   actually does — in particular the passive-only scope, which is a hard
   constraint on this project and not a marketing line. */
const PANELS: Record<string, Panel> = {
  overview: {
    label: "Overview",
    lead: "The report's front page — everything the scan concluded, in one screen.",
    sections: [
      {
        heading: "What it shows",
        items: [
          "Score and grade — 0–100 and A–F. Computed from the findings alone, so the same site scores the same every time.",
          "Severity counts — Critical, High, Medium and Low, tallied across every agent that ran.",
          "Main issue — the single worst problem found, linked straight through to the agent that found it.",
          "Deployment badge — a readiness score and a Ready / Caution / Blocked verdict, carried over from the checklist.",
          "Assessment — a plain-language summary of what those numbers actually mean for the site.",
          "Agent reel — the five agents that ran, each opening its own detail page.",
        ],
      },
    ],
  },

  checklist: {
    label: "Checklist",
    lead: "A deployment-readiness review, sorted by how much Sentinels can actually prove.",
    sections: [
      {
        heading: "Three tiers",
        items: [
          "Auto-verified — confirmed directly from scan data. These are facts, not guesses.",
          "Passively inferred — a real signal, but not conclusive. Useful hints, not hard evidence.",
          "Self-attested — things no passive scan can test. You answer these yourself, and Sentinels records the answer without ever checking it.",
        ],
      },
      {
        heading: "What it produces",
        items: [
          "Readiness score — the share of auto-verified checks that passed.",
          "Deployment status — Blocked if a critical check failed, Caution if anything is warning, otherwise Ready.",
        ],
      },
    ],
  },

  agents: {
    label: "Agents",
    lead: "Five specialists run at the same time against the target. Each owns one domain and reports on its own.",
    sections: [
      {
        heading: "Website scans",
        items: [
          "Security Headers — checks the response headers a browser relies on to constrain a page.",
          "Reconnaissance — what the site leaks about itself that helps an attacker find known weaknesses.",
          "TLS / Certificate — performs a real handshake and inspects the certificate and protocol version.",
          "Sensitive File Exposure — requests well-known paths that, if publicly readable, expose credentials or source history.",
          "DNS / Email Security — reads DNS records and the SPF, DKIM and DMARC configuration behind them.",
        ],
      },
      {
        heading: "Repository scans",
        items: [
          "Repo Hygiene, Secrets, Dependencies, Repo Config and Code Patterns — the same idea applied to a public GitHub repository instead of a live site.",
        ],
      },
      {
        heading: "How they behave",
        items: [
          "They run concurrently rather than in sequence, which is what keeps a full scan under a minute.",
          "Every agent catches its own errors. One agent failing never takes the scan down — it reports the failure and the other four carry on.",
        ],
      },
    ],
  },

  docs: {
    label: "Docs",
    lead: "How the tool reaches its conclusions, and where the limits are.",
    sections: [
      {
        heading: "Scoring",
        items: [
          "Deterministic, with no model in the loop. Findings carry fixed weights by severity, and the score is their arithmetic — rescanning an unchanged site cannot produce a different number.",
        ],
      },
      {
        heading: "What a scan actually does",
        items: [
          "Reads response headers, inspects TLS configuration, resolves DNS records, and issues ordinary GET requests to public paths. Nothing more.",
        ],
      },
      {
        heading: "The AI layer",
        items: [
          "Only enriches. It writes the plain-language assessment and the fix suggestions — it never decides the score.",
          "Entirely optional: if the API key is missing or the call fails, the scan still produces a complete report.",
        ],
      },
      {
        heading: "Export",
        items: ["The full report downloads as a PDF from the overview page."],
      },
    ],
  },

  about: {
    label: "About",
    lead: "Sentinels is a passive security auditor. A URL or a repository goes in, five agents scan it concurrently, and a graded report comes out in under a minute.",
    sections: [
      {
        heading: "Passive only — this one is not negotiable",
        items: [
          "Reads response headers, TLS configuration, DNS records, and public paths.",
          "Never sends attack traffic: no SQL injection, no brute force, no fuzzing, no denial of service, no automated form submission.",
          "If a feature would require sending something harmful, it is out of scope by definition.",
        ],
      },
      {
        heading: "What it's for",
        items: [
          "A defensive and educational tool. It tells you what a stranger can already learn about your site without touching it, and what to fix first.",
          "Only ever scan something you own or have permission to test.",
        ],
      },
    ],
  },
};

const PANEL_ORDER = ["overview", "checklist", "agents", "docs", "about"];

function InfoModal({ panel, onClose }: { panel: Panel; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Escape closes, which is the one keyboard behaviour people genuinely
    // expect from a dialog and notice the absence of.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);

    // Stop the page behind the overlay scrolling under it.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Move focus into the dialog so the next Tab lands inside it rather than
    // continuing through the footer underneath.
    dialogRef.current?.focus();

    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-end justify-center bg-ink/80 p-4 backdrop-blur-sm sm:items-center sm:p-6"
      // A click that both starts and ends on the backdrop closes. Using the
      // target check rather than a separate backdrop element means a drag
      // that happens to end out here doesn't dismiss the panel.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="footer-panel-title"
        tabIndex={-1}
        className="materialize-in glass max-h-[85vh] w-full max-w-2xl overflow-y-auto px-6 py-7 outline-none sm:px-10 sm:py-9"
      >
        <div className="flex items-start justify-between gap-6">
          <h2
            id="footer-panel-title"
            className="font-geo text-3xl font-light uppercase leading-none tracking-[0.005em] sm:text-4xl"
          >
            {panel.label}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 font-mono text-xs uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
          >
            [Close]
          </button>
        </div>

        <p className="mt-5 leading-relaxed text-parchment/85 sm:text-lg">
          {panel.lead}
        </p>

        {panel.sections.map((section) => (
          <section key={section.heading} className="mt-8">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.3em] text-muted">
              {section.heading}
            </h3>
            <ul className="mt-4 space-y-3">
              {section.items.map((item, i) => (
                <li
                  key={i}
                  className="flex gap-3 text-sm leading-relaxed text-muted sm:text-base"
                >
                  <span className="mt-1.5 shrink-0 font-mono text-[10px] text-rule">
                    —
                  </span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

function Clock() {
  // Reference site runs a live IST clock in this slot. Same idea, kept in the
  // visitor's own timezone since this build has no reason to assume where
  // they are.
  const [time, setTime] = useState<string | null>(null);

  useEffect(() => {
    const tick = () =>
      setTime(
        new Date().toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }),
      );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  // Rendered as a placeholder on the server and filled in after mount, so the
  // clock can't cause a hydration mismatch against whatever second the server
  // happened to render on.
  return (
    <span className="font-mono text-xs text-muted lg:text-sm">
      {time ?? "--:--:--"} LOCAL
    </span>
  );
}

export function Footer() {
  const [openPanel, setOpenPanel] = useState<string | null>(null);
  const close = useCallback(() => setOpenPanel(null), []);

  return (
    <>
      <footer className="mt-32 border-t border-rule bg-ink">
        <div className="mx-auto w-full max-w-7xl px-6 pt-16 pb-10 sm:px-8 lg:pt-24">
          {/* The reference's "Let's work together [Contact]" — pointed back
              into the product rather than out to a contact form, since this
              tool doesn't have one. */}
          <Link
            href="/"
            className="group flex flex-wrap items-baseline gap-x-6 gap-y-3 border-b border-rule pb-14 lg:pb-20"
          >
            <h2 className="font-geo font-light uppercase leading-[0.95] tracking-[0.005em] text-[clamp(2.2rem,6vw,5.5rem)]">
              Scan another site
            </h2>
            <span className="font-mono text-xs uppercase tracking-[0.2em] text-muted transition-colors group-hover:text-parchment lg:text-sm">
              [New scan]
            </span>
          </Link>

          {/* Reference's six-column grid, at the same proportions: index in
              column 1, contact block in column 4, socials in column 6 —
              columns 2–3 and 5 are the reference's own deliberate empty
              space, not a gap this layout forgot to fill. */}
          <div className="grid grid-cols-1 gap-y-12 pt-14 lg:grid-cols-6 lg:gap-y-0 lg:pt-20">
            <nav
              aria-label="About this report"
              className="flex flex-col items-start gap-3 lg:col-start-1"
            >
              {PANEL_ORDER.map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setOpenPanel(key)}
                  className="font-geo text-2xl font-light uppercase leading-tight text-muted transition-colors hover:text-parchment lg:text-3xl"
                >
                  {PANELS[key].label}
                </button>
              ))}
            </nav>

            <div className="flex flex-col gap-6 lg:col-start-4">
              <div className="flex flex-col gap-2">
                <span className="font-mono text-xs uppercase tracking-[0.15em] text-parchment lg:text-sm">
                  hello@sentinels.dev
                </span>
                <span className="flex items-center gap-2 font-mono text-xs uppercase tracking-[0.15em] text-parchment lg:text-sm">
                  Quick chat
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    className="h-3.5 w-3.5 text-muted"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"
                    />
                  </svg>
                </span>
                <Clock />
              </div>

              <button
                type="button"
                onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
                className="w-fit font-mono text-xs uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
              >
                ↑ Back to top
              </button>
            </div>

            <div className="flex gap-8 lg:col-start-6 lg:justify-self-end">
              <span className="font-mono text-xs uppercase tracking-[0.15em] text-muted lg:text-sm">
                LinkedIn
              </span>
              <span className="font-mono text-xs uppercase tracking-[0.15em] text-muted lg:text-sm">
                Instagram
              </span>
            </div>
          </div>

          <p className="mt-16 font-mono text-[10px] uppercase tracking-[0.2em] text-muted lg:mt-24">
            Sentinels — passive website security inspection
          </p>
        </div>
      </footer>

      {openPanel && <InfoModal panel={PANELS[openPanel]} onClose={close} />}
    </>
  );
}
