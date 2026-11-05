#!/usr/bin/env python3
"""Model-free DAP protocol, authorization and owned-process cleanup checks."""
import json
from pathlib import Path
import sys
import tempfile
import time
from debug_smoke import Client, ROOT, alive


def combined_start_case(binary):
    with tempfile.TemporaryDirectory(prefix='ffsake-dap-combined-') as td:
        base = Path(td).resolve(); root = base / 'root'; root.mkdir()
        (root / 'local.txt').write_text('local')
        policy = base / 'debug.json'
        policy.write_text(json.dumps({'version': 1, 'configurations': {'fake': {
            'argv': [sys.executable, str(ROOT / 'tests/fixtures/dap_adapter.py'), 'normal', str(base)],
            'launch': {}, 'timeout_seconds': 1, 'session_seconds': 30}}}))
        c = Client(binary, root, policy)
        try:
            launched = c.call('debug_launch', configuration='fake',
                              breakpoints=[{'path': 'local.txt', 'lines': 1}],
                              resume=True)
            c.session = launched['session']
            assert launched['status'] == 'stopped', launched
            assert launched['stop']['reason'] == 'breakpoint', launched
            assert launched.get('capabilities'), launched
            stopped = c.wait(lambda s: s['status'] == 'stopped', allow_error=True)
            assert stopped['stop']['reason'] == 'breakpoint', stopped
            calls = [json.loads(line) for line in (base / 'calls.jsonl').read_text().splitlines()]
            commands = [call['command'] for call in calls]
            assert 'setBreakpoints' in commands and 'configurationDone' in commands
            assert 'continue' not in commands
            cleaned = c.stop()
            assert 'capabilities' not in cleaned, cleaned
        finally:
            if c.p.poll() is None:
                if c.session:
                    try:
                        c.stop()
                    except Exception:
                        pass
                c.close()

