#!/usr/bin/env python3
"""Exercise cached asynchronous update notices using actual MCP calls, without network."""
import json,os,select,shutil,subprocess,sys,tempfile,time
from pathlib import Path

def main():
 binary=Path(sys.argv[1]).resolve()
 with tempfile.TemporaryDirectory(prefix='ffsake-notice-') as td:
  root=Path(td).resolve();project=root/'project';project.mkdir();prefix=root/'install';generation=prefix/'versions/release-v0.1.0-alpha.1-channel';generation.mkdir(parents=True)
  shutil.copy2(binary,generation/'ffsake-mcp');(generation/'manifest.json').write_text(json.dumps({'release_channel':'alpha','release_version':'v0.1.0-alpha.1','pinned':False}))
  (prefix/'update-cache.json').write_text(json.dumps({'checked_at':int(time.time()),'latest':'v0.1.0-alpha.2'}))
  (prefix/'current').symlink_to('versions/release-v0.1.0-alpha.1-channel')
  def session(pinned=False,offline=False):
   (generation/'manifest.json').write_text(json.dumps({'release_channel':'alpha','release_version':'v0.1.0-alpha.1','pinned':pinned}))
   if offline:(prefix/'update-cache.json').unlink()
   env=os.environ.copy()
   if offline:env.update(HTTPS_PROXY='http://127.0.0.1:1',HTTP_PROXY='http://127.0.0.1:1',ALL_PROXY='http://127.0.0.1:1',NO_PROXY='')
   p=subprocess.Popen([str(prefix/'current/ffsake-mcp'),str(project),'--profile','core'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
   def call(i):
    p.stdin.write(json.dumps({'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':'ffsake_list','arguments':{'path':'.'}}})+'\n');p.stdin.flush()
    assert select.select([p.stdout],[],[],5)[0]
    return json.loads(json.loads(p.stdout.readline())['result']['content'][0]['text'])
   try:
    notices=[]
    for i in range(1,8):
     response=call(i);assert response['ok'];notices.append(response.get('update_notice'));time.sleep(.05)
    if pinned or offline:assert not any(notices),notices
    else:
     assert sum(x is not None for x in notices)==1,notices
     n=next(x for x in notices if x);assert n['installed_version']=='v0.1.0-alpha.1' and n['latest_version']=='v0.1.0-alpha.2'
   finally:p.stdin.close();p.wait(timeout=5);p.stdout.close();p.stderr.close()
  session();session(True);session(offline=True)
  print(json.dumps({'async_cached_notice':True,'once_per_session':True,'pin_suppresses_notice':True,'offline_nonblocking':True,'mcp_calls_worked':True}))
if __name__=='__main__':main()
