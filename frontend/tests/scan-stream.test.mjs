import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../lib/scan-stream.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { startScanStream } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const originalFetch = globalThis.fetch;
const originalTimeout = globalThis.setTimeout;

function run(fetcher) {
  globalThis.fetch = fetcher;
  globalThis.setTimeout = (callback, ms, ...args) => originalTimeout(callback, ms === 5000 ? 1 : ms, ...args);
  const agents = [];
  let stop;
  const done = new Promise(resolve => {
    stop = startScanStream('/api', '/scan/stream', 'https://example.com/?a=1&b=2', {
      onAgent: agent => agents.push(agent), onDone: report => resolve({ report, agents }), onError: error => resolve({ error, agents }),
    });
  });
  return { done, stop };
}

test('chunked UTF-8 SSE delivers agents and one terminal report', async () => {
  try {
    const data = new TextEncoder().encode('event: agent\ndata: {"agent":"TLS ✓"}\n\nevent: done\ndata: {"id":"stored"}\n\n');
    const { done } = run(async () => new Response(new ReadableStream({ start(controller) {
      for (let i = 0; i < data.length; i += 2) controller.enqueue(data.slice(i, i + 2));
      controller.close();
    } })));
    const result = await done;
    assert.deepEqual(result, { report: { id: 'stored' }, agents: [{ agent: 'TLS ✓' }] });
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});

test('429 is explicit and does not retry or launch another scan', async () => {
  try {
    let calls = 0;
    const { done } = run(async () => { calls++; return Response.json({ detail: 'Daily scan limit reached.' }, { status: 429, headers: { 'Retry-After': '300' } }); });
    const result = await done;
    assert.match(result.error, /Daily scan limit reached.*300 seconds/);
    assert.equal(calls, 1);
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});

test('disconnect retry retains request ID and encoded target', async () => {
  try {
    const addresses = [];
    const { done } = run(async address => {
      addresses.push(address);
      return new Response(addresses.length === 1 ? '' : 'event: done\ndata: {"id":"same-job"}\n\n');
    });
    assert.equal((await done).report.id, 'same-job');
    assert.equal(addresses.length, 2);
    assert.equal(addresses[0], addresses[1]);
    const params = new URL(addresses[0], 'https://frontend.example').searchParams;
    assert.equal(params.get('url'), 'https://example.com/?a=1&b=2');
    assert.match(params.get('request_id'), /^[a-f0-9-]{36}$/);
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});

test('component cancellation aborts transport without surfacing an error', async () => {
  try {
    let aborted = false;
    const { stop } = run(async (_address, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => { aborted = true; reject(new DOMException('Aborted', 'AbortError')); });
    }));
    stop();
    await new Promise(resolve => originalTimeout(resolve, 10));
    assert.equal(aborted, true);
  } finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimeout; }
});
