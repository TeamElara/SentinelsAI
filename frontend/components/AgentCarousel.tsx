"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { AgentInfo, AgentResult } from "@/lib/api";
import { isProblem } from "@/lib/findings";
import { AgentPeekDialog } from "@/components/AgentPeekDialog";

/* ---------------------------------------------------------------------------
   Replaces the scroll-driven AgentReel with a circular carousel: a shallow
   arc of small cards, driven by arrows/dots/keyboard/drag instead of scroll.
   AgentReel.tsx is left on disk, unused — reverting this experiment is then
   a two-line change in the page that renders this, not a file resurrection.

   GEOMETRY. Each card sits at an angle along a flattened arc (an ellipse,
   not a circle — RADIUS_Y_RATIO squashes the vertical reach so the shape
   reads as a shelf, not a wheel). The angle comes from `d`, the card's
   *signed* distance from the active card:

     d = shortest-arc(index - activeIndex, total)
     angle = (d / visibleCount) * π
     x = sin(angle) * radiusX      y = -cos(angle) * radiusY

   The "shortest signed distance" part matters at 8 items. A naive
   `index - activeIndex` treats the ring as a line, so with activeIndex = 0
   and total = 8, index 7 computes as offset -7 (all the way around one
   side) instead of offset -1 (one step the other way) — every card ends up
   crowded onto one side of centre instead of split evenly. `d` folds any
   distance greater than half the ring back onto the short way round, the
   same trick a clock face uses to say "11 o'clock is one hour before 12,
   not eleven hours after it". */

/* How many cards ride the arc at once. Five is the desktop look, but a phone
   cannot spread five of them: `MIN_CARD_WIDTH` stops the card shrinking past
   170px, so the ring answers by pushing the outer pair off the edge instead —
   37px past the viewport at 320px wide. Narrow tracks show three cards, which
   fits with room to spare and keeps every card readable rather than shaving
   them all down to slivers. */
const VISIBLE_WIDE = 5;
const VISIBLE_NARROW = 3;
const NARROW_TRACK = 640; // track width below which the ring drops to three

// The ring's reach is a fraction of the track's own measured width rather
// than a fixed pixel count, via ResizeObserver below — so a phone-width
// track gets a phone-width ring, and a widescreen monitor gets a wide one,
// instead of the ring capping out small while empty margin grows around it.
const MIN_RADIUS_X = 110;
const MAX_RADIUS_X = 520;
const RADIUS_X_RATIO = 0.36;
const RADIUS_Y_RATIO = 0.45; // a shallow arc, not a full circle

// Card size is likewise derived from the measured radius rather than fixed
// per breakpoint, so it grows continuously with the ring instead of jumping
// at sm/lg — a 2000px monitor and a 1300px laptop both get a card sized to
// what their own ring actually has room for.
const MIN_CARD_WIDTH = 170;
// Capped rather than left to `radiusX * CARD_WIDTH_RATIO`: past roughly this
// width the outermost pair starts running out of track to sit in, and the
// active card overlaps its neighbours far enough to bury their titles.
const MAX_CARD_WIDTH = 370;
const CARD_WIDTH_RATIO = 0.95; // vs. radiusX
const CARD_ASPECT = 0.66; // height / width
const TRACK_PADDING = 40; // headroom above/below the arc so cards never clip
const EDGE_MARGIN = 8; // gap kept between the outermost card and the track edge

// `trackWidth` is 0 until the ResizeObserver's first callback. Falling back to
// a desktop-ish width means the very first paint is a full ring rather than a
// collapsed one that then springs open.
const FALLBACK_TRACK_WIDTH = 960;

/* Everything about the ring that follows from how many cards are on it.
   `edgeAngle`/`edgeScale` describe the OUTERMOST visible card and are
   re-derived from the same expressions `ringPosition` uses below, so the
   measurements the layout depends on can never drift from where the cards
   are actually drawn. */
function ringSpec(visibleCount: number) {
  const half = Math.floor(visibleCount / 2);
  return {
    visibleCount,
    half,
    edgeAngle: (half / visibleCount) * Math.PI,
    edgeScale: 1 - (half / (half + 1)) * 0.3,
  };
}

type RingSpec = ReturnType<typeof ringSpec>;

