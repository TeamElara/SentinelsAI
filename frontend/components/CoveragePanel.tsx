import type { AgentResult } from "@/lib/api";

export function CoveragePanel({ agents }: { agents: AgentResult[] }) {
  return (
    <section className="mt-10 max-w-4xl border-t border-rule pt-6">
      <h2 className="font-mono text-xs uppercase tracking-[0.2em] text-muted">Check coverage</h2>
      <p className="mt-3 text-sm text-muted">Coverage records what ran. A completed check can still find a security issue.</p>
      {agents.map((agent) => (
        <details key={agent.agent} className="mt-4 glass px-4 py-3">
          <summary className="cursor-pointer font-mono text-xs">{agent.agent} · {agent.coverage_status ?? "unavailable"}</summary>
          {!agent.coverage?.length && <p className="mt-3 text-sm text-muted">This older report did not record check coverage.</p>}
          <ul className="mt-3 space-y-3">
            {agent.coverage?.map((check, i) => <li key={i} className="text-sm break-words">
              <strong>{check.status}</strong> · {check.check}
              <p className="mt-1 text-muted">{check.reason}{!check.required && " (outside this target's required scope)"}</p>
            </li>)}
          </ul>
        </details>
      ))}
    </section>
  );
}
