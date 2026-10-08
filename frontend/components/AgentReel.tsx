"use client";

import { useEffect, useRef } from "react";
import Image from "next/image";
import Link from "next/link";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import type { AgentInfo, AgentResult } from "@/lib/api";
import { isProblem } from "@/lib/findings";

gsap.registerPlugin(ScrollTrigger);

/* Mobile browsers fire a resize every time the URL bar hides or shows.
   Without this, each of those recalculates every trigger's start/end mid-
   scroll, which is a visible hitch on exactly the gesture that causes it. */
ScrollTrigger.config({ ignoreMobileResize: true });

/* ---------------------------------------------------------------------------
   Geometry.

   The plate is a window with its LEFT EDGE PINNED. Only its width changes, so
   it opens rightwards and closes back leftwards — it never grows from its
   centre. The artwork inside is a fixed size and never resizes; it is simply
   too wide for the plate at rest, so the plate crops its right edge, and it
   slides right as the plate opens.

   Two consequences fall out of that and are the whole point:

     · the artwork un-crops as the plate opens, which is what "pops out"
     · the text sits after the plate in normal flow, so widening the plate
       pushes the text right and narrowing it pulls the text back left.
       The text has no animation of its own at all.

   `PLATE_VW` below is duplicated in the plate's own Tailwind class further
   down (`lg:w-[36vw]`) and the two have to agree: GSAP animates away from
   the width CSS gives the plate, so if they drift the plate visibly jumps
   on the first scroll. The plate's height (`lg:h-[34vw]`) and the artwork's
   width (`lg:w-[38vw]`) are plain CSS with nothing to keep in sync — neither
   is read by any function here.
   ------------------------------------------------------------------------- */
const PLATE_VW = 36; // plate's resting width, matches lg:w-[36vw]
const PEAK_RATIO = 1.28; // how far it opens — the reference measures 1.25
const ART_TRAVEL_VW = 4; // how far the artwork slides as the plate opens

/* How far the background texture zooms at the peak. Anchored to the plate's
   left edge (`origin-left` below), so the left edge of the texture stays
   welded in place and everything else pushes out to the right — the zoom and
   the plate's opening are the same gesture, not two competing ones. */
const TEXTURE_ZOOM = 1.2;

const plateRestWidth = () => (window.innerWidth * PLATE_VW) / 100;
const platePeakWidth = () => plateRestWidth() * PEAK_RATIO;
const artTravel = () => (window.innerWidth * ART_TRAVEL_VW) / 100;

/* One plate per agent, keyed by the agent's `name` from the backend registry
   rather than by position, so a given agent keeps its look no matter what
   order the results arrive in.

   `color` is not decoration any more — it is what shows while the texture is
   still downloading, so each one is that texture's own mean colour rather than
   a hand-picked shade. The plate therefore never flashes a colour the texture
   is about to replace with something different.

   Texture is matched to what the agent does rather than cycled for variety:
   green reads as the "valid certificate" colour, red as exposure, blue as
   reconnaissance. */
const PANELS: Record<
  string,
  { color: string; image: string; texture: string }
> = {
  headers: {
    color: "#121317",
    image: "/agents/headers.webp",
    texture: "/agents/tex-black.webp",
  },
  recon: {
    color: "#061020",
    image: "/agents/recon.webp",
    texture: "/agents/tex-navy.webp",
  },
  tls: {
    color: "#0C140D",
    image: "/agents/tls.webp",
    texture: "/agents/tex-green.webp",
  },
  exposure: {
    color: "#1E0607",
    image: "/agents/exposure.webp",
    texture: "/agents/tex-maroon.webp",
  },
  dns: {
    color: "#231308",
    image: "/agents/dns.webp",
    texture: "/agents/tex-brown.webp",
  },

  /* Repo scans run a different agent registry (backend/agents/repo_registry.py)
     with its own five agents. Same treatment, same texture set — a repo scan
     and a URL scan are the same screen, so neither should look like the poorer
     relation of the other. The two registries never appear together, so
     reusing a texture across both is invisible in practice. */
  "repo-hygiene": {
    color: "#0C140D",
    image: "/agents/repo-hygiene.webp",
    texture: "/agents/tex-green.webp",
  },
  "repo-secrets": {
    color: "#1E0607",
    image: "/agents/repo-secrets.webp",
    texture: "/agents/tex-maroon.webp",
  },
  "repo-dependencies": {
    color: "#061020",
    image: "/agents/repo-dependencies.webp",
    texture: "/agents/tex-navy.webp",
  },
  "repo-config": {
    color: "#231308",
    image: "/agents/repo-config.webp",
    texture: "/agents/tex-brown.webp",
  },
  "repo-patterns": {
    color: "#121317",
    image: "/agents/repo-patterns.webp",
    texture: "/agents/tex-black.webp",
  },
};

/* Only reached by an agent with no PANELS entry — a newly registered one that
   hasn't been given artwork yet. Colour only, so the reel degrades to plain
   plates rather than breaking. */
