import type { Metadata } from "next";

import { LegalPage, type LegalSection } from "@/components/legal/LegalPage";

export const metadata: Metadata = {
  title: "Terms — Sentinels",
  description: "The rules for using Sentinels: scan only what you may test, and what the results are and are not.",
};

/* DRAFT. Plain-language terms for an invite-only beta. Not legal advice — it
   leaves out the governing law and the company name on purpose, because those
   are decisions for the people running the service and a lawyer should settle
   them before the public launch. */

const UPDATED = "8 October 2026";

const SECTIONS: LegalSection[] = [
  {
    heading: "Using Sentinels",
    body: [
      "By signing in you agree to these terms. Sentinels is a beta: it is invite-only, it can change or stop at any time, and it may have mistakes. If you do not agree, do not use it.",
    ],
  },
  {
    heading: "Only scan what you may test",
    body: [
      "Sentinels sends real requests to the website you give it. Some checks look for things that should not be public, such as exposed configuration files. Testing a system you have no right to test can be illegal where you or the target are.",
    ],
    items: [
      "Scan a website only if you own it or have written permission from its owner to security-test it. Sentinels asks you to confirm this each time and records that you did.",
      "Repository scans read a public GitHub repository. Fix pull requests are only opened on repositories where you installed the App and where you can push.",
      "Do not use Sentinels to attack, overload, harass, or gather information about systems for harm. Sentinels is a passive tool and sends no attack traffic, but that does not make scanning something you may not test acceptable.",
      "Do not try to get around its limits, read other people's scans, or use it to reach internal networks or services. Scanning private, local or internal addresses is not allowed.",
    ],
  },
  {
    heading: "Your account",
    items: [
      "Sign-in is through GitHub. Keep your GitHub account secure; what happens under your session is your responsibility.",
      "Access is by invitation. You may not share your access with others.",
      "We may limit how much you can scan, and may suspend or remove an account that breaks these terms or harms the service or others, with or without notice.",
      "You can delete your account at any time from Settings. See the privacy page for what that removes.",
    ],
  },
  {
    heading: "What the results are, and are not",
    items: [
      "A report is a passive, point-in-time view of what a scan could read. It can be incomplete, and it can be wrong, with missed problems and false alarms. A scan can be incomplete when a check could not run.",
      "A good grade does not mean a site is secure, and a poor one does not mean it has been or will be attacked.",
      "AI-written summaries, fix suggestions and chat answers can be mistaken. Check them before relying on them.",
      "Sentinels is not professional security, legal or compliance advice and does not replace a penetration test or an audit.",
    ],
  },
  {
    heading: "Fix pull requests",
    items: [
      "A fix is shown to you as an exact change before anything is written. Nothing happens until you approve it.",
      "Sentinels opens one pull request on a new branch whose name starts with sentinels/. It never pushes to your default branch, never merges, and never force-pushes. You decide whether to merge, and you are responsible for reviewing what you merge.",
      "You can stop Sentinels using a repository at any time by disconnecting it in Settings, and remove the App completely in your GitHub settings.",
    ],
  },
  {
    heading: "Your content and ours",
    body: [
      "You keep whatever rights you have in the sites and repositories you scan and in what you type. You give Sentinels permission to process them to provide the service, as described on the privacy page.",
    ],
  },
  {
    heading: "No guarantee, and limits on liability",
    body: [
      "Sentinels is provided as it is, with no promise that it will be available, accurate, or fit for any purpose. To the extent the law allows, the people who run Sentinels are not liable for losses arising from using it or from results or fixes it produced, including a pull request you merged. Nothing here limits any right you have that the law does not allow to be limited.",
    ],
  },
  {
    heading: "Changes and contact",
    body: [
      "We may update these terms. If a change matters, the date at the top changes and we will say so in the app; using Sentinels after that means you accept the new terms.",
    ],
  },
];

export default function TermsPage() {
  return (
    <LegalPage
      title="Terms of use"
      updated={UPDATED}
      intro="The rules for using Sentinels during the beta, in plain language: scan only what you are allowed to test, treat results as a starting point, and review every fix before you merge it."
      sections={SECTIONS}
      other={{ href: "/privacy", label: "Privacy" }}
    />
  );
}
