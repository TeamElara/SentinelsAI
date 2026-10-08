"""Reproduce B2's connect-budget finding without DNS or socket traffic.

Loads the exact reviewed commit instead of silently testing a later B4/B7 fix.
This is diagnostic review evidence, not a test that endorses broken behavior.
"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
import types

import httpcore

COMMIT = 'e47197b0dc30368798e382f7f6a08953d0698307'


async def check():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'backend'))
    source = subprocess.check_output(['git', 'show', f'{COMMIT}:backend/net/client.py'], cwd=root, text=True)
    module = types.ModuleType('reviewed_b2_client')
    exec(compile(source, 'reviewed_b2_client.py', 'exec'), module.__dict__)
    calls = []
    async def slow_dns(host):
        await asyncio.sleep(.05)
        return ['93.184.216.34']
    class Inner(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, **kwargs):
            calls.append({'host':host, 'timeout':kwargs['timeout']})
            return object()
    started = time.perf_counter()
    await module.PolicyBackend(resolver=slow_dns, inner=Inner()).connect_tcp('example.com', 443, timeout=.005)
    dns_elapsed = time.perf_counter() - started
    retry_calls = []
    async def two_addresses(host):
        return ['93.184.216.34', '93.184.216.35']
    class SlowInner(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, **kwargs):
            retry_calls.append(kwargs['timeout'])
            await asyncio.sleep(.03)
            raise httpcore.ConnectTimeout('fixture')
    started = time.perf_counter()
    try:
        await module.PolicyBackend(resolver=two_addresses, inner=SlowInner()).connect_tcp('example.com', 443, timeout=.02)
    except httpcore.ConnectTimeout:
        pass
    return {'reviewed_commit':COMMIT, 'no_real_dns_or_sockets':True,
            'dns':{'connect_timeout_seconds':.005,'elapsed_seconds':round(dns_elapsed,4),'connected_after_budget':bool(calls)},
            'address_retries':{'connect_timeout_seconds':.02,'elapsed_seconds':round(time.perf_counter()-started,4),'timeouts_passed_to_attempts':retry_calls}}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(check()), indent=2))