const FALLBACK_COLORS = ["#121317", "#061020", "#0C140D", "#1E0607", "#231308"];

function statusLabel(result: AgentResult): string {
  if (result.error) return "Failed";
  const problems = result.findings.filter(isProblem).length;
  if (problems === 0) return result.coverage_status === "completed" ? "No issues observed" : "Incomplete";
  return `${problems} issue${problems === 1 ? "" : "s"}`;
}

export function AgentReel({
  agents,
  info,
  scanId,
}: {
  agents: AgentResult[];
  info: AgentInfo[];
  scanId: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);

  /* `report.agents` arrives in completion order, which varies run to run
     because the agents race each other. Re-sorting into registry order is
     what keeps the reel in a stable sequence every time. */
  const ordered = [...agents].sort(
    (a, b) =>
      info.findIndex((i) => i.name === a.agent) -
      info.findIndex((i) => i.name === b.agent),
  );

  useEffect(() => {
    const ctx = gsap.context(() => {
      const mm = gsap.matchMedia();

      /* Below `lg` the row stacks and the plate is already full width, so
         there is nothing to open — and under reduced motion nothing should
         move at all. In both cases no tween is registered, and every element
         keeps the resting state its CSS already gives it. */
      mm.add(
        "(min-width: 1024px) and (prefers-reduced-motion: no-preference)",
        () => {
          const entries = gsap.utils.toArray<HTMLElement>("[data-reel-entry]");

          entries.forEach((entry) => {
            const q = gsap.utils.selector(entry);

            /* `top bottom` to `bottom top` is the row's entire journey across
               the viewport, so timeline progress 0.5 lands exactly when the
               row's centre meets the viewport's centre — the moment the plate
               is fully open. */
            const tl = gsap.timeline({
              defaults: { ease: "none" },
              scrollTrigger: {
                trigger: entry,
                start: "top bottom",
                end: "bottom top",
                scrub: 1,
                /* Every distance here is derived from `innerWidth`, so all of
                   them have to be recomputed when the window changes size.
                   This is also what makes the function-based values below get
                   re-evaluated rather than baked in on first run. */
                invalidateOnRefresh: true,
              },
            });

            /* Two halves, each one unit long, so the timeline runs 0 → 2 and
               its midpoint is the peak. `sine.out` into `sine.in` rounds the
               top: with a linear ease the plate visibly corners at full width
               instead of easing through it. */
            tl.fromTo(
              q("[data-reel-plate]"),
              { width: plateRestWidth },
              { width: platePeakWidth, ease: "sine.out", duration: 1 },
              0,
            ).to(
              q("[data-reel-plate]"),
              { width: plateRestWidth, ease: "sine.in", duration: 1 },
              1,
            );

            /* The texture zooms on the same curve as the plate's width, and
               from the same anchor. Because its origin is the left edge, the
               plate opening rightwards and the texture swelling rightwards are
               one movement — the left edge of both is welded to the same line
               and never budges.

               This is `scale`, a transform, so it costs nothing to composite:
               the zoom does not add to the layout work the width tween is
               already doing. */
            tl.fromTo(
              q("[data-reel-texture]"),
              { scale: 1 },
              {
                scale: TEXTURE_ZOOM,
                ease: "sine.out",
                duration: 1,
                force3D: true,
              },
              0,
            ).to(
              q("[data-reel-texture]"),
              { scale: 1, ease: "sine.in", duration: 1, force3D: true },
              1,
            );

            /* The artwork keeps its size throughout and only slides, on the
               same curve as the plate so the two stay locked together. */
            tl.fromTo(
              q("[data-reel-art]"),
              { x: 0 },
              { x: artTravel, ease: "sine.out", duration: 1, force3D: true },
              0,
            ).to(
              q("[data-reel-art]"),
              { x: 0, ease: "sine.in", duration: 1, force3D: true },
              1,
            );

            /* A slow vertical drift across the whole pass, against the
               direction of travel. The plate crops it, so this reads as the
               picture being panned behind a window rather than moved. */
            tl.fromTo(
              q("[data-reel-art]"),
              { yPercent: 4 },
              { yPercent: -4, duration: 2, force3D: true },
              0,
            );

            tl.fromTo(
              q("[data-reel-rule]"),
              { scaleX: 0.12 },
              { scaleX: 1, ease: "sine.out", duration: 1 },
              0,
            ).to(
              q("[data-reel-rule]"),
              { scaleX: 0.12, ease: "sine.in", duration: 1 },
              1,
            );
          });

          /* ScrollTrigger refreshes itself on window `load`, but the reel only
             mounts once the scan has been fetched — long after that fired. On
             a reload the browser then restores the previous scroll offset
             without emitting a scroll event, so every trigger would sit at
             progress 0 and the reel would stay shut until you happened to
             scroll. This measures against where the page actually is. */
          ScrollTrigger.refresh();
        },
      );
    }, rootRef);

    return () => ctx.revert();
  }, [agents]);

  return (
    /* `overflow-x: clip` rather than `hidden`: the plate at full width plus
       the text column still fits, but rounding during a scrub can put it a
       fraction over, and `hidden` would answer that with a horizontal
       scrollbar. `clip` just clips. */
    <section ref={rootRef} className="mt-20 overflow-x-clip pb-24">
      <h2 className="mx-auto max-w-3xl px-6 font-mono text-xs uppercase tracking-[0.3em] text-muted">
        Agents
      </h2>

      {/* No gap and no negative margin. Rows sit flush, and any crowding
          between them is produced by the plates opening and closing — not
          imposed on top of it. */}
      <div className="mt-8 flex flex-col gap-16 lg:gap-0">
        {ordered.map((result, index) => {
          const meta = info.find((a) => a.name === result.agent);
          const panel = PANELS[result.agent];
          const color =
            panel?.color ?? FALLBACK_COLORS[index % FALLBACK_COLORS.length];

          return (
            <Link
              key={result.agent}
              data-reel-entry
              href={`/scan/${scanId}/agents/${result.agent}`}
              className="group flex flex-col lg:flex-row lg:items-center"
              /* The plate's width is animated, which is a layout change. This
                 confines the resulting reflow to the row, so five plates
                 opening at once cannot cascade into a whole-page relayout. */
              style={{ contain: "layout" }}
            >
              {/* The plate. `overflow-hidden` is doing real work: the artwork
                  inside is wider than this box at rest, so this is what crops
                  it — and what un-crops it as the box opens. */}
              <div
                data-reel-plate
                className="relative h-[62vw] w-full shrink-0 overflow-hidden lg:h-[34vw] lg:w-[36vw]"
                style={{ backgroundColor: color }}
              >
                {/* The texture, as a CSS background rather than an <Image>:
                    this layer is zoomed every frame, and a background paints
                    without a DOM node of its own to re-lay-out. `origin-left`
                    is the whole trick — it pins the texture's left edge so the
                    zoom only ever pushes right.

                    It sits over `backgroundColor`, so a texture that is missing
                    or still downloading leaves the flat colour showing rather
                    than a gap. */}
                {panel && (
                  <div
                    data-reel-texture
                    aria-hidden
                    className="absolute inset-0 origin-left bg-cover bg-left bg-no-repeat will-change-transform"
                    style={{ backgroundImage: `url(${panel.texture})` }}
                  />
                )}

                {/* `inset-y-0 my-auto` with an explicit height centres this
                    vertically without using a transform — leaving `x`/`y` free
                    for GSAP, which would otherwise fight a CSS translate. */}
                <div
                  data-reel-art
                  className="absolute inset-y-0 left-0 my-auto h-[42vw] w-[102%] will-change-transform lg:h-[25.3vw] lg:w-[38vw]"
                >
                  {panel && (
                    <Image
                      src={panel.image}
                      alt=""
                      fill
                      sizes="(max-width: 1024px) 102vw, 38vw"
                      // The first panel is usually the one in view when the
                      // reel is reached, so it is the reel's LCP image.
                      priority={index === 0}
                      className="object-cover"
                    />
                  )}
                </div>
              </div>

              {/* Fixed width and no shrink, so the plate pushes this sideways
                  instead of squeezing it — text that reflows while it moves
                  reads as a bug, not as motion. */}
              <div className="px-6 pt-6 lg:w-[32vw] lg:shrink-0 lg:pt-0 lg:pl-[2vw]">
                {/* `w-fit` so the rule below tracks the title's real width —
                    in the reference it underlines the words, not the column. */}
                <div className="w-fit max-w-full">
                  <h3 className="font-geo font-light uppercase leading-[0.95] tracking-[0.005em] text-[clamp(1.5rem,2.7vw,3rem)]">
                    {meta?.display_name ?? result.agent}
                    <sup className="ml-[0.3em] align-super text-[0.24em] font-normal tracking-[0.06em] text-muted transition-colors group-hover:text-parchment">
                      [OPEN]
                    </sup>
                  </h3>

                  <div
                    data-reel-rule
                    className="mt-3 h-px w-full origin-left bg-parchment/35"
                  />
                </div>

                <p
                  className={`mt-5 font-geo text-[13px] font-light uppercase tracking-[0.055em] lg:text-[15px] ${
                    result.error ? "text-critical" : "text-parchment/85"
                  }`}
                >
                  {meta?.category ?? result.agent} · {statusLabel(result)} ·{" "}
                  {result.duration_ms}ms
                </p>

                {meta && (
                  <p className="mt-1.5 font-geo text-[13px] font-light leading-relaxed text-muted lg:text-[15px]">
                    <span className="uppercase tracking-[0.055em]">Role:</span>{" "}
                    {meta.purpose}
                  </p>
                )}
              </div>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
