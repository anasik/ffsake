#!/usr/bin/env python3
"""Real local PID attachment and detach checks; uses an installed netcoredbg."""
import argparse, json, os, shutil, subprocess, tempfile, time
from pathlib import Path
from debug_smoke import Client, ROOT

def main():
    p=argparse.ArgumentParser();p.add_argument('binary',type=Path);p.add_argument('--adapter',type=Path,required=True);p.add_argument('--report',type=Path);a=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='ffsake-attach-') as td:
        base=Path(td).resolve(); root=base/'project';shutil.copytree(ROOT/'tests/fixtures/debug-fibonacci',root)
        (root/'Program.cs').write_text('''using System;\nusing System.Runtime.InteropServices;\nusing System.Threading;\nclass Program {\n    [DllImport("libc", EntryPoint="prctl")] static extern int Prctl(int option, ulong arg2, ulong arg3, ulong arg4, ulong arg5);\n    static void Main() {\n        if (OperatingSystem.IsLinux() && Prctl(0x59616d61, ulong.MaxValue, 0, 0, 0) != 0) throw new Exception("PR_SET_PTRACER failed");\n        int count = 0;\n        while (true) {\n            count = Tick(count);\n            Thread.Sleep(100);\n        }\n    }\n    static int Tick(int value) {\n        return value + 1;\n    }\n}\n''')
        subprocess.run(['dotnet','build',str(root),'-v','quiet'],check=True,stdout=subprocess.DEVNULL)
        dll=root/'bin/Debug/net8.0/Fibonacci.dll'; policy=base/'debug.json'
        policy.write_text(json.dumps({'version':1,'configurations':{'local':{'argv':[str(a.adapter.resolve()),'--interpreter=vscode'],'attach':{'pid_field':'processId'},'timeout_seconds':20,'session_seconds':60}}}))
        target=subprocess.Popen(['dotnet',str(dll)],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        c=None
        try:
            time.sleep(1);assert target.poll() is None
            c=Client(a.binary.resolve(),root,policy)
            c.call('debug_attach',ok=False,configuration='local',pid=1)
            c.session=c.call('debug_attach',configuration='local',pid=2147483647)['session']
            failed=c.wait(lambda state:state['cleaned'],allow_error=True)
            assert failed['status']=='failed' and 'unavailable or inaccessible' in failed['error'],failed
            c.session=c.call('debug_attach',configuration='local',pid=target.pid)['session']
            c.wait(lambda s:s['status']=='configuring')
            c.action('breakpoints',path='Program.cs',lines=[10])
            c.action('continue')
            stopped=c.wait(lambda s:s['status']=='stopped')
            tid=stopped['stop']['threadId'];stack=c.action('stack',thread=tid)
            frame=next(f for f in stack['frames'] if f.get('handle') and f['source_path'].endswith('Program.cs'))
            scopes=c.action('scopes',handle=frame['handle'])
            variables=[]
            for scope in scopes['scopes']:
                variables += c.action('variables',handle=scope['handle'])['variables']
            assert any(v['name']=='count' for v in variables),variables
            c.action('step_in',thread=tid);c.wait(lambda s:s['status']=='stopped')
            c.action('scopes',ok=False,handle=frame['handle'])
            c.action('step_out',thread=tid);c.wait(lambda s:s['status']=='stopped')
            c.action('next',thread=tid);c.wait(lambda s:s['status']=='stopped')
            c.action('breakpoints',path='Program.cs',lines=[])
            c.action('continue',thread=tid)
            c.action('pause',thread=tid);c.wait(lambda s:s['status']=='stopped')
            c.stop();assert target.poll() is None
            c.close();assert target.poll() is None
            if a.report:a.report.write_text(json.dumps({'adapter':str(a.adapter),'configuration':'local processId','target_pid':target.pid,'stop':stopped['stop'],'variables':variables,'target_alive_after_detach_and_server_exit':True,'trace':c.trace},indent=2))
            print(json.dumps({'stopped':stopped['stop'],'variables':variables,'target_alive_after_detach_and_server_exit':True}))
        finally:
            if c and c.p.poll() is None:
                if c.session:c.stop()
                c.close()
            target.terminate();target.wait(timeout=5)
if __name__=='__main__':main()