interface RingGeometry {
  x: number;
  y: number;
  scale: number;
  opacity: number;
  zIndex: number;
}

function ringPosition(
  index: number,
  activeIndex: number,
  total: number,
  radiusX: number,
  radiusY: number,
  spec: RingSpec,
): RingGeometry | null {
  if (total === 0) return null;

  let d = (((index - activeIndex) % total) + total) % total; // 0 … total-1
  if (d > total / 2) d -= total; // fold onto the short way round
  if (Math.abs(d) > spec.half) return null; // outside the visible span

  const angle = (d / spec.visibleCount) * Math.PI;
  const x = Math.sin(angle) * radiusX;
  const y = -Math.cos(angle) * radiusY;

  const distance = Math.abs(d);
  const maxDistance = spec.half + 1;
  const scale = Math.max(0, 1 - (distance / maxDistance) * 0.3);
  const opacity = Math.max(0.3, 1 - (distance / maxDistance) * 0.7);
  const zIndex = spec.visibleCount - distance;

  return { x, y, scale, opacity, zIndex };
}

// Lifted verbatim from AgentReel.tsx — same rule, same wording.
export function statusLabel(result: AgentResult): string {
  if (result.error) return "Failed";
  const problems = result.findings.filter(isProblem).length;
  if (problems === 0) return result.coverage_status === "completed" ? "No issues observed" : "Incomplete";
  return `${problems} issue${problems === 1 ? "" : "s"}`;
}

