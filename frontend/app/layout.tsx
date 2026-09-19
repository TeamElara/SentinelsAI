import type { Metadata } from "next";
import {
  Instrument_Serif,
  JetBrains_Mono,
  Inter,
  Space_Grotesk,
} from "next/font/google";
import "./globals.css";

/* Three type roles, per docs/DESIGN.md — not two.

   Each call downloads the font at build time and self-hosts it, so the browser
   never asks Google for anything at runtime. `variable` names the CSS custom
   property the font gets bound to; globals.css maps those onto Tailwind's
   --font-* tokens, which is what makes `font-display` etc. work in markup. */

// Display: the letter grade, chapter titles. Instrument Serif ships one weight,
// so unlike the other two it has to be stated explicitly.
const instrumentSerif = Instrument_Serif({
  variable: "--font-instrument-serif",
  subsets: ["latin"],
  weight: "400",
});

// Evidence: headers, certificates, DNS records. Fixed-width because that data
// genuinely is fixed-width machine output — not as a hacker affectation.
const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

// Body: everything read in sentences.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

/* Landing-page display face. The reference site sets its hero in "At Aero",
   which is a commercially licensed typeface — so this is a substitution, not
   a copy: Space Grotesk is the closest freely-licensed face in the same
   geometric-grotesque family (constructed shapes, tight apertures, a little
   character in the terminals rather than pure neutrality).

   Scoped to the landing page only. Instrument Serif is still the display
   face for the scan and report screens, so docs/DESIGN.md's typographic
   system is untouched everywhere the product actually does its work. */
const spaceGrotesk = Space_Grotesk({
  variable: "--font-space-grotesk",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Sentinels — passive website security inspection",
  description:
    "Reads a site's headers, certificate, DNS records and robots.txt, then issues a graded inspection report. Never sends attack traffic.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${instrumentSerif.variable} ${jetbrainsMono.variable} ${inter.variable} ${spaceGrotesk.variable} h-full antialiased`}
    >
      <body className="font-body min-h-full flex flex-col">{children}</body>
    </html>
  );
}
