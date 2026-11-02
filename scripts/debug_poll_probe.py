#!/usr/bin/env python3
"""Compare one equivalent PDB/DAP stop and inspection without model calls."""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time

from debug_smoke import Client, ROOT, alive


def native(python, root):
    started = time.monotonic()
    proc = subprocess.Popen([str(python), '-u', '-m', 'pdb', 'payroll.py'], cwd=root,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffer = b''
    def prompt():
        nonlocal buffer
        deadline = time.monotonic() + 20
        while b'(Pdb) ' not in buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([proc.stdout], [], [], remaining)[0]:
                raise TimeoutError('native debugger prompt')
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError('native debugger closed stdout')
            buffer += chunk
        before, buffer = buffer.split(b'(Pdb) ', 1)
        return before.decode('utf-8', 'replace')
    def send(command):
        proc.stdin.write((command + '\n').encode()); proc.stdin.flush()
        return prompt()
    try:
        prompt()
        startup_ms = (time.monotonic() - started) * 1000
        send('break 18'); send('continue'); send('continue')
        values = [send('p ' + name).strip() for name in ('hours', 'base', 'adjustments')]
        assert values == ['40', '1000', '[50.0]'], values
        inspect_ms = (time.monotonic() - started) * 1000
        proc.stdin.write(b'quit\n'); proc.stdin.flush(); proc.wait(timeout=5)
        return {'startup_ms': startup_ms, 'through_inspection_ms': inspect_ms,
                'total_ms': (time.monotonic() - started) * 1000, 'values_match': True,
                'process_cleaned': proc.poll() is not None}
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()


class WaitClient(Client):
    def wait_for(self, predicate, until, seconds=35):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = self.call('debug', request={'action':'wait','session':self.session,
                                                'until':until,'timeout_ms':1000})
            if predicate(value):
                return value
            assert value['error'] is None, value
        raise AssertionError(('debug wait expired', value))

    def action(self, action, ok=True, **args):
        self.call('debug', auto_wait=False, request={'action':action,'session':self.session,**args})
        value = self.wait_for(lambda s:s['result'] is not None, 'ready')
        assert value['result']['ok'] == ok, value
        return value['result'].get('result', value['result'])

    def stop(self):
        self.call('debug', request={'action':'terminate','session':self.session})
        return self.wait_for(lambda s:s['cleaned'], 'cleaned')


def dap(binary, python, root, policy, use_wait=False):
    policy.write_text(json.dumps({'version':1,'configurations':{'test':{
        'argv':[str(python),'-m','debugpy.adapter'],
        'launch':{'program':str(root/'payroll.py'),'python':[str(python)],
                  'console':'internalConsole','justMyCode':True,'subProcess':False},
        'timeout_seconds':20,'session_seconds':120}}}))
    client = (WaitClient if use_wait else Client)(binary, root, policy)
    def until(predicate, target):
        return client.wait_for(predicate, target) if use_wait else client.wait(predicate)
    started = time.monotonic()
    try:
        client.session = client.call('debug_launch', configuration='test')['session']
        state = until(lambda s: s['status'] == 'configuring' or s['cleaned'], 'ready')
        assert not state['cleaned']
        adapter_pid = state['adapter_pid']
        startup_ms = (time.monotonic() - started) * 1000
        client.action('breakpoints', path='payroll.py', lines=[18])
        client.action('continue')
        stop = until(lambda s: s['status'] == 'stopped', 'stopped')
        client.action('continue', thread=stop['stop']['threadId'])
        stop = until(lambda s: s['status'] == 'stopped', 'stopped')
        frames = client.action('stack', thread=stop['stop']['threadId'])['frames']
        scopes = client.action('scopes', handle=frames[0]['handle'])['scopes']
        values = []
        for scope in scopes:
            if scope['handle']:
                values += client.action('variables', handle=scope['handle'])['variables']
        observed = {x['name']: x['value'] for x in values}
        assert observed.get('hours') == '40' and observed.get('base') == '1000' and '50' in observed.get('adjustments', ''), observed
        inspect_ms = (time.monotonic() - started) * 1000
        client.stop()
        cleaned = not alive(adapter_pid)
        return {'startup_ms': startup_ms, 'through_inspection_ms': inspect_ms,
                'total_ms': (time.monotonic() - started) * 1000, 'values_match': True,
                'adapter_cleaned': cleaned, 'mcp_calls':len(client.trace),
                'poll_calls':sum(x['arguments'].get('request', {}).get('action') == 'poll' for x in client.trace),
                'wait_calls':sum(x['arguments'].get('request', {}).get('action') == 'wait' for x in client.trace)}
    finally:
        if client.p.poll() is None:
            if client.session:
                try: client.stop()
                except Exception: pass
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--baseline-binary', type=Path,
                        help='compare prior polling against wait on the supplied binary')
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    python = args.python.absolute()
    baseline = args.baseline_binary.resolve(strict=True) if args.baseline_binary else None
    if args.output.exists() or not python.is_file() or args.repetitions < 1:
        raise SystemExit('use a new output path, an installed debugpy Python, and positive repetitions')
    rows = []
    with tempfile.TemporaryDirectory(prefix='ffsake-debug-poll-probe-') as temporary:
        root = Path(temporary).resolve() / 'project'; root.mkdir()
        shutil.copy(ROOT/'tests/fixtures/debug-python-payroll/payroll.py', root/'payroll.py')
        for repetition in range(1, args.repetitions+1):
            modes = ('dap_poll','dap_wait') if baseline else ('native','dap')
            for mode in (modes if repetition % 2 else modes[::-1]):
                if mode == 'native':
                    value = native(python, root)
                else:
                    value = dap(baseline if mode == 'dap_poll' else binary, python, root,
                                root.parent/'debug.json', use_wait=mode == 'dap_wait')
                rows.append({'repetition': repetition, 'mode': mode, **value})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'repetitions':args.repetitions,'model_calls':0,
        'scope':'one launch, breakpoint twice, inspect hours/base/adjustments, terminate', 'results':rows}, indent=2)+'\n')
    print(json.dumps({'results':rows}, indent=2))


if __name__ == '__main__':
    main()
