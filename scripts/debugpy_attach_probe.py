#!/usr/bin/env python3
"""Probe debugpy's declared detach capability without weakening attach safeguards."""
import argparse,json,subprocess,tempfile,time
from pathlib import Path
from debug_smoke import Client

def main():
 p=argparse.ArgumentParser();p.add_argument('binary',type=Path);p.add_argument('--python',required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
 with tempfile.TemporaryDirectory(prefix='ffsake-debugpy-attach-') as td:
  root=Path(td).resolve(); policy=root/'debug.json';policy.write_text(json.dumps({'version':1,'configurations':{'local':{'argv':[a.python,'-m','debugpy.adapter'],'attach':{'pid_field':'processId'},'timeout_seconds':10}}}))
  target=subprocess.Popen([a.python,'-c','import time; time.sleep(120)'],start_new_session=True); c=Client(a.binary.resolve(),root,policy)
  try:
   c.session=c.call('debug_attach',configuration='local',pid=target.pid)['session']
   result=c.wait(lambda s:s['cleaned'],allow_error=True,seconds=20)
   assert result['status']=='failed' and 'supportTerminateDebuggee' in result['error'],result
   assert target.poll() is None
   c.close();assert target.poll() is None
   a.report.write_text(json.dumps({'result':'blocked','error':result['error'],'capabilities':result['capabilities'],'target_alive':True,'trace':c.trace},indent=2))
   print(json.dumps({'blocked':result['error'],'target_alive':True}))
  finally:
   if c.p.poll() is None:c.close()
   target.terminate();target.wait(timeout=5)
if __name__=='__main__':main()
