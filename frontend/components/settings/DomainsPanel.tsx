"use client";

/* Settings → Your domains: proving you control a website before scanning it
   (Launch Plan gate 6, backend/domains.py).

   Each domain shows both ways to prove it, ready to copy: a DNS TXT record,
   which covers the domain and every name under it, and a file on the site,
   which covers that one host. "Check" asks the backend to look for either. */

import { useEffect, useState } from "react";
import {
  addDomain,
  checkDomain,
  fetchDomains,
  removeDomain,
  type DomainClaim,
  type DomainList,
} from "@/lib/api";

function Copyable({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused; the value is on screen to select.
    }
  }

  return (
    <div className="mt-3">
      <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-rule">{label}</p>
      <div className="mt-1 flex items-baseline gap-3">
        <code className="break-all font-mono text-xs text-parchment">{value}</code>
        <button
          type="button"
          onClick={copy}
          className="shrink-0 font-mono text-[9px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-parchment"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

function DomainRow({
  domain,
  onChange,
}: {
  domain: DomainClaim;
  onChange: (next: DomainClaim | null) => void;
}) {
  const [busy, setBusy] = useState<"check" | "remove" | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function check() {
    setBusy("check");
    setError(null);
    try {
      const result = await checkDomain(domain.host);
      setProblems(result.problems);
      onChange(result.domain);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't check that domain.");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("remove");
    setError(null);
    try {
      await removeDomain(domain.host);
      onChange(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't remove that domain.");
      setBusy(null);
    }
  }

  const verified = domain.verified_at !== null;

  return (
    <li className="border-l-2 border-rule pl-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <p className="text-lg leading-snug">{domain.host}</p>
          <p
            className={`mt-1 font-mono text-[10px] uppercase tracking-[0.2em] ${
              verified ? "text-parchment" : "text-muted"
            }`}
          >
            {verified
              ? `Verified by ${domain.method === "dns" ? "DNS record" : "file"} · ${new Date(
                  domain.verified_at as string,
                ).toLocaleDateString()}`
              : "Not verified yet"}
          </p>
        </div>
        <div className="flex gap-5">
          {!verified && (
            <button
              type="button"
              onClick={check}
              disabled={busy !== null}
              className="font-mono text-[10px] uppercase tracking-[0.2em] text-parchment transition-colors hover:text-white disabled:opacity-40"
            >
              {busy === "check" ? "Checking…" : "Check"}
            </button>
          )}
          <button
            type="button"
            onClick={remove}
            disabled={busy !== null}
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted transition-colors hover:text-critical disabled:opacity-40"
          >
            {busy === "remove" ? "Removing…" : "Remove"}
          </button>
        </div>
      </div>

      {verified ? (
        <p className="mt-3 max-w-2xl text-sm text-muted">
          {domain.method === "dns"
            ? `You can scan ${domain.host} and any address under it.`
            : `You can scan ${domain.host}. Addresses under it need their own proof, or a DNS record here.`}
        </p>
      ) : (
        <div className="mt-4 max-w-2xl text-sm leading-relaxed text-muted">
          <p>Do one of these, then press Check.</p>
          <p className="mt-4 text-parchment">
            Add a DNS TXT record{" "}
            <span className="text-muted">(covers {domain.host} and everything under it)</span>
          </p>
          <Copyable label="Name" value={domain.dns_name} />
          <Copyable label="Value" value={domain.dns_value} />
          <p className="mt-5 text-parchment">
            Or put a file on the site{" "}
            <span className="text-muted">(covers {domain.host} only; no redirect)</span>
          </p>
          <Copyable label="Address" value={domain.file_url} />
          <Copyable label="Contents" value={domain.file_contents} />
        </div>
      )}

      {problems.length > 0 && !verified && (
        <ul className="mt-4 max-w-2xl space-y-1 font-mono text-[10px] leading-relaxed text-critical">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {error && (
        <p className="mt-4 font-mono text-[10px] uppercase tracking-[0.2em] text-critical">{error}</p>
      )}
    </li>
  );
}

export function DomainsPanel() {
  const [list, setList] = useState<DomainList | null>(null);
  const [host, setHost] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // `.then` rather than awaiting in the effect body: state is only set from
    // the callback (react-hooks/set-state-in-effect), as the page itself does.
    let cancelled = false;
    fetchDomains()
      .then((next) => {
        if (!cancelled) setList(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Couldn't load your domains.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!host.trim() || adding) return;
    setAdding(true);
    setError(null);
    try {
      const claim = await addDomain(host);
      setList((prev) => {
        const others = (prev?.domains ?? []).filter((d) => d.host !== claim.host);
        return {
          required: prev?.required ?? false,
          domains: [...others, claim].sort((a, b) => a.host.localeCompare(b.host)),
        };
      });
      setHost("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add that domain.");
    } finally {
      setAdding(false);
    }
  }

  function replace(hostName: string, next: DomainClaim | null) {
    setList((prev) =>
      prev
        ? {
            ...prev,
            domains: next
              ? prev.domains.map((d) => (d.host === hostName ? next : d))
              : prev.domains.filter((d) => d.host !== hostName),
          }
        : prev,
    );
  }

  return (
    <section id="domains" className="mt-14 scroll-mt-10">
      <h2 className="font-mono text-xs uppercase tracking-[0.3em] text-muted">Your domains</h2>

      <p className="mt-5 max-w-2xl text-sm leading-relaxed text-muted sm:text-base">
        {list?.required
          ? "Sentinels only scans websites you have shown you control. Add a domain and prove it with a DNS record or a file on the site."
          : "Prove that you control a website with a DNS record or a file on the site. Scans don't need this yet on this deployment, but they will before sign-ups open to everyone."}
      </p>

      <form onSubmit={add} className="mt-8 flex max-w-xl items-end gap-4">
        <div className="flex flex-1 flex-col gap-2">
          <label
            htmlFor="domain"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted"
          >
            Domain
          </label>
          <input
            id="domain"
            type="text"
            placeholder="example.com"
            value={host}
            onChange={(event) => setHost(event.target.value)}
            disabled={adding}
            autoComplete="off"
            className="w-full border-b border-rule bg-transparent pb-2 font-mono text-base outline-none placeholder:text-muted/50 focus:border-parchment disabled:text-muted"
          />
        </div>
        <button
          type="submit"
          disabled={adding || !host.trim()}
          className="glass shrink-0 px-5 py-2.5 font-mono text-xs uppercase tracking-[0.2em] text-parchment transition-colors hover:bg-white/10 disabled:cursor-not-allowed disabled:text-muted"
        >
          {adding ? "Adding…" : "Add"}
        </button>
      </form>

      {error && (
        <p className="glass mt-6 px-4 py-3 font-mono text-[10px] uppercase tracking-[0.2em] text-critical">
          {error}
        </p>
      )}

      {list === null ? (
        !error && (
          <p className="mt-8 animate-pulse font-mono text-[10px] uppercase tracking-[0.25em] text-muted">
            Loading…
          </p>
        )
      ) : list.domains.length === 0 ? (
        <p className="mt-8 text-sm text-muted">No domains added yet.</p>
      ) : (
        <ul className="mt-8 space-y-8">
          {list.domains.map((domain) => (
            <DomainRow
              key={domain.host}
              domain={domain}
              onChange={(next) => replace(domain.host, next)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
