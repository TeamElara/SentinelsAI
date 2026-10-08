import Link from "next/link";

/* The shared frame for /terms and /privacy: a plain reading column with the
   same type roles as the rest of the site, no animation and no client code,
   so both pages are static and readable with scripts off.

   The text itself lives in each page as data (`LegalSection[]`) rather than
   JSX, so a change to what Sentinels does is a change to a sentence, not to
   markup. */

export type LegalSection = {
  heading: string;
  /** Plain paragraphs. */
  body?: string[];
  /** A bulleted list under the paragraphs. */
  items?: string[];
};

export const ISSUES_URL = "https://github.com/TeamElara/SentinelsAI/issues";

export function LegalPage({
  title,
  updated,
  intro,
  sections,
  other,
}: {
  title: string;
  updated: string;
  intro: string;
  sections: LegalSection[];
  other: { href: string; label: string };
}) {
  return (
    <main className="mx-auto w-full max-w-3xl px-6 py-20 sm:px-8">
      <Link
        href="/"
        className="font-mono text-xs uppercase tracking-[0.35em] text-muted transition-colors hover:text-parchment"
      >
        Sentinels
      </Link>

      <h1 className="mt-8 font-display text-5xl sm:text-6xl">{title}</h1>
      <p className="mt-4 font-mono text-[10px] uppercase tracking-[0.25em] text-muted">
        Last updated {updated}
      </p>

      <p className="mt-10 max-w-2xl text-base leading-relaxed text-parchment/85 sm:text-lg">
        {intro}
      </p>

      {sections.map((section) => (
        <section key={section.heading} className="mt-12">
          <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">
            {section.heading}
          </h2>
          {section.body?.map((paragraph) => (
            <p
              key={paragraph}
              className="mt-5 max-w-2xl text-sm leading-relaxed text-muted sm:text-base"
            >
              {paragraph}
            </p>
          ))}
          {section.items && (
            <ul className="mt-5 max-w-2xl space-y-3">
              {section.items.map((item) => (
                <li
                  key={item}
                  className="flex gap-3 text-sm leading-relaxed text-muted sm:text-base"
                >
                  <span className="mt-1.5 shrink-0 font-mono text-[10px] text-rule">—</span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}

      <p className="mt-16 border-t border-rule pt-8 font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
        Questions, corrections or requests:{" "}
        <a
          href={ISSUES_URL}
          className="underline decoration-rule transition-colors hover:text-parchment"
        >
          open an issue on GitHub
        </a>
        {" · "}
        <Link
          href={other.href}
          className="underline decoration-rule transition-colors hover:text-parchment"
        >
          {other.label}
        </Link>
      </p>
    </main>
  );
}
