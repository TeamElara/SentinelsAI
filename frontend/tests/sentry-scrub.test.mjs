import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { loadTs } from './load-ts.mjs';

const { scrubEvent, scrubText, scrubPath, isExpectedApiError, createGate, ALLOWED_INTEGRATIONS } = await loadTs('../lib/sentry-scrub.ts');
const { sentryEnvelopeUrl } = await loadTs('../lib/sentry-tunnel.ts');

// Things this product really handles; none may survive into a report.
const SECRETS = [
  'victim-site.example',
  'https://victim-site.example/admin?token=abc',
  'ghp_' + 'Z9y8X7w6V5u4T3s2R1q0P9o8',
  'gsk_' + 'abcdEFGH1234ijklMNOP5678',
  'Bearer abcdef0123456789',
  'alice@example.org',
  '203.0.113.77',
  '3f2c1b9e-8d4a-4f6b-9a1c-0e5d7c2b1a90',
  '2001:db8:85a3::8a2e:370:7334',
  'session-cookie-value-123',
  'octocat/secret-repo',
];

function richEvent() {
  return {
    event_id: 'e1', timestamp: 1, level: 'error', platform: 'javascript', release: 'abc123', environment: 'production',
    sdk: { name: 'sentry.javascript.browser', version: '11.6.0' },
    user: { email: SECRETS[5], ip_address: SECRETS[6] },
    server_name: SECRETS[0],
    tags: { target: SECRETS[0] },
    extra: { evidence: SECRETS[1], repo: SECRETS[10] },
    contexts: { device: { model: 'x' }, browser: { name: 'Chrome' } },
    breadcrumbs: [{ category: 'fetch', data: { url: SECRETS[1] } }],
    request: {
      url: `https://sentinels.example/scan/${SECRETS[7]}/files?url=${SECRETS[1]}#${SECRETS[9]}`,
      headers: { Cookie: SECRETS[9], Authorization: SECRETS[4] },
      cookies: { s: SECRETS[9] },
    },
    message: `scan of ${SECRETS[1]} failed`,
    exception: {
      values: [{
        type: 'TypeError',
        value: `cannot read ${SECRETS[0]} using ${SECRETS[2]} / ${SECRETS[3]} / ${SECRETS[4]} for ${SECRETS[5]} from ${SECRETS[6]} and ${SECRETS[8]}`,
        mechanism: { type: 'onerror', handled: false },
        stacktrace: { frames: [{
          filename: `https://sentinels.example/_next/static/chunks/app.js?v=${SECRETS[9]}`,
          abs_path: SECRETS[1], function: 'renderReport', lineno: 10, colno: 4, in_app: true,
          vars: { host: SECRETS[0] }, context_line: SECRETS[1],
        }] },
      }],
    },
  };
}

test('a report contains none of the planted secrets', () => {
  const blob = JSON.stringify(scrubEvent(richEvent()));
  for (const secret of SECRETS) assert.ok(!blob.includes(secret), `leaked: ${secret}`);
});

test('a report keeps what is needed to debug', () => {
  const out = scrubEvent(richEvent());
  assert.deepEqual(out.request, { url: '/scan/:id/files' });
  assert.equal(out.release, 'abc123');
  const entry = out.exception.values[0];
  assert.equal(entry.type, 'TypeError');
  assert.deepEqual(entry.stacktrace.frames[0], {
    filename: '/_next/static/chunks/app.js', function: 'renderReport', lineno: 10, colno: 4, in_app: true,
  });
  for (const dropped of ['user', 'server_name', 'tags', 'extra', 'contexts', 'breadcrumbs']) {
    assert.ok(!(dropped in out), dropped);
  }
});

test('a field the SDK adds later is dropped by default', () => {
  const out = scrubEvent({ ...richEvent(), brand_new_field: SECRETS[0] });
  assert.ok(!('brand_new_field' in out));
});

test('scrubText replaces addresses and leaves file names readable', () => {
  assert.equal(scrubText('see https://a.example/x?y=1 now'), 'see <url> now');
  assert.equal(scrubText('host a.b.example failed'), 'host <host> failed');
  assert.equal(scrubText('edit main.js and package.json'), 'edit main.js and package.json');
  assert.equal(scrubText('ipv6 ::1 and 2001:db8::1'), 'ipv6 <ip> and <ip>');
  assert.equal(scrubText('at 12:30:45 done'), 'at 12:30:45 done');
});

test('scrubPath keeps only the path and replaces ids', () => {
  assert.equal(scrubPath('https://x.example/scan/3f2c1b9e-8d4a-4f6b-9a1c-0e5d7c2b1a90?u=1#h'), '/scan/:id');
  assert.equal(scrubPath('/settings?tab=a'), '/settings');
  assert.equal(scrubPath(undefined), undefined);
});

test('expected 4xx API errors are not reported, other errors are', () => {
  const api = (status) => Object.assign(new Error('x'), { name: 'ApiError', status });
  assert.equal(isExpectedApiError(api(403)), true);
  assert.equal(isExpectedApiError(api(429)), true);
  assert.equal(isExpectedApiError(api(500)), false);
  assert.equal(isExpectedApiError(new TypeError('x')), false);
  assert.equal(isExpectedApiError(null), false);
});

test('the gate lets only the first N reports through', () => {
  const gate = createGate(3);
  assert.deepEqual([1, 2, 3, 4, 5].map(() => gate()), [true, true, true, false, false]);
});

test('the tunnel destination is derived from a valid https DSN only', () => {
  assert.equal(sentryEnvelopeUrl('https://key@o123.ingest.us.sentry.io/456'), 'https://o123.ingest.us.sentry.io/api/456/envelope/');
  assert.equal(sentryEnvelopeUrl('http://key@o123.ingest.us.sentry.io/456'), null);
  assert.equal(sentryEnvelopeUrl('https://key@o123.ingest.us.sentry.io/abc'), null);
  assert.equal(sentryEnvelopeUrl('not a dsn'), null);
  assert.equal(sentryEnvelopeUrl(undefined), null);
});

function sdkSource(dir) {
  let text = '';
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) text += sdkSource(path);
    else if (path.endsWith('.js')) text += readFileSync(path, 'utf8');
  }
  return text;
}

test('every allowed integration name exists in the installed SDK', () => {
  const source = sdkSource(new URL('../node_modules/@sentry/core/build/esm', import.meta.url).pathname)
    + sdkSource(new URL('../node_modules/@sentry/browser/build/npm/esm', import.meta.url).pathname);
  for (const name of ALLOWED_INTEGRATIONS) {
    assert.ok(source.includes(`INTEGRATION_NAME = "${name}"`) || source.includes(`name: "${name}"`), `${name} is not an integration in this SDK version`);
  }
});
