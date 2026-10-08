import type { AgentResult, ScanReport, ScanStreamHandlers } from './api';
import { waitForScannerHealth } from './scanner-health';

/** Fetch exposes 429 responses, unlike EventSource. Retries retain the job ID. */
export function startScanStream(base: string, path: string, url: string, handlers: ScanStreamHandlers): () => void {
  const controller = new AbortController();
  const requestId = crypto.randomUUID();
  const address = `${base}${path}?url=${encodeURIComponent(url)}&request_id=${requestId}`;
  let finished = false;
  const refresh = () => {
    if (typeof window !== 'undefined') window.dispatchEvent(new Event('usage-changed'));
  };
  const pause = (milliseconds: number) => new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { controller.signal.removeEventListener('abort', abort); resolve(); }, milliseconds);
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
    controller.signal.addEventListener('abort', abort, { once: true });
    if (controller.signal.aborted) abort();
  });
  async function run() {
    await waitForScannerHealth(base, controller.signal, handlers.onWaking);
    handlers.onReady?.();
    for (let attempt = 0; attempt < 4 && !finished && !controller.signal.aborted; attempt++) {
      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      try {
        const response = await fetch(address, { credentials: 'include', signal: controller.signal });
        refresh();
        if (!response.ok) {
          const body = await response.json().catch(() => ({})) as { detail?: string };
          if ((response.status === 409 || response.status >= 500) && attempt < 3) {
            await pause(5000);
            continue;
          }
          if (response.status === 401 && typeof window !== 'undefined') window.location.assign('/login');
          const retry = response.headers.get('Retry-After');
          finished = true;
          handlers.onError((body.detail ?? `Scanner unavailable (${response.status}).`) + (response.status === 429 && retry ? ` Retry in ${retry} seconds.` : ''));
          return;
        }
        reader = response.body?.getReader();
        if (!reader) throw new Error('Scanner did not send a stream.');
        const decoder = new TextDecoder();
        let buffer = '';
        while (!finished) {
          const { value, done } = await reader.read();
          if (done) throw new Error('Stream disconnected before completion.');
          buffer += decoder.decode(value, { stream: true });
          buffer = buffer.replace(/\r\n/g, '\n');
          let end: number;
          while ((end = buffer.indexOf('\n\n')) >= 0 && !finished) {
            const block = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            const lines = block.split('\n');
            const event = lines.find(line => line.startsWith('event:'))?.slice(6).trim();
            const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
            if (!data) continue;
            if (event === 'agent') handlers.onAgent(JSON.parse(data) as AgentResult);
            else if (event === 'done') { finished = true; refresh(); handlers.onDone(JSON.parse(data) as ScanReport); }
            else if (event === 'failed') { finished = true; refresh(); handlers.onError((JSON.parse(data) as { detail?: string }).detail ?? 'The scan could not complete.'); }
          }
        }
      } catch (error) {
        if (controller.signal.aborted || finished) return;
        if (attempt === 3) { finished = true; handlers.onError(error instanceof Error ? error.message : 'Lost connection to the scanner.'); }
        else await pause(5000);
      } finally {
        await reader?.cancel().catch(() => {});
      }
    }
  }
  void run().catch(error => {
    if (!controller.signal.aborted && !finished) handlers.onError(error instanceof Error ? error.message : 'Scanner unavailable.');
  });
  return () => controller.abort();
}
