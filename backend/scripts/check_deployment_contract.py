"""Read-only launch preflight against Render and the Vercel /api proxy.

An HTTP 200 health response alone cannot prove the quota/auth integration is live.
No cookies, scans, logins, AI calls or write requests are sent.
"""
import argparse
import asyncio
import json
from pathlib import Path

import httpx


async def check(backend, frontend, *, allow_pdf=False):
    result = {'status': 'passed', 'checks': []}
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for name, base in (('backend', backend.rstrip('/')), ('frontend_proxy', frontend.rstrip('/') + '/api')):
            for path in ('/health', '/auth/me', '/usage', '/export/formats'):
                item = {'surface': name, 'url': base + path, 'status': 'failed'}
                try:
                    response = await client.get(base + path)
                    item['http_status'] = response.status_code
                    if path in ('/usage', '/auth/me'):
                        # No session is supplied: these routes must exist and require one.
                        valid = response.status_code == 401
                        item['expected'] = '401 without a session'
                    elif path == '/health':
                        valid = response.status_code == 200 and response.json().get('status') == 'ok'
                        item['expected'] = '200 with status=ok'
                    else:
                        formats = response.json()
                        identifiers = [entry['format_id'] for entry in formats]
                        item['formats'] = identifiers
                        valid = (response.status_code == 200 and {'json', 'markdown'} <= set(identifiers)
                                 and (allow_pdf or 'pdf' not in identifiers))
                        item['expected'] = 'JSON and Markdown; PDF disabled' if not allow_pdf else 'JSON and Markdown; PDF allowed'
                    item['status'] = 'passed' if valid else 'failed'
                except httpx.RequestError as exc:
                    item.update(status='pending', reason=type(exc).__name__)
                except (ValueError, TypeError, KeyError, AttributeError):
                    item['reason'] = 'Unexpected response shape; application contract was not satisfied.'
                result['checks'].append(item)
    states = {item['status'] for item in result['checks']}
    result['status'] = 'failed' if 'failed' in states else 'pending' if 'pending' in states else 'passed'
    result['scope'] = 'Anonymous HTTP contract only; authenticated flows, remote DB and restart durability still need separate evidence.'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', required=True)
    parser.add_argument('--frontend', required=True)
    parser.add_argument('--allow-pdf', action='store_true', help='Only for a deployment with PDF explicitly enabled after resource checks.')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(check(args.backend, args.frontend, allow_pdf=args.allow_pdf))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    raise SystemExit({'passed': 0, 'failed': 1, 'pending': 2}[result['status']])
