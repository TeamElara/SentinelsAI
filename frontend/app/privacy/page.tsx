import type { Metadata } from "next";

import { LegalPage, type LegalSection } from "@/components/legal/LegalPage";

export const metadata: Metadata = {
  title: "Privacy — Sentinels",
  description: "What Sentinels stores, who else sees it, how long it is kept and how to delete it.",
};

/* DRAFT. Written from what the code does on the date below, and it has to be
   kept true: when Sentinels starts using a new service (error monitoring, a
   hosted database, a new AI provider) or stores something new, change the
   matching section in the same pull request. Not legal advice — a lawyer
   should read it before the public launch. */

const UPDATED = "9 October 2026";

const SECTIONS: LegalSection[] = [
  {
    heading: "What Sentinels is",
    body: [
      "Sentinels is a security auditor in beta. You give it a website address or a public GitHub repository, it reads what is publicly visible, and it returns a graded report. If you connect a repository, it can also open pull requests that fix what it found. This page says what that involves for your data.",
    ],
  },
  {
    heading: "What we store, and why",
    items: [
      "Your account: your GitHub numeric id, your GitHub login (username) and your avatar address, read from GitHub when you sign in, plus when you first and last used Sentinels. We use them to know whose scans are whose. We do not store a password, your email address, or the GitHub access token used at sign-in; that token is used once to read your profile and is then discarded.",
      "Your sign-in sessions: a random token (stored only as a hash) and its expiry, so you stay signed in for up to 14 days.",
      "Your scans: the address or repository you scanned, the score and grade, every finding with its evidence, the deployment checklist and any answers you gave to it, and the plain-language summary. For a website, evidence includes response headers, certificate details, DNS records and the subdomains found. For a repository, it includes file paths, line numbers, file sizes and languages, and short excerpts that show a problem; where a secret is detected its value is masked before it is stored. Repository contents are downloaded to be scanned and are not kept.",
      "Your chat with the assistant about a scan: the questions you ask and the answers given.",
      "Fixes: the changes Sentinels proposed (which can include the text of the files it would change), whether a pull request was opened, its number and link, and its status.",
      "Connected repositories: if you install the Sentinels GitHub App, the installation number, the account it covers, whether it covers all or selected repositories, and the permissions granted.",
      "Your daily usage: how many scans, verifications, PDF exports, AI fixes and chat questions you used today, and the scan jobs behind them (the address and whether the job finished), so the daily allowances can be enforced and a scan can be resumed if your connection drops.",
      "A record of actions: when you confirm you may test a website (with the address), and when a pull request is opened for you, with the time. This record exists so that what Sentinels did on someone's behalf can be accounted for.",
    ],
  },
  {
    heading: "What we do not do",
    items: [
      "No advertising, no analytics or tracking scripts, and no selling or renting of data.",
      "No cookies other than the ones below.",
      "We do not read your private repositories unless you install the App on them, and then only the files needed to prepare a fix.",
    ],
  },
  {
    heading: "Cookies",
    items: [
      "A sign-in cookie that keeps you signed in. It is not readable by page scripts and lasts up to 14 days.",
      "Short-lived cookies (about ten minutes) used only to complete the GitHub sign-in and install steps safely.",
    ],
  },
  {
    heading: "Who else handles your data",
    body: [
      "Sentinels uses these services to work. Each sees only what is needed for its part.",
    ],
    items: [
      "GitHub: sign-in, reading public repositories, reading and writing files and pull requests on repositories where you installed the App. GitHub's own privacy statement applies to what it holds.",
      "Groq (AI provider): to write the summary, fix suggestions and chat answers, Sentinels sends it the scan's findings, short evidence excerpts, discovered subdomain names, checklist state and your chat questions. It is not sent your GitHub token or repository files as a whole. If the AI service is unavailable, reports still work without those parts.",
      "Render (hosts the Sentinels server and, today, its database) and Vercel (hosts this website): they process requests and keep ordinary server logs, which include your IP address, browser type and the time of each request.",
      "Sentry (error reports), only if switched on for this deployment: when the server hits an unexpected error it sends the kind of error, the file and line in Sentinels' own code, which route it was (with ids removed) and the software version. It never receives the addresses you scan, findings, repository names or contents, chat, tokens, cookies, IP addresses or your name; those are removed before anything is sent.",
      "Public services queried while scanning: DNS resolvers (Google and Cloudflare) and the certificate transparency search crt.sh receive the domain being scanned; the OSV vulnerability database receives package names and versions found in a scanned repository.",
      "The website you scan: it receives ordinary requests from Sentinels' servers, as any visitor's browser would send.",
    ],
  },
  {
    heading: "How long we keep it",
    items: [
      "Your account, scans, chat, fixes and connected-repository records are kept until you delete them or delete your account.",
      "Sessions end after 14 days or when you sign out.",
      "If you delete your account, today's usage counts are kept against your GitHub numeric id until the end of that UTC day, so that deleting and signing in again does not reset the daily allowances. Nothing else about your usage is kept.",
      "Server logs are kept by the hosting providers under their own schedules.",
      "After you delete your account, the record of actions (see above) is kept without your name on it, because the pull requests it describes remain on GitHub. Backups made by our hosting providers age out on their schedule.",
    ],
  },
  {
    heading: "Your choices",
    items: [
      "Delete your account: Settings → Delete account. This removes your account, sessions, scans, reports, chat, fixes and connected-repository records.",
      "Disconnect a repository: Settings → Disconnect stops Sentinels using it at once. To remove the App from GitHub as well, uninstall it in your GitHub settings under Applications; only you can do that.",
      "Have one scan removed, ask for a copy of your data or a correction, or raise a concern: open an issue using the link at the bottom of this page.",
    ],
  },
  {
    heading: "Who may use Sentinels",
    body: [
      "Sentinels is invite-only during the beta and is not meant for children. If you believe a child has signed in, tell us and we will remove the account.",
    ],
  },
  {
    heading: "Security",
    body: [
      "Scans belong to the account that ran them and are not visible to other accounts. Sentinels never merges a pull request itself and never writes to a repository's default branch. No system is perfectly secure: if we learn of a breach affecting your data, we will tell affected users.",
    ],
  },
  {
    heading: "Changes",
    body: [
      "If this page changes in a way that matters, the date at the top changes and we will say so in the app.",
    ],
  },
];

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy"
      updated={UPDATED}
      intro="This is what Sentinels stores about you and your scans, who else sees it, and how to have it removed. It is written to match what the software actually does."
      sections={SECTIONS}
      other={{ href: "/terms", label: "Terms of use" }}
    />
  );
}
