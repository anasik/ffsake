#!/usr/bin/env python3
"""Real MCP live task output, retention, UTF-8, cancellation and cleanup checks."""
import base64,json,os,subprocess,sys,tempfile,time
from pathlib import Path
from workflow_smoke import MCP
from project_tasks_smoke import wait


def main():
    binary=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-task-output-') as td:
        root=Path(td).resolve();(root/'note.txt').write_text('editing works\n');m=MCP(binary,root)
        def register(name,code,timeout=10):
            d={'label':name,'type':'process','command':sys.executable,'args':['-u','-c',code]}
            m.call('ffsake_task_register',{'definition':d,'timeout_seconds':timeout,'output_bytes':128})
        def start(name):return m.call('ffsake_run_task',{'name':name})['job']
        def read(job,stream='stdout',cursor=0,limit=64,wait_ms=1000):
            return m.call('ffsake_task_output',{'job':job,'stream':stream,'cursor':cursor,'max_bytes':limit,'wait_ms':wait_ms})
        try:
            register('Live',"import sys,time;sys.stdout.write('first\\n');sys.stdout.flush();time.sleep(1);sys.stdout.buffer.write('A🚀B'.encode());sys.stdout.flush();time.sleep(.2);print('done')")
            job=start('Live');first=read(job);assert first['text']=='first\n' and not first['complete'],first
            assert 'editing works' in json.dumps(m.call('ffsake_read',{'path':'note.txt'}))
            cursor=first['next_cursor']; chunks=[]
            while True:
                result=read(job,cursor=cursor,limit=4);chunks.append(base64.b64decode(result['data_base64']));cursor=result['next_cursor']
                if result['complete'] and cursor==result['total_bytes']:break
            assert b''.join(chunks)=='A🚀Bdone\n'.encode(),chunks
            assert wait(m,job)['success'] and result['drained'] and result['dropped_bytes']==0
            broken=root/'bad-executable';broken.write_text('#!/no/such/ffsake-interpreter\n');broken.chmod(0o755)
            m.call('ffsake_task_register',{'definition':{'label':'BadLaunch','type':'process','command':str(broken)}})
            m.call('ffsake_run_task',{'name':'BadLaunch'},False)
            assert read(job,cursor=0)['text'].startswith('first')
            register('Split',"import sys,time;sys.stdout.buffer.write(b'\\xf0\\x9f');sys.stdout.flush();time.sleep(.4);sys.stdout.buffer.write(b'\\x9a\\x80');sys.stdout.flush();sys.stderr.write('separate stderr');sys.stderr.flush()")
            split=start('Split');started=time.monotonic();out=read(split,limit=4)
            assert out['text']=='🚀' and not out['text_is_lossy'] and time.monotonic()-started>=.25,out
            wait(m,split);assert read(split,'stderr')['text']=='separate stderr'
            register('Overflow',"import sys;sys.stdout.write('x'*4300000+'FAILURE SUMMARY\\n');sys.stdout.flush();sys.exit(7)")
            job2=start('Overflow');status=wait(m,job2,timeout=20);assert status['exit_code']==7 and status['output_truncated']
            compact=m.call('ffsake_task_status',{'job':job2});assert 'stdout' not in compact and 'stderr' not in compact and compact['output_included'] is False
            assert 'stdout' in status and len(status['stdout'])==128
            overflow=read(job2);assert overflow['cursor_expired'] and overflow['dropped_bytes']>0
            tail=read(job2,cursor=overflow['next_cursor']+overflow['retained_bytes']-64,limit=64);assert 'FAILURE SUMMARY' in tail['text'] and tail['drained']
            m.call('ffsake_task_output',{'job':job,'stream':'stdout'},False)
            code="import signal,sys,time,subprocess,pathlib;signal.signal(signal.SIGTERM,lambda *_:(print('received term',flush=True),sys.exit(0)));p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);pathlib.Path('child.pid').write_text(str(p.pid));print('cancel me',flush=True);time.sleep(60)"
            register('Cancel',code);j=start('Cancel');out=read(j);assert 'cancel me' in out['text'];pid=int((root/'child.pid').read_text());m.call('ffsake_task_cancel',{'job':j});done=wait(m,j);assert done['cancelled'] and done['output_drained']
            assert done['exit_code']==0 and done['signal'] is None and not done['success']
            combined=read(j,cursor=out['next_cursor']);assert 'received term' in combined['text'],combined
            for _ in range(50):
                ps=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True)
                if not ps.stdout.strip() or ps.stdout.lstrip().startswith('Z'):break
                time.sleep(.05)
            else:raise AssertionError('child survived cancellation')
            register('IgnoreTerm',"import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('ready',flush=True);time.sleep(60)")
            j=start('IgnoreTerm');assert 'ready' in read(j)['text'];m.call('ffsake_task_cancel',{'job':j});done=wait(m,j)
            assert done['cancelled'] and done['signal']==9 and done['output_drained']
            register('Sleep',"import time;print('sleeping',flush=True);time.sleep(60)")
            j=start('Sleep');assert 'sleeping' in read(j)['text'];m.call('ffsake_task_cancel',{'job':j});done=wait(m,j)
            assert done['cancelled'] and done['exit_code'] is None and done['signal']==15 and not done['success']
            register('Timeout',"import time;print('before timeout',flush=True);time.sleep(60)",1);j=start('Timeout');assert 'before timeout' in read(j)['text'];done=wait(m,j);assert done['timed_out'] and done['output_drained']
            print(json.dumps({'live_output_and_edit':True,'utf8_cursor_pagination':True,'failure_tail_and_overflow':True,'expired_job':True,'graceful_cancellation':True,'descendant_cleanup':True,'timeout_drained':True},indent=2))
        finally:m.close()
if __name__=='__main__':main()
