#!/usr/bin/env python3
"""Run real adapter bug investigations via FFSAKE MCP, retaining failed traces."""
import argparse, json, os, shutil, subprocess, sys, tempfile, time, traceback
from pathlib import Path
from debug_smoke import Client, ROOT, alive


def main():
    p=argparse.ArgumentParser();p.add_argument('binary',type=Path);p.add_argument('--kind',choices=('python','cpp'),required=True);p.add_argument('--adapter',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
    result={'adapter_kind':a.kind,'adapter':str(a.adapter),'result':'failed','trace':[]}
    with tempfile.TemporaryDirectory(prefix='ffsake-debug-compat-') as td:
        base=Path(td).resolve(); root=base/'project'; root.mkdir(); policy=base/'debug.json';client=None
        try:
            if a.kind=='python':
                shutil.copy(ROOT/'tests/fixtures/debug-python-payroll/payroll.py',root/'payroll.py')
                source='payroll.py';line=18
                before=subprocess.run([str(a.adapter),str(root/source)],capture_output=True,text=True,timeout=10)
                argv=[os.path.abspath(a.adapter),'-m','debugpy.adapter']
                launch={'program':str(root/source),'python':[os.path.abspath(a.adapter)],'console':'internalConsole','justMyCode':True,'subProcess':False}
            else:
                shutil.copy(ROOT/'tests/fixtures/debug-cpp-list/main.cpp',root/'main.cpp')
                subprocess.run(['c++','-g','-O0','-c',str(root/'main.cpp'),'-o',str(root/'main.o')],check=True)
                subprocess.run(['c++',str(root/'main.o'),'-o',str(root/'demo')],check=True)
                source='main.cpp';line=10
                before=subprocess.run([str(root/'demo')],capture_output=True,text=True,timeout=10)
                argv=[str(a.adapter.resolve())];launch={'program':str(root/'demo'),'cwd':str(root),'stopOnEntry':False}
            result['before']={'exit_code':before.returncode,'stdout':before.stdout}; assert before.returncode==1
            policy.write_text(json.dumps({'version':1,'configurations':{'test':{'argv':argv,'launch':launch,'timeout_seconds':20,'session_seconds':120}}}))
            client=Client(a.binary.resolve(),root,policy);client.session=client.call('debug_launch',configuration='test')['session']
            configured=client.wait(lambda s:s['status']=='configuring' or s['cleaned'],allow_error=True)
            assert not configured['cleaned'], configured
            pid=configured['adapter_pid'];client.action('breakpoints',path=source,lines=[line]);client.action('continue')
            stopped=client.wait(lambda s:s['status']=='stopped',allow_error=True);assert stopped['stop']['reason']=='breakpoint',stopped
            tid=stopped['stop']['threadId'];stack=client.action('stack',thread=tid)['frames'];frame=next(x for x in stack if x.get('handle') and x['source_path'].endswith(source))
            scopes=client.action('scopes',handle=frame['handle'])['scopes'];variables=[]
            for scope in scopes:
                if scope['handle']:variables+=client.action('variables',handle=scope['handle'])['variables']
            result.update({'stop':stopped['stop'],'frame':frame,'variables':variables})
            client.action('next',thread=tid);client.wait(lambda s:s['status']=='stopped',allow_error=True)
            client.action('scopes',ok=False,handle=frame['handle'])
            client.action('continue',thread=tid)
            if a.kind=='python':
                second=client.wait(lambda s:s['status']=='stopped',allow_error=True)
                second_frame=client.action('stack',thread=second['stop']['threadId'])['frames'][0]
                second_scopes=client.action('scopes',handle=second_frame['handle'])['scopes']
                second_vars=[]
                for scope in second_scopes:
                    if scope['handle']:second_vars+=client.action('variables',handle=scope['handle'])['variables']
                result['second_variables']=second_vars
                assert any(v['name']=='adjustments' and '50' in v['value'] for v in second_vars),second_vars
                client.action('breakpoints',path=source,lines=[]);client.action('continue',thread=second['stop']['threadId'])
            else:
                nextstop=client.wait(lambda s:s['status']=='stopped',allow_error=True)
                client.action('breakpoints',path=source,lines=[]);client.action('continue',thread=nextstop['stop']['threadId'])
            client.wait(lambda s:s['cleaned'],allow_error=True)
            assert not alive(pid),'adapter leaked'
            debuggees={event['processId'] for call in client.trace for event in call['result'].get('result',{}).get('events',[]) if event.get('event')=='process' and event.get('processId')}
            assert debuggees and not any(alive(pid) for pid in debuggees),('debuggee cleanup',debuggees)
            result.update({'result':'passed','adapter_cleaned':True,'debuggee_pids':sorted(debuggees),'debuggees_cleaned':True})
        except Exception as e:
            result['error']=repr(e);result['traceback']=traceback.format_exc()
        finally:
            if client:
                if client.p.poll() is None:
                    if client.session:
                        try:client.stop()
                        except Exception:pass
                    try:client.close()
                    except Exception:pass
                result['trace']=client.trace
            a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('trace','traceback')},indent=2))
    return 0 if result['result']=='passed' else 1
if __name__=='__main__':sys.exit(main())
