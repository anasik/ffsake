#!/usr/bin/env python3
"""Real MCP checks for persistent VS Code process-task authorization and lifecycle."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from workflow_smoke import MCP


def wait(m, job, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = m.call('ffsake_task_status', {'job':job})
        if status['status'] != 'running': return status
        time.sleep(.02)
    raise AssertionError(('task did not finish', job))


def define(label, code):
    return {'label':label,'type':'process','command':sys.executable,'args':['-c',code],
            'options':{'cwd':'${workspaceFolder}'},'group':'test'}


def run(m, name):
    return wait(m, m.call('ffsake_run_task', {'name':name})['job'])


def main():
    binary=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-project-tasks-') as td:
        base=Path(td).resolve();root=base/'root';root.mkdir();(root/'editable.txt').write_text('before\n')
        m=MCP(binary,root)
        try:
            assert m.call('ffsake_tasks',{})['tasks']==[]
            m.call('ffsake_execute',{'executable':sys.executable,'args':[],'cwd':'.'},False)
            path=root/'.vscode/tasks.json';path.parent.mkdir()
            original=define('Check','print("first")')
            path.write_text(json.dumps({'version':'2.0.0','tasks':[original]}))
            assert not m.call('ffsake_tasks',{})['tasks'][0]['authorized']
            m.call('ffsake_run_task',{'name':'Check'},False)
            approval=m.call('ffsake_task_register',{'definition':original,'timeout_seconds':5,'output_bytes':1024})
            assert approval['persistent'] and approval['authorized']
            assert run(m,'Check')['stdout'].strip()=='first'
            # Formatting and object-key order do not change execution authorization.
            data=json.loads(path.read_text());path.write_text(json.dumps(data,separators=(',',':'),sort_keys=True))
            assert m.call('ffsake_tasks',{})['tasks'][0]['authorized'] and run(m,'Check')['success']
            # VS Code JSONC comments/trailing commas are formatting, not new authorization.
            jsonc='{\n  // workspace tasks\n  "version":"2.0.0",\n  "tasks":[\n    // keep this note\n    '+json.dumps(original)+',\n  ],\n}\n'
            path.write_text(jsonc)
            assert m.call('ffsake_tasks',{})['tasks'][0]['authorized'] and run(m,'Check')['success']
            m.call('ffsake_task_register',{'definition':original,'timeout_seconds':5,'output_bytes':1024})
            assert '// workspace tasks' in path.read_text() and '// keep this note' in path.read_text()
            path.write_text(json.dumps({'version':'2.0.0','tasks':[original]}))
            # Another live instance observes host edits and reauthorization.
            second_client=MCP(binary,root)
            try:
                externally_changed=define('Check','print("external")')
                data=json.loads(path.read_text());data['tasks'][0]=externally_changed;path.write_text(json.dumps(data))
                second_client.call('ffsake_run_task',{'name':'Check'},False)
                m.call('ffsake_task_register',{'definition':original,'timeout_seconds':5,'output_bytes':1024})
                assert run(second_client,'Check')['stdout'].strip()=='first'
            finally: second_client.close()
            # Executable content changes invalidate approval; timestamp changes alone do not.
            executable=root/'tool.sh';executable.write_text('#!/bin/sh\necho one\n');executable.chmod(0o755)
            script={'label':'Script','type':'process','command':str(executable),'args':[]}
            m.call('ffsake_task_register',{'definition':script});os.utime(executable,None)
            assert run(m,'Script')['stdout'].strip()=='one'
            executable.write_text('#!/bin/sh\necho two\n');m.call('ffsake_run_task',{'name':'Script'},False)
            m.call('ffsake_task_register',{'definition':script});assert run(m,'Script')['stdout'].strip()=='two'
            # A normal FFSAKE file edit is not task approval.
            preview=m.call('ffsake_preview_edit',{'path':'.vscode/tasks.json','old_text':'first','new_text':'second','expected_matches':1})
            m.call('ffsake_apply',{'handle':preview['handle']})
            assert not m.call('ffsake_tasks',{})['tasks'][0]['authorized']
            m.call('ffsake_run_task',{'name':'Check'},False)
            changed=define('Check','print("second")')
            m.call('ffsake_task_register',{'definition':changed,'timeout_seconds':5,'output_bytes':1024})
            assert run(m,'Check')['stdout'].strip()=='second'
            # Unsupported shell tasks, interpolation, env and escapes never register.
            for bad in [dict(changed,type='shell'),dict(changed,command='${command:evil}'),dict(changed,args=['${input:evil}']),dict(changed,options={'env':{'X':'Y'}}),dict(changed,options={'cwd':'../outside'}),dict(changed,dependsOn=['Other'])]:
                m.call('ffsake_task_register',{'definition':bad},False)
            m.call('ffsake_read',{'path':'.ffsake/project-tasks/approvals.json'},False)
            m.call('ffsake_preview_edit',{'path':'.ffsake/project-tasks/approvals.json','old_text':'x','new_text':'y','expected_matches':1},False)
            failed=define('Fail','import sys;print("bad",file=sys.stderr);sys.exit(7)')
            m.call('ffsake_task_register',{'definition':failed});result=run(m,'Fail');assert not result['success'] and result['exit_code']==7 and 'bad' in result['stderr'];m.call('ffsake_task_cancel',{'job':result['job']},False)
            output=define('Output','import sys;print("x"*50000);print("y"*50000,file=sys.stderr)')
            m.call('ffsake_task_register',{'definition':output,'output_bytes':512});result=run(m,'Output');assert result['success'] and result['output_truncated'] and len(result['stdout'])==len(result['stderr'])==512
            sleeper=define('Sleep','import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);pathlib.Path("child.pid").write_text(str(p.pid));time.sleep(60)')
            m.call('ffsake_task_register',{'definition':sleeper,'timeout_seconds':10});job=m.call('ffsake_run_task',{'name':'Sleep'})['job']
            for _ in range(100):
                if (root/'child.pid').exists():break
                time.sleep(.02)
            assert (root/'child.pid').exists();pid=int((root/'child.pid').read_text());m.call('ffsake_task_cancel',{'job':job});result=wait(m,job);assert result['cancelled'] and not result['success']
            for _ in range(50):
                ps=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True)
                if not ps.stdout.strip() or ps.stdout.lstrip().startswith('Z'):break
                time.sleep(.05)
            else:raise AssertionError('cancelled descendant remains alive')
            # Changing an approved task while it runs does not alter the launched process.
            frozen=define('Frozen','import time;time.sleep(0.4);print("original")')
            updated=define('Frozen','print("updated")')
            m.call('ffsake_task_register',{'definition':frozen});job=m.call('ffsake_run_task',{'name':'Frozen'})['job']
            m.call('ffsake_task_register',{'definition':updated})
            assert wait(m,job)['stdout'].strip()=='original'
            assert run(m,'Frozen')['stdout'].strip()=='updated'
            timeout=define('Timeout','import time;time.sleep(30)')
            m.call('ffsake_task_register',{'definition':timeout,'timeout_seconds':1});result=run(m,'Timeout');assert result['timed_out'] and not result['success']
            # Direct task-side changes preserve editor stale-preview guard.
            preview=m.call('ffsake_preview_edit',{'path':'editable.txt','old_text':'before','new_text':'after','expected_matches':1})
            changer=define('Change','from pathlib import Path;Path("editable.txt").write_text("changed\\n")')
            m.call('ffsake_task_register',{'definition':changer});assert run(m,'Change')['success']
            m.call('ffsake_apply',{'handle':preview['handle']},False);assert (root/'editable.txt').read_text()=='changed\n'
        finally:m.close()
        # Restart preserves matching authorization; copying approvals to another project does not.
        m=MCP(binary,root)
        try:assert run(m,'Check')['stdout'].strip()=='second'
        finally:m.close()
        other=base/'other';shutil.copytree(root,other)
        m=MCP(binary,other)
        try:m.call('ffsake_tasks',{},False);m.call('ffsake_run_task',{'name':'Check'},False)
        finally:m.close()
        # A restrictive startup policy disables registration and still runs its named task.
        policy=base/'tasks.json';policy.write_text(json.dumps({'version':1,'tasks':{'allowed':{'kind':'test','argv':[sys.executable,'-c','print("allowed")']}}}))
        m=MCP(binary,root,policy)
        try:
            m.call('ffsake_task_register',{'definition':changed},False)
            m.call('ffsake_run_task',{'name':'Check'},False)
            assert m.call('ffsake_run_task',{'name':'allowed'})['success']
        finally:m.close()
    print(json.dumps({'external_file_not_trusted':True,'authorization_persistent':True,'formatting_preserved':True,'ordinary_edits_do_not_authorize':True,'reauthorization':True,'unsupported_rejected':True,'project_isolation':True,'failed_test':True,'output_bounds':True,'timeout':True,'cancellation_cleanup':True,'stale_preview':True,'restrictive_policy':True},indent=2))

if __name__=='__main__':main()
