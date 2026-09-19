/* The landing page — the site's front door.

   The scan launcher that used to live here now sits at `/url`, but the
   working input reappears at the bottom of this page: once the photo has
   assembled, scrolling on lands you at a real choice of engine and a real
   address field, so the page ends by doing the thing it spent the hero
   describing.

   This file stays a server component. The interactive pieces
   (`SmoothScroll`, `HeroPuzzle`, `ScanTypeSelect`) are their own client
   islands, so slow-loading JS delays the puzzle and nothing else.

   THE SEQUENCE, which is what all the pieces below are arranged around:

     on load    the wordmark, huge, top-left; the tagline at the bottom-left
     act one    the wordmark shrinks to logo size while the tagline flies up
                to sit beneath it — same start, same end, no delay between
     act two    the first pieces appear out of the far distance and hover
     act three  the tagline scrolls away; the picture assembles
     after      the pinned hero releases and the address field arrives

   Act One's two movements are locked to each other on purpose; see
   ACT_ONE_END in HeroPuzzle for why the easing matters as much as the
   timing there. */

import { SplitText } from "@/components/landing/SplitText";
import { HeroPuzzle } from "@/components/landing/HeroPuzzle";
import { SmoothScroll } from "@/components/landing/SmoothScroll";
import { ScrollCursor } from "@/components/landing/ScrollCursor";
import { ScanTypeSelect } from "@/components/landing/scan-select/ScanTypeSelect";

export default function LandingPage() {
  return (
    <SmoothScroll>
      {/* The trailing "scroll down" label. Sits outside <main> because it is
          fixed-position chrome, not part of the document flow. */}
      <ScrollCursor />

      <main className="flex flex-1 flex-col">
        {/* One pinned hero holding both the heading and the image, the way
            the reference composes it. The heading is passed in as `header`
            so it lives inside the same sticky element as the grid — that is
            what keeps it parked near the top of the screen while the image
            assembles below, instead of scrolling away. */}
        <HeroPuzzle
          header={
            <>
              {/* THE SHRINKING WORDMARK.

                  The h1 is set at its HUGE size and scaled DOWN on scroll,
                  rather than starting small and being scaled up. Two
                  reasons: text rasterised at a large size and scaled down
                  stays crisp, whereas scaling up magnifies the glyph bitmap
                  and goes soft; and `transform: scale` never touches
                  layout, so the shrink costs nothing per frame.

                  This wrapper reserves only the FINAL, logo-sized height. At
                  the top of the page the full-size wordmark deliberately
                  overflows it, spilling down over the image area — which is
                  free real estate at that moment, because the tiles are
                  still at opacity 0 and don't appear until the shrink has
                  finished. That is what lets the wordmark be genuinely huge
                  on arrival without a gaping hole under it afterwards.

                  The heights are fontSize x line-height x titleTargetScale:
                  20vw x 1.1 x 0.28, 17 x 1.1 x 0.20, 13 x 1.1 x 0.16. */}
              <div className="h-[6.16vw] sm:h-[3.74vw] lg:h-[2.29vw]">
                {/* `origin-top-left` is what keeps the position fixed while
                    the size changes: scaling about the top-left corner
                    leaves that corner pinned, so the wordmark shrinks in
                    place instead of drifting toward a centre point. */}
                <h1
                  data-hero-title
                  className="origin-top-left font-geo font-medium leading-[1.1] tracking-[-0.03em]
                             text-[20vw] will-change-transform sm:text-[17vw] lg:text-[13vw]"
                >
                  <SplitText text="Sentinels" delay={150} stagger={0} />
                </h1>
              </div>

              {/* THE TRAVELLING TAGLINE.

                  It is written here, directly under the wordmark, because
                  this is where it ENDS UP — and measuring a real laid-out
                  element beats hard-coding a destination that breaks the
                  moment the text wraps differently. HeroPuzzle measures this
                  resting position, works out the offset that would put it at
                  the bottom-left of the first screen, and starts it there.

                  So on load it reads as bottom-left copy; by the end of Act
                  One it has flown up to sit under the logo. None of that is
                  in this file's markup, deliberately — the layout describes
                  the destination, the motion describes the journey. */}
              <p
                data-hero-subtitle
                className="mt-[1.8vh] max-w-none font-mono text-xl leading-[1.5] text-muted opacity-0 will-change-transform sm:max-w-[54.4vw] sm:text-2xl"
              >
                AI-Powered Autonomous Website Security Auditor
              </p>
            </>
          }
        />

        {/* THE PAYOFF. The hero above is 560vh of pinned scrolling; this is
            the first thing that moves normally again, which is what makes it
            read as arriving rather than as another hero beat.

            It asks which engine before it asks for an address — the choice
            between a live site and a repository is a real fork in what
            Sentinels does, and burying it in a dropdown above a single input
            would understate it. The URL branch still ends in the same
            `ScanLauncher` the `/url` page uses, so this is a real scan and
            not a link dressed up as one. */}
        <ScanTypeSelect />
      </main>
    </SmoothScroll>
  );
}
