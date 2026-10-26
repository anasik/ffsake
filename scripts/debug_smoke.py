#!/usr/bin/env python3
"""Reproducible real C# DAP check. Supply an installed netcoredbg; never downloads it."""
import argparse
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


class Client:
    def __init__(self, binary, root, policy):
        self.p = subprocess.Popen([str(binary), str(root), '--debug', str(policy)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        self.n = 0
        self.trace = []
        self.session = None

    def call(self, name, ok=True, **arguments):
        self.n += 1
        started = time.monotonic()
        self.p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.n, 'method': 'tools/call',
                                      'params': {'name': 'ffsake_' + name, 'arguments': arguments}}) + '\n')
        self.p.stdin.flush()
        assert select.select([self.p.stdout], [], [], 5)[0], 'MCP blocked for >5s'
        line = self.p.stdout.readline()
        assert line, self.p.stderr.read()
        result = json.loads(json.loads(line)['result']['content'][0]['text'])
        self.trace.append({'tool': name, 'arguments': arguments, 'result': result,
                           'elapsed_ms': round((time.monotonic() - started) * 1000, 2)})
        assert result['ok'] == ok, result
        return result.get('result', result)

    def launch(self, configuration):
        result = self.call('debug_launch', configuration=configuration)
        self.session = result['session']
        return self.wait(lambda s: s['status'] == 'configuring')

    def poll(self):
        return self.call('debug', request={'action': 'poll', 'session': self.session})

    def wait(self, predicate, seconds=35, allow_error=False):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = self.poll()
            if not allow_error:
                assert value['error'] is None, value
            if predicate(value):
                return value
            time.sleep(.025)
        raise AssertionError(('debug wait expired', value))

    def action(self, action, ok=True, **args):
        self.call('debug', request={'action': action, 'session': self.session, **args})
        value = self.wait(lambda s: s['result'] is not None, allow_error=not ok)
        assert value['result']['ok'] == ok, value
        return value['result'].get('result', value['result'])

    def stop(self):
        self.call('debug', request={'action': 'terminate', 'session': self.session})
        return self.wait(lambda s: s['cleaned'], allow_error=True)

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)
        assert self.p.returncode == 0, self.p.stderr.read()


def alive(pid):
    result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--adapter', required=True, type=Path)
    parser.add_argument('--dotnet', default=shutil.which('dotnet'))
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    assert args.dotnet and args.adapter.is_file(), 'install .NET 8 SDK and netcoredbg first'
    with tempfile.TemporaryDirectory(prefix='ffsake-dap-real-') as td:
        base = Path(td).resolve()
        root = base / 'project'
        shutil.copytree(ROOT / 'tests/fixtures/debug-fibonacci', root)
        subprocess.run([args.dotnet, 'build', str(root), '-v', 'quiet'], check=True,
                       env={**os.environ, 'DOTNET_CLI_TELEMETRY_OPTOUT': '1'})
        program = root / 'bin/Debug/net8.0/Fibonacci.dll'
        before = subprocess.run([args.dotnet, str(program)], capture_output=True, text=True)
        assert before.returncode == 1 and before.stdout.strip() == '-1323752223', before
        policy = base / 'debug.json'
        policy.write_text(json.dumps({'version': 1, 'configurations': {'sample': {
            'argv': [str(args.adapter.resolve()), '--interpreter=vscode'],
            'launch': {'program': str(program), 'cwd': str(root), 'stopAtEntry': False},
            'timeout_seconds': 20}}}))
        client = Client(args.binary.resolve(), root, policy)
        try:
            configured = client.launch('sample')
            pid = configured['adapter_pid']
            bp = client.action('breakpoints', path=str(root / 'Program.cs'), lines=[12])
            assert len(bp['breakpoints']) == 1 and bp['breakpoints'][0]['line'] == 12, bp
            client.action('continue')
            stopped = client.wait(lambda s: s['status'] == 'stopped')
            assert stopped['stop']['reason'] == 'breakpoint', stopped
            thread = stopped['stop']['threadId']
            frames = client.action('stack', thread=thread)['frames']
            assert frames[0]['line'] == 12 and frames[0]['source_path'] == str(root / 'Program.cs'), frames
            old = frames[0]['handle']
            scopes = client.action('scopes', handle=old)['scopes']
            variables = []
            for scope in scopes:
                if scope['handle']:
                    variables += client.action('variables', handle=scope['handle'])['variables']
            assert any(v['name'] == 'actual' and v['value'] == '-1323752223' for v in variables), variables
            # Ordinary editing remains available while the debuggee is stopped.
            preview = client.call('preview_create', path='debug-note.txt', content='host editing works\n')
            client.call('apply', handle=preview['handle'])
            step_frames = []
            for operation in ('step_in', 'next', 'step_out'):
                client.action(operation, thread=thread)
                step = client.wait(lambda s: s['status'] == 'stopped')
                assert step['stop']['reason'] == 'step', step
                frame = client.action('stack', thread=thread)['frames'][0]
                assert ('Print' if operation != 'step_out' else 'Main') in frame['name'], frame
                step_frames.append({'operation': operation, 'frame': frame})
                client.action('scopes', ok=False, handle=old)
            client.action('continue', thread=thread)
            client.wait(lambda s: s['cleaned'])
            assert not alive(pid), 'adapter process leaked'
            # A second launch is actively terminated, without reaching natural exit.
            second = client.launch('sample')
            client.action('breakpoints', path=str(root / 'Program.cs'), lines=[12])
            client.action('continue')
            client.wait(lambda s: s['status'] == 'stopped')
            client.stop()
            assert not alive(second['adapter_pid']), 'terminated adapter leaked'
            debuggees = {event['processId'] for call in client.trace
                         for event in call['result'].get('result', {}).get('events', [])
                         if event.get('event') == 'process' and event.get('processId')}
            assert len(debuggees) == 2, ('missing real debuggee identities', debuggees)
            assert not any(alive(pid) for pid in debuggees), ('debuggee leaked', debuggees)
            result = {'debuggee_pids': sorted(debuggees), 'step_frames': step_frames, 'adapter': str(args.adapter), 'observed_actual': -1323752223,
                      'external_exit': before.returncode, 'breakpoint': True,
                      'step_in_over_out': True, 'editing_while_stopped': True,
                      'stale_handles_rejected': True, 'cleanup': True, 'trace': client.trace}
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(result, indent=2))
            print(json.dumps({k: v for k, v in result.items() if k != 'trace'}, indent=2))
        finally:
            if client.session:
                client.stop()
            client.close()


if __name__ == '__main__':
    main()
