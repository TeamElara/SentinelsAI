"""Direct header and browser checks for a local production build or live host.

Run from backend: python scripts/check_frontend_headers.py --url URL --output FILE
--local-fixtures additionally verifies wake-up/stream UI with mocked API data.
Never passes application checks for a Vercel SSO redirect.
"""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright

HEADER_RULES = {
    'content-security-policy': "frame-ancestors 'none'",
    'strict-transport-security': 'max-age=',
    'referrer-policy': 'strict-origin-when-cross-origin',
    'x-content-type-options': 'nosniff',
    'x-frame-options': 'DENY',
    'permissions-policy': 'camera=()',
}


async def check(args):
    base = args.url.rstrip('/')
    result = {'url': base, 'http': [], 'browser': {}, 'status': 'pending'}
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for path in ('/', '/login', '/url', '/repo', '/settings', '/missing-c11', '/api/missing-c11'):
            response = await client.get(base + path)
            if response.is_redirect and urlsplit(response.headers.get('location', '')).hostname == 'vercel.com':
                result['reason'] = 'Vercel deployment protection returned SSO, not an application response.'
                return result
            headers = {key: response.headers.get(key, '') for key in HEADER_RULES}
            assert all(value in headers[key] for key, value in HEADER_RULES.items()), (path, headers)
            assert "object-src 'none'" in headers['content-security-policy']
            result['http'].append({'path': path, 'status': response.status_code, 'headers': headers})

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context()
            page = await context.new_page()
            messages = []
            page.on('console', lambda message: messages.append(message.text))
            health_calls, streams = [], []
            if args.local_fixtures:
                fixture = {'id':'browser-fixture','url':'https://example.com','target_type':'url',
                    'scanned_at':'2026-10-08T10:00:00Z','duration_ms':1,'score':100,'grade':'A',
                    'scorer_version':'canonical-v2','summary':'Incomplete fixture coverage.',
                    'counts':{},'findings':[],'readiness_score':0,'deployment_status':'incomplete',
                    'checklist':[],'subdomains':[],'provisional':True,
                    'agents':[{'agent':'headers','findings':[],'duration_ms':1,'error':None,
                        'coverage_status':'unavailable','coverage':[{'check':'headers','status':'unavailable','reason':'Fixture server timeout','required':True}]}]}
                async def api(route):
                    path = urlsplit(route.request.url).path
                    if path == '/health':
                        health_calls.append(asyncio.get_running_loop().time())
                        if len(health_calls) == 1:
                            await asyncio.sleep(3.5)
                        try:
                            await route.fulfill(json={'status': 'ok'})
                        except Exception:
                            pass  # First request is intentionally aborted at three seconds.
                    elif path == '/usage':
                        await route.fulfill(json={'budgets': {key: {'remaining': 7, 'limit': 10} for key in ('url_scan','repo_scan','pdf','verify','ai_fix','chat')}})
                    elif path == '/scan/stream':
                        streams.append(route.request.url)
                        await route.fulfill(content_type='text/event-stream', body='event: done\ndata: ' + json.dumps({'id':'browser-fixture'}) + '\n\n')
                    elif path == '/scans/browser-fixture':
                        await route.fulfill(json=fixture)
                    elif path == '/scans/browser-fixture/export/pdf':
                        await route.fulfill(status=429, json={'detail':'Daily PDF limit reached. Resets at 00:00 UTC.'}, headers={'Retry-After':'300'})
                    elif path == '/agents':
                        await route.fulfill(json=[])
                    elif path == '/auth/me':
                        await route.fulfill(json={'id':1,'github_id':1,'github_login':'fixture','avatar_url':None})
                    else:
                        await route.fulfill(status=404, json={'detail':'Fixture only'})
                await context.route('http://localhost:8000/**', api)
            await page.goto(base + '/login')
            await page.get_by_role('link', name='Sign in with GitHub').wait_for()
            result['browser']['login_rendered'] = True
            if args.local_fixtures:
                await page.goto(base + '/url')
                await page.get_by_label('Address to inspect').fill('https://example.com')
                await page.get_by_role('button', name='Inspect', exact=True).click()
                await page.get_by_role('status').filter(has_text='Waking up the scanner').wait_for(timeout=4500)
                await page.screenshot(path=str(args.output.with_suffix('.png')), full_page=True)
                await page.wait_for_url('**/scan/browser-fixture', timeout=12000)
                await page.get_by_text('Provisional score and grade:', exact=False).wait_for()
                await page.locator('details summary').filter(has_text='headers').click()
                await page.get_by_text('Fixture server timeout', exact=False).wait_for()
                await page.get_by_role('button', name='Download PDF', exact=True).click()
                await page.get_by_text('Daily PDF limit reached.', exact=False).wait_for()
                assert len(health_calls) == 2 and len(streams) == 1
                assert 4.7 < health_calls[1] - health_calls[0] < 6
                result['browser']['cold_start'] = {'probes':len(health_calls),'streams':len(streams),'retry_interval_seconds':round(health_calls[1]-health_calls[0],2)}
                result['browser']['incomplete_coverage_visible'] = True
                result['browser']['pdf_429_message_visible'] = True
            assert not any('Content Security Policy' in message or 'violates' in message.lower() for message in messages), messages
            result['browser']['normal_flow_csp_violations'] = []

            # A foreign parent must fail to frame the real app response.
            parent_url = base + '/qa-frame-parent'
            await page.route(parent_url, lambda route: route.fulfill(content_type='text/html', body=f'<iframe src="{base}/login"></iframe>'))
            await page.goto(parent_url)
            await page.wait_for_timeout(600)
            blocked = [message for message in messages if 'frame-ancestors' in message or 'X-Frame-Options' in message]
            assert blocked, 'The browser did not reject framing.'
            result['browser']['framing_blocked'] = True

            # Cross-origin navigation reveals only the app origin, not scan paths.
            await page.goto(base + '/scan/referrer-fixture')
            received = []
            async def destination(route):
                received.append(route.request.headers.get('referer', ''))
                await route.fulfill(content_type='text/html', body='Referrer QA')
            await page.route('https://referrer-qa.invalid/**', destination)
            await page.evaluate("""() => { const link=document.createElement('a'); link.href='https://referrer-qa.invalid/'; document.body.append(link); link.click(); }""")
            await page.wait_for_url('https://referrer-qa.invalid/')
            assert received == [base + '/'], received
            result['browser']['external_navigation_referrer'] = received[0]
            result['status'] = 'passed'
            return result
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--local-fixtures', action='store_true')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(check(args))
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key != 'http'}, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 2)
