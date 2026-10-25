#!/usr/bin/env python3
"""Actual core MCP Git review and explicitly configured build/test execution."""
import json,os,subprocess,sys,tempfile,time
from pathlib import Path
from semantic_smoke import MCP as BaseMCP
from agent_benchmark_suite import git_init
class MCP(BaseMCP):
    def __init__(self,binary,root,policy=None,env=None):
        args=[str(binary),str(root),'--profile','core']
        if policy:args+=['--tasks',str(policy)]
        self.proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env);self.number=0

def main():
    binary=Path(sys.argv[1] if len(sys.argv)>1 else 'target/debug/ffsake-mcp').resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-workflow-') as td:
        base=Path(td);root=base/'repo';root.mkdir()
        (root/'tracked.txt').write_text('original\n');(root/'user.txt').write_text('user baseline\n')
        (root/'main.rs').write_text('fn main() { assert_eq!(2+3,5); }\n')
        (root/'weird[1].txt').write_text('before\n')
        git_init(root)
        def git(*args):return subprocess.check_output(['git',*args],cwd=root)
        (root/'user.txt').write_text('user staged\n');git('add','user.txt');(root/'user.txt').write_text('user staged and working\n')
        (root/'tracked.txt').write_text('working\n');(root/'weird[1].txt').write_text('after\n')
        (root/'untracked.txt').write_text('keep\n');(root/'nested/.ffsake').mkdir(parents=True);(root/'nested/.ffsake/private').write_text('do not expose\n')
        git('add','-f','nested/.ffsake/private')
        index_before=(root/'.git/index').read_bytes();user_before=(root/'user.txt').read_bytes()
        m=MCP(binary,root)
        try:
            assert not m.call('ffsake_tasks',{})['execution_enabled']
            m.call('ffsake_run_task',{'name':'build'},False)
            status=m.call('ffsake_git',{'query':'status'});entries={e['path']:e['status'] for e in status['entries']};assert entries['user.txt']=='MM',entries
            diff=m.call('ffsake_git',{'query':'diff','staged':True});assert '+user staged' in diff['diff'] and '+working' not in diff['diff'] and 'do not expose' not in diff['diff']
            diff=m.call('ffsake_git',{'query':'diff','paths':['weird[1].txt']});assert '+after' in diff['diff'] and 'tracked.txt' not in diff['diff']
            for paths in [['../outside'],['.git/config'],['nested/.ffsake/private'],['/tmp']]:m.call('ffsake_git',{'query':'diff','paths':paths},False)
            # Read-only Git must not dispatch configured executable clean filters.
            git('config','filter.probe.clean',f'touch {base}/FILTER_RAN')
            (root/'.gitattributes').write_text('tracked.txt filter=probe\n')
            error=m.call('ffsake_git',{'query':'diff'},False);assert 'clean/process filters' in str(error),error
            assert not (base/'FILTER_RAN').exists();git('config','--unset','filter.probe.clean')
            # No external diff/textconv execution.
            git('config','diff.probe.command',f'touch {base}/DIFF_RAN')
            git('config','diff.probe.textconv',f'touch {base}/TEXTCONV_RAN')
            (root/'.gitattributes').write_text('tracked.txt diff=probe\n')
            assert '+working' in m.call('ffsake_git',{'query':'diff','paths':['tracked.txt']})['diff']
            assert not (base/'DIFF_RAN').exists() and not (base/'TEXTCONV_RAN').exists()
            assert (root/'.git/index').read_bytes()==index_before
            assert (root/'user.txt').read_bytes()==user_before
        finally:m.close()
        # Inherited routing must not redirect inspection to an unrelated repository.
        m=MCP(binary,root,env={**os.environ,'GIT_DIR':str(base/'not-a-repository')})
        try:assert m.call('ffsake_git',{'query':'status'})['head']==git('rev-parse','HEAD').decode().strip()
        finally:m.close()
        policy=base/'trusted-tasks.json'
        tasks={'build':{'kind':'build','argv':['rustc','main.rs','-o','program']},
               'test':{'kind':'test','argv':[str(root/'program')]},
               'fail':{'kind':'test','argv':[sys.executable,'-c','raise SystemExit(7)']},
               'output':{'kind':'test','argv':[sys.executable,'-c','import sys;print("x"*100000);print("y"*100000,file=sys.stderr)'],'output_bytes':1000},
               'change':{'kind':'build','argv':[sys.executable,'-c','from pathlib import Path;Path("tracked.txt").write_text("task changed\\n")']},
               'timeout':{'kind':'test','argv':[sys.executable,'-c','import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);pathlib.Path("child.pid").write_text(str(p.pid));time.sleep(60)'],'timeout_seconds':1},
               'escape':{'kind':'test','argv':[sys.executable,'-c','raise SystemExit(0)'],'cwd':'escape'}}
        policy.write_text(json.dumps({'version':1,'tasks':tasks}));(root/'escape').symlink_to(base,target_is_directory=True)
        m=MCP(binary,root,policy)
        try:
            assert m.call('ffsake_tasks',{})['execution_enabled']
            policy.write_text('{}') # Policy was read once at startup; requests cannot change it.
            assert m.call('ffsake_run_task',{'name':'build'})['success']
            assert m.call('ffsake_run_task',{'name':'test'})['success']
            failed=m.call('ffsake_run_task',{'name':'fail'});assert not failed['success'] and failed['exit_code']==7
            output=m.call('ffsake_run_task',{'name':'output'});assert output['success'] and output['output_truncated'] and len(output['stdout'])==1000 and len(output['stderr'])==1000
            m.call('ffsake_run_task',{'name':'unknown'},False);m.call('ffsake_run_task',{'name':'test','argv':['anything']},False)
            m.call('ffsake_run_task',{'name':'escape'},False)
            preview=m.call('ffsake_preview_edit',{'path':'tracked.txt','old_text':'working','new_text':'edited','expected_matches':1})
            assert m.call('ffsake_run_task',{'name':'change'})['success']
            m.call('ffsake_apply',{'handle':preview['handle']},False);assert (root/'tracked.txt').read_text()=='task changed\n'
            assert m.call('ffsake_search',{'pattern':'task changed'})['matches']==1
            timed=m.call('ffsake_run_task',{'name':'timeout'});assert timed['timed_out'] and not timed['success'] and timed['elapsed_ms']<5000,timed
            pid=int((root/'child.pid').read_text());live=True
            for _ in range(30):
                ps=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True)
                if not ps.stdout.strip() or ps.stdout.lstrip().startswith('Z'):live=False;break
                time.sleep(.1)
            assert not live,'timed-out child remains alive'
            assert (root/'.git/index').read_bytes()==index_before and (root/'user.txt').read_bytes()==user_before
        finally:m.close()
        policy.write_text(json.dumps({'version':1,'tasks':tasks}))
        jsonl=subprocess.run([str(binary.with_name('ffsake')),'serve',str(root),'--tasks',str(policy)],input='{"op":"tasks"}\n{"op":"run_task","name":"fail"}\n',capture_output=True,text=True,timeout=10)
        replies=[json.loads(line) for line in jsonl.stdout.splitlines()]
        assert jsonl.returncode==0 and replies[0]['result']['execution_enabled'] and replies[1]['result']['exit_code']==7,(jsonl.stderr,replies)
        # Invalid policy must fail at startup, without running any task.
        for invalid in [{'version':2,'tasks':tasks},{'version':1,'tasks':{'bad':{'kind':'test','argv':['true'],'cwd':'../outside'}}},{'version':1,'tasks':{'bad':{'kind':'test','argv':['true'],'timeout_seconds':301}}}]:
            policy.write_text(json.dumps(invalid));r=subprocess.run([str(binary),str(root),'--tasks',str(policy)],input='',capture_output=True,text=True,timeout=5);assert r.returncode!=0
        print(json.dumps({'git_staged_and_worktree_preserved':True,'literal_diff_paths':True,'external_git_helpers_not_executed':True,'explicit_policy_only':True,'real_build_and_test':True,'failure_status':True,'bounded_output':True,'timeout_descendants_terminated':True,'stale_preview_after_task_rejected':True,'model_calls':0},indent=2))
if __name__=='__main__':main()
