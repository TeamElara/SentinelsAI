import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadTs } from './load-ts.mjs';

const { waitForScannerHealth } = await loadTs('../lib/scanner-health.ts');
const settle = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };

function setup(t) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 0 });
  t.mock.method(AbortSignal, 'timeout', milliseconds => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException('Timeout', 'TimeoutError')), milliseconds);
    return controller.signal;
  });
}

test('healthy service starts immediately without showing wake message', async t => {
  setup(t);
  t.mock.method(globalThis, 'fetch', async () => Response.json({ status: 'ok' }));
  let waking = false;
  await waitForScannerHealth('/api', new AbortController().signal, () => { waking = true; });
  assert.equal(waking, false);
});

test('three-second timeout shows wake message; next probe starts at five seconds', async t => {
  setup(t);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    calls.push(Date.now());
    if (calls.length === 2) return Response.json({ status: 'ok' });
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason)));
  });
  let waking = 0;
  const work = waitForScannerHealth('/api', new AbortController().signal, () => { waking++; });
  t.mock.timers.tick(2999);
  await settle();
  assert.equal(waking, 0);
  t.mock.timers.tick(1);
  await settle();
  assert.equal(waking, 1);
  t.mock.timers.tick(2000);
  await work;
  assert.deepEqual(calls, [0, 5000]);
});

test('cancelling during wake wait stops further probes', async t => {
  setup(t);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async () => { calls.push(1); return new Response('', { status: 503 }); });
  const controller = new AbortController();
  const work = waitForScannerHealth('/api', controller.signal);
  await settle();
  controller.abort();
  await assert.rejects(work, { name: 'AbortError' });
  t.mock.timers.tick(10000);
  await settle();
  assert.equal(calls.length, 1);
});

test('failed health probes stop after the bounded wait', async t => {
  setup(t);
  t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 503 }));
  const work = waitForScannerHealth('/api', new AbortController().signal);
  const checked = assert.rejects(work, /taking too long to wake up/);
  for (let i = 0; i < 25; i++) { await settle(); t.mock.timers.tick(5000); }
  await checked;
});
