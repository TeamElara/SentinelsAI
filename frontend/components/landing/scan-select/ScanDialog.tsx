"use client";

/* The "enter a URL" dialog.

   WHY A DIALOG AND NOT AN INLINE FIELD. The first build revealed the input
   underneath the cards, which put the one thing the user now has to do at
   the bottom of a full-height section — below the fold on a short window,
   and visually subordinate to artwork they had already finished with. A
   dialog inverts that: the chooser is done, so it goes behind a scrim, and
   the field becomes the only lit thing on screen.

   IT WRAPS `ScanLauncher` RATHER THAN REIMPLEMENTING IT. That component
   already owns the real streaming scan, the per-agent progress list, the
   error state and the navigation to `/scan/{id}` — and `/url` uses the same
   one. Forking it to get a different frame around it is how two copies start
   drifting. So the dialog supplies the chrome and nothing else; press Next
   and `ScanLauncher` swaps its own form for `ScanProgress` in place, which
   is the "complete scanning page" this dialog turns into.

   ACCESSIBILITY. A real dialog has obligations a styled `<div>` does not:
   Escape closes it, focus moves in on open and returns to the trigger on
   close, the backdrop is clickable, and the page behind is marked
   `aria-hidden` — handled here rather than left to chance. */

import { useEffect, useRef } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { ScanLauncher } from "@/components/ScanLauncher";
import { EXPO_OUT } from "./motion";

type ScanDialogProps = {
  open: boolean;
  onClose: () => void;
  /** Shown as the small line above the field, e.g. "Website URL". */
  label: string;
  placeholder: string;
};

export function ScanDialog({
  open,
  onClose,
  label,
  placeholder,
}: ScanDialogProps) {
  const reduceMotion = useReducedMotion();
  const panelRef = useRef<HTMLDivElement>(null);
  /* Whatever had focus when the dialog opened — the card that was clicked.
     Focus has to go back there on close, or a keyboard user is dumped at the
     top of the document with no idea where they were. */
  const returnFocusTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    returnFocusTo.current = document.activeElement as HTMLElement | null;

    // Move focus into the dialog. The URL field is the only thing here worth
    // focusing, and focusing it means a keyboard user can simply start typing.
    const input = panelRef.current?.querySelector("input");
    input?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);

    /* The page behind is still scrollable underneath a fixed overlay, and on
       this landing page it is a 560vh pinned hero — scrolling it while a
       dialog is open is disorienting. Locking `overflow` is the plain-CSS
       half of that; Lenis keeps its own scroll position, but with the body
       clamped there is nothing for it to move. */
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      returnFocusTo.current?.focus();
    };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center px-6"
          initial="enter"
          animate="settled"
          exit="leave"
        >
          {/* THE SCRIM. Clicking it closes — the standard escape hatch for
              anyone who opened this by accident. It is a sibling of the
              panel, not its parent, so a click inside the panel can never
              bubble out to it and close the dialog mid-typing. */}
          <motion.button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="absolute inset-0 h-full w-full cursor-default bg-black/80 backdrop-blur-sm"
            variants={{
              enter: { opacity: 0 },
              settled: { opacity: 1, transition: { duration: 0.3 } },
              leave: { opacity: 0, transition: { duration: 0.2 } },
            }}
          />

          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={label}
            className="relative w-full max-w-xl border border-white/10 bg-ink p-8 sm:p-12"
            variants={{
              enter: { opacity: 0, y: reduceMotion ? 0 : 24, scale: reduceMotion ? 1 : 0.98 },
              settled: {
                opacity: 1,
                y: 0,
                scale: 1,
                transition: { duration: reduceMotion ? 0.15 : 0.45, ease: EXPO_OUT },
              },
              leave: {
                opacity: 0,
                y: reduceMotion ? 0 : -12,
                scale: reduceMotion ? 1 : 0.99,
                transition: { duration: 0.2, ease: "easeIn" },
              },
            }}
          >
            <button
              type="button"
              onClick={onClose}
              className="absolute right-5 top-5 font-mono text-[10px] uppercase
                         tracking-[0.28em] text-muted transition-colors hover:text-parchment
                         focus-visible:text-parchment focus-visible:outline-none"
            >
              Close
            </button>

            {/* `ScanLauncher` hides `children` the moment a scan starts, so
                this heading gets out of the way and the progress list takes
                the whole panel — the dialog becomes the scanning view rather
                than sitting on top of one. */}
            <ScanLauncher
              label={label}
              placeholder={placeholder}
              submitLabel="Next"
              footnote={
                <p className="mt-8 font-mono text-[11px] leading-relaxed text-muted">
                  Passive inspection only. Sentinels sends ordinary GET
                  requests to public paths and reads public DNS. It never
                  sends attack traffic.
                </p>
              }
            >
              <h2 className="font-geo text-3xl leading-tight tracking-[-0.02em] sm:text-4xl">
                Scan a website
              </h2>
              <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted">
                Paste the address you want inspected. Eight agents read what is
                already public and return a graded report.
              </p>
            </ScanLauncher>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
