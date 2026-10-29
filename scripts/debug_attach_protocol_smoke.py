#!/usr/bin/env python3
"""Controlled attach cleanup checks against an independent disposable process."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
from debug_smoke import Client, ROOT, alive

def main():
    binary=Path(sys.argv[1]).resolve(); outcomes=[]
    for mode in ('attach','normal','unsupported','timeout'):
        for exit_mode in (('cancel','eof','lifetime') if mode=='attach' else ('failure',)):
            with tempfile.TemporaryDirectory(prefix='ffsake-attach-protocol-') as td:
                base=Path(td).resolve();root=base/'project';root.mkdir()
                policy=base/'debug.json';policy.write_text(json.dumps({'version':1,'configurations':{'local':{'argv':[sys.executable,str(ROOT/'tests/fixtures/dap_adapter.py'),mode,str(base)],'attach':{'pid_field':'pid'},'timeout_seconds':1,'session_seconds':5}}}))
                target=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],start_new_session=True)
                c=Client(binary,root,policy)
                try:
                    c.call('debug_launch',ok=False,configuration='local')
                    c.session=c.call('debug_attach',configuration='local',pid=target.pid)['session']
                    if exit_mode=='failure':
                        ended=c.wait(lambda s:s['cleaned'],allow_error=True,seconds=8)
                        assert ended['status']=='failed' and ended['error']
                        if mode=='normal': assert 'supportTerminateDebuggee' in ended['error']
                    else:
                        c.wait(lambda s:s['status']=='configuring')
                        if exit_mode=='cancel': c.stop()
                        elif exit_mode=='eof': c.close()
                        else: c.wait(lambda s:s['cleaned'],allow_error=True,seconds=8)
                    if c.p.poll() is None:c.close()
                    assert alive(target.pid), (mode,exit_mode)
                    if (base/'calls.jsonl').exists():
                        calls=[json.loads(x) for x in (base/'calls.jsonl').read_text().splitlines()]
                        for m in calls:
                            if m['command']=='disconnect':assert m['arguments']['terminateDebuggee'] is False
                    for pid in json.loads((base/'pids.json').read_text()):
                        assert not alive(pid),(mode,exit_mode,pid)
                    outcomes.append(mode+':'+exit_mode)
                finally:
                    if c.p.poll() is None:c.close()
                    target.terminate();target.wait(timeout=5)
    print(json.dumps({'cases':outcomes,'target_survives':True,'adapter_processes_cleaned':True}))
if __name__=='__main__':main()
