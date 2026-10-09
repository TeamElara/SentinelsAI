/** A sleeping Render service is retried every five seconds, with cancellation. */
export async function waitForScannerHealth(base: string, signal: AbortSignal, onWaking?: () => void): Promise<void> {
  const started = Date.now();
  const maximum = 120000;
  let nextAttempt = started;
  let waking = false;
  while (Date.now() - started < maximum) {
    signal.throwIfAborted();
    const delay = Math.max(0, nextAttempt - Date.now());
    if (delay) await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, delay);
      const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) abort();
    });
    signal.throwIfAborted();
    if (Date.now() - started >= maximum) break;
    const probeStart = Date.now();
    try {
      const response = await fetch(`${base}/health`, { credentials: 'include', cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(3000)]) });
      const body = response.ok ? await response.json() as { status?: string } : null;
      if (body?.status === 'ok') return;
    } catch {
      signal.throwIfAborted();
    }
    if (!waking) { waking = true; onWaking?.(); }
    nextAttempt = probeStart + 5000;
  }
  throw new Error('The scanner is taking too long to wake up. Please try again shortly.');
}
