#!/usr/bin/env python3
"""Exercise concurrent MCP debug waits, ordinary reads and cancellation."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from debug_smoke import Client, ROOT, alive


pending = b''

def cleanup_fixture(pid):
    if pid is None:
        return
    # Signal only the exact disposable fixture, never a process matched by name.
    info = subprocess.run(['ps','-o','command=','-p',str(pid)], capture_output=True, text=True)
    if str(ROOT/'tests/fixtures/dap_adapter.py') not in info.stdout:
        return
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

def read_reply(proc, timeout=3):
    global pending
    deadline = time.monotonic() + timeout
    while b'\n' not in pending:
        remaining = max(0, deadline - time.monotonic())
        assert select.select([proc.stdout], [], [], remaining)[0], 'MCP reply timed out'
        chunk = os.read(proc.stdout.fileno(), 4096)
        assert chunk, 'MCP closed before reply'
        pending += chunk
    line, pending = pending.split(b'\n', 1)
    result = json.loads(line)
    return result['id'], json.loads(result['result']['content'][0]['text'])


def main():
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-debug-wait-') as td:
        base = Path(td).resolve(); root = base/'root'; root.mkdir()
        (root/'local.txt').write_text('ok')
        policy = base/'debug.json'
        policy.write_text(json.dumps({'version':1,'configurations':{'fake':{
            'argv':[sys.executable,str(ROOT/'tests/fixtures/dap_adapter.py'),'running',str(base)],
            'launch':{},'timeout_seconds':1,'session_seconds':30}}}))
        c = Client(binary, root, policy)
        adapter_pid = None
        try:
            c.session = c.call('debug_launch', configuration='fake')['session']
            state = c.call('debug', request={'action':'wait','session':c.session,'until':'ready','timeout_ms':1000})
            if state['wait_timed_out']:
                state = c.wait(lambda s: s['status'] == 'configuring')
            assert state['status'] == 'configuring', state
            adapter_pid = state['adapter_pid']
            c.action('continue')
            c.call('debug', ok=False, request={'action':'wait','session':c.session,'until':'stopped','timeout_ms':1001})
            c.call('debug', ok=False, request={'action':'wait','session':c.session+1,'until':'stopped','timeout_ms':100})
            # The read must return while a full-second wait remains pending.
            c.n += 1; wait_id = c.n
            wait = {'jsonrpc':'2.0','id':wait_id,'method':'tools/call','params':{'name':'ffsake_debug','arguments':{'request':{'action':'wait','session':c.session,'until':'stopped','timeout_ms':1000}}}}
            c.n += 1; read_id = c.n
            read = {'jsonrpc':'2.0','id':read_id,'method':'tools/call','params':{'name':'ffsake_read','arguments':{'path':'local.txt'}}}
            c.n += 1; duplicate_id = c.n
            duplicate = dict(wait, id=duplicate_id)
            started = time.monotonic()
            c.p.stdin.write(json.dumps(wait)+'\n'+json.dumps(duplicate)+'\n'+json.dumps(read)+'\n'); c.p.stdin.flush()
            immediate = dict(read_reply(c.p) for _ in range(2))
            assert set(immediate) == {read_id, duplicate_id}, immediate
            assert immediate[read_id]['result']['text'] == 'ok', immediate
            assert not immediate[duplicate_id]['ok'] and 'pending' in immediate[duplicate_id]['error']
            assert time.monotonic()-started < .75, 'debug wait blocked ordinary read'
            ident, response = read_reply(c.p, 3)
            assert ident == wait_id and response['result']['wait_timed_out'] is True, response
            assert .75 <= time.monotonic()-started < 2.5
            # Termination may proceed even when a wait is pending, and wakes it.
            c.n += 1; wait_id = c.n; wait['id'] = wait_id
            c.n += 1; stop_id = c.n
            stop = {'jsonrpc':'2.0','id':stop_id,'method':'tools/call','params':{'name':'ffsake_debug','arguments':{'request':{'action':'terminate','session':c.session}}}}
            c.p.stdin.write(json.dumps(wait)+'\n'+json.dumps(stop)+'\n'); c.p.stdin.flush()
            replies = dict(read_reply(c.p, 3) for _ in range(2))
            assert set(replies)=={wait_id,stop_id}, replies
            assert replies[stop_id]['ok'] and replies[wait_id]['ok']
            assert not replies[wait_id]['result']['wait_timed_out']
            c.wait(lambda s:s['cleaned'],allow_error=True)
            assert not alive(adapter_pid), 'owned adapter survived cleanup'
            c.close()
        finally:
            if c.p.poll() is None:
                try: c.stop(); c.close()
                except Exception:
                    c.p.kill(); c.p.wait()
            cleanup_fixture(adapter_pid)
        # EOF during a pending wait remains bounded and cleans up the adapter.
        c = Client(binary, root, policy)
        adapter_pid = None
        try:
            c.session = c.call('debug_launch', configuration='fake')['session']
            state = c.wait(lambda s:s['status']=='configuring')
            adapter_pid = state['adapter_pid']
            c.action('continue')
            c.n += 1
            c.p.stdin.write(json.dumps({'jsonrpc':'2.0','id':c.n,'method':'tools/call',
                'params':{'name':'ffsake_debug','arguments':{'request':{'action':'wait',
                'session':c.session,'until':'stopped','timeout_ms':1000}}}})+'\n')
            c.p.stdin.close()
            c.p.wait(timeout=5)
            assert c.p.returncode == 0 and not alive(adapter_pid), 'EOF left owned adapter running'
        finally:
            if c.p.poll() is None:
                c.p.kill(); c.p.wait()
            cleanup_fixture(adapter_pid)
        # A host that stops reading a large wait response must not prevent cleanup.
        policy.write_text(json.dumps({'version':1,'configurations':{'fake':{
            'argv':[sys.executable,str(ROOT/'tests/fixtures/dap_adapter.py'),'output',str(base)],
            'launch':{},'timeout_seconds':1,'session_seconds':30}}}))
        c = Client(binary, root, policy)
        adapter_pid = None
        try:
            c.session = c.call('debug_launch', configuration='fake')['session']
            state = c.wait(lambda s:s['status']=='configuring')
            adapter_pid = state['adapter_pid']
            c.call('debug', request={'action':'continue','session':c.session})
            c.n += 1
            c.p.stdin.write(json.dumps({'jsonrpc':'2.0','id':c.n,'method':'tools/call',
                'params':{'name':'ffsake_debug','arguments':{'request':{'action':'wait',
                'session':c.session,'until':'ready','timeout_ms':1000}}}})+'\n')
            c.p.stdin.close()
            c.p.wait(timeout=5)
            assert c.p.returncode == 0 and not alive(adapter_pid), 'blocked output prevented adapter cleanup'
        finally:
            if c.p.poll() is None:
                c.p.kill(); c.p.wait()
            cleanup_fixture(adapter_pid)
    print(json.dumps({'bounded_timeout':True,'concurrent_read':True,'termination':True,'cleanup':True,'invalid_wait_rejected':True}))

if __name__=='__main__': main()
