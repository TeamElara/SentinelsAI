import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

// The route imports "next/server", which a data: module can't resolve, so it
// is compiled here and given the real package through a require.
const require = createRequire(import.meta.url);
const { NextRequest } = require('next/server');
const source = readFileSync(new URL('../app/api/auth/github/callback/route.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const module_ = { exports: {} };
new Function('require', 'module', 'exports', 'process', code)(require, module_, module_.exports, process);
const { GET } = module_.exports;

const originalFetch = globalThis.fetch;
process.env.NEXT_PUBLIC_API_BASE = 'https://api.example.test';

function call(path, cookies, upstream) {
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), cookie: init?.headers?.cookie ?? null });
    return upstream;
  };
  const request = new NextRequest(`https://web.example.test${path}`, {
    headers: cookies ? { cookie: cookies } : {},
  });
  return GET(request).then((response) => ({ response, seen }));
}

const all = 'sentinels_oauth_state=oauth1; sentinels_install_state=install1; sentinels_session=sess1; other=x';

test('an install redirect forwards the install state and the session, not the sign-in state', async () => {
  try {
    const { response, seen } = await call(
      '/api/auth/github/callback?code=c&state=install1&installation_id=500&setup_action=install',
      all,
      new Response(null, { status: 307, headers: { location: 'https://web.example.test/settings?installed=octo' } }),
    );
    assert.equal(seen.length, 1);
    assert.equal(
      seen[0].url,
      'https://api.example.test/auth/github/callback?code=c&state=install1&installation_id=500&setup_action=install',
    );
    assert.equal(seen[0].cookie, 'sentinels_install_state=install1; sentinels_session=sess1');
    assert.equal(response.headers.get('location'), 'https://web.example.test/settings?installed=octo');
    // The install state is single-use; no session cookie is set by an install.
    const setCookie = response.headers.get('set-cookie') ?? '';
    assert.match(setCookie, /sentinels_install_state=;/);
    assert.doesNotMatch(setCookie, /sentinels_session=sess/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('a sign-in redirect still forwards only its own state cookie', async () => {
  try {
    const { response, seen } = await call(
      '/api/auth/github/callback?code=c&state=oauth1',
      all,
      new Response(null, {
        status: 307,
        headers: { location: 'https://web.example.test/', 'set-cookie': 'sentinels_session=newsession; Path=/; HttpOnly' },
      }),
    );
    assert.equal(seen[0].cookie, 'sentinels_oauth_state=oauth1');
    assert.equal(seen[0].url, 'https://api.example.test/auth/github/callback?code=c&state=oauth1');
    const setCookie = response.headers.get('set-cookie') ?? '';
    assert.match(setCookie, /sentinels_session=newsession/);
    assert.doesNotMatch(setCookie, /sentinels_install_state=;/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('an install redirect with no cookies forwards none', async () => {
  try {
    const { seen } = await call(
      '/api/auth/github/callback?code=c&state=s&installation_id=500',
      null,
      new Response(null, { status: 307, headers: { location: 'https://web.example.test/settings?install_error=not_signed_in' } }),
    );
    assert.equal(seen[0].cookie, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