def main():
    binary = Path(sys.argv[1]).resolve()
    tested = []
    for mode in ('normal', 'output', 'resume_during_inspect', 'invalidate_during_inspect',
                 'bad_correlation', 'malformed', 'crash', 'continue_without_thread',
                 'timeout', 'no_read', 'unsupported', 'cancel', 'eof', 'lifetime', 'reverse', 'running'):
        with tempfile.TemporaryDirectory(prefix='ffsake-dap-protocol-') as td:
            base = Path(td).resolve(); root = base / 'root'; root.mkdir()
            source = base / 'outside.txt'; source.write_text('EXTERNAL_SOURCE_SENTINEL')
            (root / 'local.txt').write_text('local')
            policy = base / 'debug.json'
            policy.write_text(json.dumps({'version': 1, 'configurations': {'fake': {
                'argv': [sys.executable, str(ROOT / 'tests/fixtures/dap_adapter.py'), mode, str(base)],
                'launch': {}, 'timeout_seconds': 1, 'session_seconds': 5 if mode == 'lifetime' else 30}}}))
            c = Client(binary, root, policy)
            try:
                c.call('debug_launch', ok=False, configuration='not-configured')
                assert not (base / 'pids.json').exists()
                c.session = c.call('debug_launch', configuration='fake')['session']
                started = time.monotonic()
                assert c.call('read', path='local.txt')['text'] == 'local'
                assert time.monotonic() - started < .5, 'debug launch blocked ordinary reads'
                if mode in ('malformed', 'crash', 'timeout', 'no_read', 'unsupported', 'bad_correlation'):
                    ended = c.wait(lambda s: s['cleaned'], allow_error=True, seconds=5)
                    assert ended['status'] == 'failed' and ended['error'], ended
                elif mode == 'cancel':
                    c.stop()
                elif mode == 'eof':
                    c.wait(lambda s: s['status'] == 'configuring')
                    c.close()
                else:
                    c.wait(lambda s: s['status'] == 'configuring')
                    if mode == 'continue_without_thread':
                        c.action('continue', inspect=False)
                        c.wait(lambda s: s['status'] == 'stopped')
                        c.action('continue', inspect=False)
                        c.wait(lambda s: s['status'] == 'stopped')
                        calls = [json.loads(line) for line in
                                 (base / 'calls.jsonl').read_text().splitlines()]
                        assert any(call['command'] == 'continue'
                                   and call['arguments']['threadId'] == 1 for call in calls)
                        c.stop()
                    elif mode == 'running':
                        c.action('continue', inspect=False)
                        assert c.call('read', path='local.txt')['text'] == 'local'
                        # Even corrupted/revoked access exports cannot prevent cleanup.
                        (root / '.ffsake/native-access/grants.json').write_text('invalid')
                        c.stop()
                    elif mode == 'lifetime':
                        ended = c.wait(lambda s: s['cleaned'], allow_error=True, seconds=8)
                        assert 'timed out' in ended['error'], ended
                    else:
                        c.action('continue', inspect=False)
                        c.wait(lambda s: s['status'] == 'stopped')
                        frame = c.action('stack', thread=1)['frames'][0]
                        assert frame['source_authorized'] is False and 'handle' not in frame
                        c.call('open_access', path=str(source), kind='file', mode='read_only')
                        frame = c.action('stack', thread=1)['frames'][0]
                        scope = c.action('scopes', handle=frame['handle'])['scopes'][0]['handle']
                        if mode in ('resume_during_inspect', 'invalidate_during_inspect'):
                            result = c.action('variables', ok=False, handle=scope)
                            assert 'execution changed' in result['error'], result
                        else:
                            variables = c.action('variables', handle=scope, limit=2)
                            assert len(variables['variables']) == 2 and variables['truncated']
                            assert len(variables['variables'][0]['value']) == 2048
                            assert c.action('source', handle=frame['source_handle'])['content'] == 'EXTERNAL_SOURCE_SENTINEL'
                            # Queue source, revoke before consuming result: no cached disclosure.
                            c.call('debug', auto_wait=False, request={'action': 'source', 'session': c.session, 'handle': frame['source_handle']})
                            time.sleep(.1)
                            c.call('remove_access', path=str(source))
                            result = c.wait(lambda s: s['result'] is not None)['result']
                            assert not result['ok'] and 'EXTERNAL_SOURCE_SENTINEL' not in json.dumps(result)
                            c.action('scopes', ok=False, handle=frame['handle'])
                            c.call('open_access', path=str(source), kind='file', mode='read_only')
                            # The asynchronous wait path also rechecks revocation before delivery.
                            c.call('debug', auto_wait=False, request={'action':'source', 'session':c.session, 'handle':frame['source_handle']})
                            time.sleep(.1)
                            c.call('remove_access', path=str(source))
                            waited = c.call('debug', request={'action':'wait', 'session':c.session,
                                'until':'ready', 'timeout_ms':1000})['result']
                            assert not waited['ok'] and 'EXTERNAL_SOURCE_SENTINEL' not in json.dumps(waited)
                            c.call('open_access', path=str(source), kind='file', mode='read_only')
                            c.action('next', thread=1, inspect=False)
                            c.wait(lambda s: s['status'] == 'stopped')
                            assert 'stale' in c.action('scopes', ok=False, handle=frame['handle'])['error']
                            assert 'unsupported' in c.action('pause', ok=False, thread=1, inspect=False)['error']
                        if mode == 'output':
                            polls = [t['result']['result'] for t in c.trace
                                     if t['tool'] == 'debug' and t['arguments']['request']['action'] == 'poll']
                            assert any(s['output_truncated'] for s in polls)
                            assert all(len(s['output'].encode()) <= 65536 for s in polls)
                        c.stop()
                        if mode == 'reverse':
                            assert (base / 'reverse-rejected').read_text() == 'yes'
                deadline = time.monotonic() + 2
                while not (base / 'pids.json').exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                if (base / 'pids.json').exists():
                    pids = json.loads((base / 'pids.json').read_text())
                    while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
                        time.sleep(.02)
                    assert not any(alive(pid) for pid in pids), (mode, 'owned process leaked', pids)
                tested.append(mode)
            finally:
                if c.p.poll() is None:
                    if c.session:
                        c.stop()
                    c.close()
    combined_start_case(binary)
    tested.append('combined_start')
    print(json.dumps({'protocol_cases': tested, 'cleanup': True, 'authorization': True,
                      'stale_handles': True, 'nonblocking_editing': True}, indent=2))


if __name__ == '__main__':
    main()
