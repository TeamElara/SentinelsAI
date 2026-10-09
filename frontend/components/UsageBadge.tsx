'use client';
import { useEffect, useState } from 'react';
import { API_BASE } from '@/lib/api';

type Usage = { budgets: Record<string, { limit: number; remaining: number }> };

/** Show only limits confirmed by the server. Missing/unavailable data stays hidden. */
export function UsageBadge({ kind }: { kind?: 'url_scan' | 'repo_scan' }) {
  const [usage, setUsage] = useState<Usage | null>(null);
  useEffect(() => {
    let active = true;
    async function update() {
      try {
        const response = await fetch(`${API_BASE}/usage`, { credentials: 'include' });
        if (active) setUsage(response.ok ? await response.json() as Usage : null);
      } catch { if (active) setUsage(null); }
    }
    const refresh = () => { void update(); };
    refresh();
    window.addEventListener('usage-changed', refresh);
    window.addEventListener('focus', refresh);
    const timer = setInterval(refresh, 30000);
    return () => { active = false; clearInterval(timer); window.removeEventListener('usage-changed', refresh); window.removeEventListener('focus', refresh); };
  }, []);
  if (!usage) return null;
  const kinds = kind ? [kind] : ['pdf', 'verify', 'ai_fix', 'chat'];
  const labels: Record<string, string> = { url_scan: 'Website scans', repo_scan: 'Repository scans', pdf: 'PDFs', verify: 'Verifications', ai_fix: 'AI fixes', chat: 'Chat answers' };
  return <p className="mt-3 text-xs text-muted" aria-live="polite">
    {kinds.filter(key => usage.budgets[key]).map(key => `${labels[key]}: ${usage.budgets[key].remaining} of ${usage.budgets[key].limit} left`).join(' · ')}. Resets at 00:00 UTC.
  </p>;
}