// Inline rather than lucide-react — that package isn't installed, and two
// static paths aren't worth adding a dependency for.
function Chevron({ direction }: { direction: "left" | "right" }) {
  const d = direction === "left" ? "M15 18l-6-6 6-6" : "M9 18l6-6-6-6";
  return (
    <svg viewBox="0 0 24 24" fill="none" className="size-5" aria-hidden>
      <path
        d={d}
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function AgentCarousel({
  agents,
  info,
  scanId,
}: {
  agents: AgentResult[];
  info: AgentInfo[];
  scanId: string;
}) {
  // `report.agents` arrives in completion order, which varies run to run
  // because the agents race each other — re-sort into registry order so the
  // ring is stable regardless of finish order.
  const ordered = useMemo(
    () =>
      [...agents].sort(
        (a, b) =>
          info.findIndex((i) => i.name === a.agent) -
          info.findIndex((i) => i.name === b.agent),
      ),
    [agents, info],
  );

  const total = ordered.length;
  const [activeIndex, setActiveIndex] = useState(0);
  const [peekAgent, setPeekAgent] = useState<AgentResult | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  // The one measured input. Card size, radius, card count and track height are
  // all derived from it during render rather than kept as their own state, so
  // there is a single source of truth and no chance of two of them disagreeing.
  const [trackWidth, setTrackWidth] = useState(0);
  const reduceMotion = useReducedMotion() ?? false;

  useEffect(() => {
    const el = trackRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      setTrackWidth(entries[0]?.contentRect.width ?? el.clientWidth);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const width = trackWidth || FALLBACK_TRACK_WIDTH;
  const spec = ringSpec(width < NARROW_TRACK ? VISIBLE_NARROW : VISIBLE_WIDE);

  const desiredRadiusX = Math.min(
    MAX_RADIUS_X,
    Math.max(MIN_RADIUS_X, width * RADIUS_X_RATIO),
  );

  // Card size and the track's own height both fall out of the same radius
  // reading, so the arc and the cards riding it scale as one thing rather
  // than the ring changing size independently of what sits on it.
  const cardWidth = Math.min(
    MAX_CARD_WIDTH,
    Math.max(MIN_CARD_WIDTH, desiredRadiusX * CARD_WIDTH_RATIO),
    // …and never wider than the track itself, however narrow that gets.
    Math.max(0, width - 2 * EDGE_MARGIN),
  );
  const cardHeight = cardWidth * CARD_ASPECT;

  /* The largest radius that still keeps the outermost card inside the track.
     This is the horizontal counterpart to the arc measurement below, and it is
     what stops the ring overflowing on a phone: once `MIN_CARD_WIDTH` refuses
     to shrink the card any further, the only remaining way to fit is to pull
     the ring in, so that is what happens here. */
  const fitRadiusX =
    (width / 2 - EDGE_MARGIN - (cardWidth * spec.edgeScale) / 2) /
    Math.sin(spec.edgeAngle);
  const radiusX = Math.max(0, Math.min(desiredRadiusX, fitRadiusX));
  const radiusY = radiusX * RADIUS_Y_RATIO;

  /* The arc's true top and bottom edges, measured from the track's centre.
     The top is the active card (angle 0, full scale); the bottom is the
     outermost visible pair, which sits much higher than +radiusY and is
     scaled down as well. Sizing the track to `radiusY * 2` instead — as this
     did originally — left a dead band roughly 275px tall under the lowest
     card, which is what pushed the controls so far from the ring. */
  const arcTop = -radiusY - cardHeight / 2;
  const arcBottom =
    -Math.cos(spec.edgeAngle) * radiusY + (cardHeight * spec.edgeScale) / 2;
  const trackHeight = arcBottom - arcTop + TRACK_PADDING;

  /* CSS centres each card on the track (`top-1/2 -translate-y-1/2`), but the
     arc is not centred on itself — it hangs above the origin. Without this
     the whole ring would hug the track's top edge and re-open the same gap
     underneath that shrinking the track just closed. */
  const arcYOffset = -(arcTop + arcBottom) / 2;

  // A re-scan could in principle change how many agents come back. Rather
  // than an effect that notices `activeIndex` is out of range and issues a
  // second setState to correct it — a cascading render — the same modulo
  // `goTo` already uses folds it back into range on every read, so a stale
  // index just wraps instead of pointing past the end of the array.
  const safeIndex = total > 0 ? ((activeIndex % total) + total) % total : 0;

  const goTo = useCallback(
    (index: number) => {
      if (total === 0) return;
      setActiveIndex(((index % total) + total) % total);
    },
    [total],
  );
  const next = useCallback(() => goTo(safeIndex + 1), [safeIndex, goTo]);
  const prev = useCallback(() => goTo(safeIndex - 1), [safeIndex, goTo]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "ArrowLeft") prev();
      if (event.key === "ArrowRight") next();
    }
    const el = rootRef.current;
    el?.addEventListener("keydown", onKeyDown);
    return () => el?.removeEventListener("keydown", onKeyDown);
  }, [next, prev]);

  if (total === 0) return null;

  const metaFor = (agentName: string) => info.find((a) => a.name === agentName);
  const activeAgent = ordered[safeIndex];

  return (
    <section
      ref={rootRef}
      tabIndex={0}
      role="region"
      aria-label="Agents"
      // max-w-7xl + w-full matches the article container above it exactly
      // (page.tsx:179) — the carousel used to be capped at max-w-3xl, far
      // narrower than the rest of the report. `w-full` is load-bearing, not
      // decorative: this section is a flex item inside the scan layout's
      // `flex flex-col` wrapper, and `mx-auto`'s auto side-margins override
      // flexbox's default stretch behaviour, collapsing the section to its
      // shrink-to-fit content width unless something forces it to 100% first.
      className="mx-auto mt-20 flex w-full max-w-7xl flex-col items-center gap-8 px-6 pb-24 outline-none sm:px-8"
    >
      <h2 className="self-start font-mono text-[40px] uppercase tracking-[0.3em] text-muted">
        Agents
      </h2>

      {/* Track and controls are grouped in their own tighter flex column so
          the slide buttons sit close under the cards — only the heading
          keeps the section's wider `gap-8` above this group. */}
      <div className="flex w-full flex-col items-center gap-3">
        <motion.div
          ref={trackRef}
          drag="x"
          dragConstraints={{ left: 0, right: 0 }}
          dragElastic={reduceMotion ? 0 : 0.18}
          dragMomentum={false}
          onDragEnd={(_, dragInfo) => {
            if (dragInfo.offset.x < -50) next();
            else if (dragInfo.offset.x > 50) prev();
          }}
          // Height is computed, not a fixed/breakpoint class — it has to grow
          // in lockstep with `radiusY` and `cardHeight` above, or the arc
          // widening on a big screen would just clip the top/bottom rows.
          style={{ height: trackHeight }}
          className="relative w-full cursor-grab touch-pan-y active:cursor-grabbing"
        >
          <AnimatePresence mode="popLayout">
            {ordered.map((result, index) => {
              const pos = ringPosition(
                index,
                safeIndex,
                total,
                radiusX,
                radiusY,
                spec,
              );
              if (!pos) return null;

              const isActive = index === safeIndex;
              const agentMeta = metaFor(result.agent);

              return (
                <motion.button
                  key={result.agent}
                  type="button"
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{
                    x: pos.x,
                    y: pos.y + arcYOffset,
                    scale: pos.scale,
                    opacity: pos.opacity,
                    zIndex: pos.zIndex,
                  }}
                  exit={{ opacity: 0, scale: 0.8 }}
                  transition={
                    reduceMotion
                      ? { duration: 0.01 }
                      : { duration: 0.65, ease: [0.22, 1, 0.36, 1] }
                  }
                  onClick={() => {
                    setActiveIndex(index);
                    setPeekAgent(result);
                  }}
                  aria-label={`${agentMeta?.display_name ?? result.agent} — ${statusLabel(result)}`}
                  aria-current={isActive ? "true" : undefined}
                  className={`glass absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-start justify-between rounded-2xl p-4 text-left transition-shadow duration-300 ${
                    isActive
                      ? "border-parchment/25 shadow-[0_20px_60px_-12px_rgba(0,0,0,0.55)]"
                      : "shadow-[0_8px_24px_-4px_rgba(0,0,0,0.35)] hover:border-parchment/15"
                  }`}
                  // Width/height ride the same computed size as the track
                  // (see `cardWidth`/`cardHeight` above) instead of a fixed
                  // breakpoint class, so cards scale continuously with the ring.
                  style={{
                    width: cardWidth,
                    height: cardHeight,
                    transformOrigin: "center center",
                  }}
                >
                  <span
                    className={`font-mono text-[11px] uppercase tracking-[0.2em] ${
                      result.error ? "text-critical" : "text-muted"
                    }`}
                  >
                    {statusLabel(result)}
                  </span>
                  <div>
                    <h3
                      className={`font-display leading-tight ${
                        isActive ? "text-lg text-parchment" : "text-[15px] text-parchment/80"
                      }`}
                    >
                      {agentMeta?.display_name ?? result.agent}
                    </h3>
                    <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.15em] text-muted">
                      {agentMeta?.category ?? result.agent}
                    </p>
                  </div>
                </motion.button>
              );
            })}
          </AnimatePresence>
        </motion.div>

        {/* The "03 of 08" counter used to float inside the ring; it now sits
            directly above the slide buttons instead, so the numbering and the
            controls that change it read as one group rather than two things
            separated by the whole height of the track. */}
        <div className="flex flex-col items-center gap-2">
          <motion.p
            key={activeAgent.agent}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: reduceMotion ? 0.01 : 0.3, ease: "easeOut" }}
            className="font-mono text-xs text-muted"
          >
            <span className="text-parchment/90">{String(safeIndex + 1).padStart(2, "0")}</span>
            {" "}of {String(total).padStart(2, "0")}
          </motion.p>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={prev}
              aria-label="Previous agent"
              className="glass flex h-10 w-10 items-center justify-center rounded-full text-muted transition-colors hover:text-parchment"
            >
              <Chevron direction="left" />
            </button>

            <div className="flex items-center gap-1.5">
              {ordered.map((result, i) => (
                <button
                  key={result.agent}
                  type="button"
                  onClick={() => goTo(i)}
                  aria-label={`Go to ${metaFor(result.agent)?.display_name ?? result.agent}`}
                  aria-current={i === safeIndex ? "true" : undefined}
                  className={`h-1.5 rounded-full transition-all duration-300 ${
                    i === safeIndex
                      ? "w-6 bg-parchment/80"
                      : "w-1.5 bg-parchment/20 hover:bg-parchment/40"
                  }`}
                />
              ))}
            </div>

            <button
              type="button"
              onClick={next}
              aria-label="Next agent"
              className="glass flex h-10 w-10 items-center justify-center rounded-full text-muted transition-colors hover:text-parchment"
            >
              <Chevron direction="right" />
            </button>
          </div>
        </div>
      </div>

      <AgentPeekDialog
        result={peekAgent}
        info={peekAgent ? metaFor(peekAgent.agent) : undefined}
        scanId={scanId}
        onClose={() => setPeekAgent(null)}
      />
    </section>
  );
}

export default AgentCarousel;
