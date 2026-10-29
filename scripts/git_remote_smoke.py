#!/usr/bin/env python3
"""Actual MCP branch, fetch and non-force push checks against local bare remotes."""
import json,os,subprocess,sys,tempfile
from pathlib import Path
from workflow_smoke import MCP
from access_smoke import Client

def git(root,*args,ok=True):
    p=subprocess.run(['git',*args],cwd=root,capture_output=True,text=True)
    if ok and p.returncode:raise AssertionError((args,p.returncode,p.stderr))
    return p

def main():
    binary=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-remotes-') as td:
        base=Path(td).resolve();bare=base/'remote.git';git(base,'init','--bare','-q',str(bare))
        root=base/'work';root.mkdir();git(root,'init','-q','-b','main');git(root,'config','user.name','Fixture');git(root,'config','user.email','fixture@example.invalid')
        (root/'tracked.txt').write_text('base\n');(root/'other.txt').write_text('base\n');(root/'staged.txt').write_text('base\n')
        git(root,'add','.');git(root,'commit','-qm','initial');git(root,'remote','add','origin',str(bare))
        (root/'other.txt').write_text('unrelated unstaged\n');(root/'staged.txt').write_text('unrelated staged\n');git(root,'add','staged.txt')
        m=MCP(binary,root)
        def review(op):return m.call('ffsake_git_operation_review',{'operation':op})
        def apply(op,r=None,ok=True):
            r=r or review(op);args={'review':r['review'],'operation':op}
            if op['action']=='push':args['source_commit']=r['source_commit']
            return m.call('ffsake_git_operation',args,ok)
        try:
            assert m.call('ffsake_git_branches',{})['current']=='main'
            create={'action':'create','branch':'topic/test'};apply(create)
            switch={'action':'switch','branch':'topic/test'};apply(switch)
            assert m.call('ffsake_git_branches',{})['current']=='topic/test'
            assert (root/'other.txt').read_text()=='unrelated unstaged\n' and '+unrelated staged' in git(root,'diff','--cached','--','staged.txt').stdout
            git(root,'branch','origin/team/topic')  # A local name shadows the short tracking name.
            push={'action':'push','branch':'topic/test','remote':'origin','remote_branch':'team/topic','set_upstream':True}
            r=review(push);assert r['source_commit']==git(root,'rev-parse','HEAD').stdout.strip() and r['operation']==push
            m.call('ffsake_git_operation',{'review':r['review'],'operation':push},False)
            r=review(push)
            lock=root/'.git/refs/heads/topic/test.lock';lock.write_text('another Git operation')
            apply(push,r,False);lock.unlink();r=review(push)
            apply(push,r);assert git(bare,'rev-parse','refs/heads/team/topic').stdout.strip()==r['source_commit']
            assert not (root/'.git/refs/heads/topic/test.lock').exists()
            assert git(root,'config','--get','branch.topic/test.remote').stdout.strip()=='origin'
            assert git(root,'config','--get','branch.topic/test.merge').stdout.strip()=='refs/heads/team/topic'
            assert git(root,'rev-parse','--symbolic-full-name','topic/test@{upstream}').stdout.strip()=='refs/remotes/origin/team/topic'
            branches=m.call('ffsake_git_branches',{})['branches']
            assert next(b for b in branches if b['branch']=='topic/test')['upstream_ref']=='refs/remotes/origin/team/topic'
            assert any(b['branch']=='origin/team/topic' for b in branches)
            r=review(push);(root/'staged.txt').write_text('unrelated staged\nnew index content\n');git(root,'add','staged.txt');apply(push,r,False)
            r=review(push);git(root,'config','user.name','Changed after review');apply(push,r,False);git(root,'config','user.name','Fixture')
            (root/'tracked.txt').write_text('next\n');git(root,'add','tracked.txt');git(root,'commit','-qm','next','--only','tracked.txt')
            push['set_upstream']=False;apply(push);assert git(bare,'rev-parse','refs/heads/team/topic').stdout.strip()==git(root,'rev-parse','HEAD').stdout.strip()
            fetch={'action':'fetch','remote':'origin'};apply(fetch)
            stale=review(push);(root/'tracked.txt').write_text('third\n');git(root,'add','tracked.txt');git(root,'commit','-qm','third','--only','tracked.txt');apply(push,stale,False)
            outsider=base/'outsider';git(base,'clone','-q','--branch','team/topic',str(bare),str(outsider));git(outsider,'config','user.name','Other');git(outsider,'config','user.email','other@example.invalid')
            (outsider/'external').write_text('external\n');git(outsider,'add','external');git(outsider,'commit','-qm','external');git(outsider,'push','-q','origin','HEAD:refs/heads/team/topic')
            remote_before=git(bare,'rev-parse','refs/heads/team/topic').stdout.strip();err=apply(push,ok=False)
            assert 'non-fast-forward' in str(err) or 'failed' in str(err),err
            assert git(bare,'rev-parse','refs/heads/team/topic').stdout.strip()==remote_before
            assert not (root/'.git/refs/heads/topic/test.lock').exists()
            apply(fetch);assert git(root,'rev-parse','refs/remotes/origin/team/topic').stdout.strip()==remote_before
            older=git(bare,'rev-parse',remote_before+'^').stdout.strip()
            git(bare,'update-ref','refs/heads/team/topic',older,remote_before)
            apply(fetch,ok=False)
            assert git(root,'rev-parse','refs/remotes/origin/team/topic').stdout.strip()==remote_before
            git(bare,'update-ref','refs/heads/team/topic',remote_before,older)
            # Switch with unrelated changes preserved; reject stale and overlapping edits.
            back={'action':'switch','branch':'main'};r=review(back);(root/'tracked.txt').write_text('new local edit\n');apply(back,r,False)
            assert (root/'tracked.txt').read_text()=='new local edit\n';r=review(back);apply(back,r,False)
            assert (root/'tracked.txt').read_text()=='new local edit\n' and m.call('ffsake_git_branches',{})['current']=='topic/test'
            assert (root/'other.txt').read_text()=='unrelated unstaged\n' and '+unrelated staged' in git(root,'diff','--cached','--','staged.txt').stdout
            for op in [{'action':'push','branch':'topic/test','remote':'origin','remote_branch':'+refs/heads/oops'},{'action':'fetch','remote':'--all'},{'action':'switch','branch':'../invalid'}]:m.call('ffsake_git_operation_review',{'operation':op},False)
            git(root,'config','remote.origin.mirror','true');m.call('ffsake_git_operation_review',{'operation':push},False);git(root,'config','--unset','remote.origin.mirror')
            git(root,'config','push.followTags','true');m.call('ffsake_git_operation_review',{'operation':push},False);review(fetch);git(root,'config','--unset','push.followTags')
            git(root,'config','fetch.prune','true');m.call('ffsake_git_operation_review',{'operation':fetch},False);review(push);git(root,'config','--unset','fetch.prune')
            git(root,'config','push.gpgSign','true');m.call('ffsake_git_operation_review',{'operation':push},False);git(root,'config','--unset','push.gpgSign')
            hook=root/'.git/hooks/pre-push';hook.write_text(f'#!/bin/sh\ntouch {base}/PUSH_HOOK_RAN\n');hook.chmod(0o755)
            m.call('ffsake_git_operation_review',{'operation':push},False);assert not (base/'PUSH_HOOK_RAN').exists();hook.unlink()
            git(root,'config','core.fsmonitor',f'touch {base}/MONITOR_RAN');m.call('ffsake_git_operation_review',{'operation':push},False)
            assert not (base/'MONITOR_RAN').exists();git(root,'config','--unset','core.fsmonitor')
            git(root,'config','--add','remote.origin.fetch','+refs/tags/*:refs/tags/*');m.call('ffsake_git_operation_review',{'operation':fetch},False)
            git(root,'config','--unset-all','remote.origin.fetch');git(root,'config','--add','remote.origin.fetch','+refs/heads/*:refs/remotes/origin/*')
            r=review(push);mismatch=dict(push,remote_branch='different');apply(mismatch,r,False)
            assert git(bare,'rev-parse','refs/heads/team/topic').stdout.strip()==remote_before
            git(root,'remote','add','secret','https://user:TOP_SECRET_TOKEN@127.0.0.1:9/repo.git')
            bad={'action':'fetch','remote':'secret'};r=review(bad);assert 'TOP_SECRET_TOKEN' not in json.dumps(r)
            e=apply(bad,r,False);assert 'TOP_SECRET_TOKEN' not in json.dumps(e)
        finally:m.close()
        policy=base/'host-policy.json'
        def export(mode):policy.write_text(json.dumps({'version':1,'project':str(root),'primary_mode':mode,'grants':[]}))
        export('read_write');restricted=Client(binary,root,policy,mcp=True)
        try:
            op={'action':'create','branch':'must-not-create'}
            reviewed=restricted.call('git_operation_review',operation=op)
            export('read_only');restricted.call('git_branches')
            restricted.call('git_operation',False,review=reviewed['review'],operation=op)
            assert git(root,'show-ref','--verify','refs/heads/must-not-create',ok=False).returncode!=0
        finally:restricted.close()
        protected=base/'protected';protected.mkdir();git(protected,'init','-q','-b','main');git(protected,'config','user.name','Fixture');git(protected,'config','user.email','fixture@example.invalid')
        (protected/'ordinary').write_text('base\n');git(protected,'add','ordinary');git(protected,'commit','-qm','base');git(protected,'switch','-qc','unsafe')
        (protected/'.ffsake').mkdir();(protected/'.ffsake/guard').write_text('must stay protected');git(protected,'add','-f','.ffsake/guard');git(protected,'commit','-qm','protected path');git(protected,'switch','-q','main')
        m=MCP(binary,protected)
        try:
            error=m.call('ffsake_git_operation_review',{'operation':{'action':'switch','branch':'unsafe'}},False)
            assert '.ffsake' in str(error) and not (protected/'.ffsake/guard').exists()
        finally:m.close()
        ignored=base/'ignored';ignored.mkdir();git(ignored,'init','-q','-b','main');git(ignored,'config','user.name','Fixture');git(ignored,'config','user.email','fixture@example.invalid')
        (ignored/'.gitignore').write_text('local.bin\n');(ignored/'ordinary').write_text('base\n');git(ignored,'add','.');git(ignored,'commit','-qm','base');git(ignored,'switch','-qc','target')
        (ignored/'local.bin').write_text('tracked target\n');git(ignored,'add','-f','local.bin');git(ignored,'commit','-qm','target');git(ignored,'switch','-q','main');(ignored/'local.bin').write_text('preserve ignored user file\n')
        m=MCP(binary,ignored)
        try:
            op={'action':'switch','branch':'target'};r=m.call('ffsake_git_operation_review',{'operation':op})
            m.call('ffsake_git_operation',{'review':r['review'],'operation':op},False)
            assert (ignored/'local.bin').read_text()=='preserve ignored user file\n' and m.call('ffsake_git_branches',{})['current']=='main'
        finally:m.close()
        sym=base/'symlink';sym.mkdir();git(sym,'init','-q','-b','main');git(sym,'config','user.name','Fixture');git(sym,'config','user.email','fixture@example.invalid')
        (sym/'ordinary').write_text('base\n');git(sym,'add','ordinary');git(sym,'commit','-qm','base');git(sym,'switch','-qc','unsafe')
        (sym/'link').symlink_to(base);git(sym,'add','link');git(sym,'commit','-qm','symlink');git(sym,'switch','-q','main')
        m=MCP(binary,sym)
        try:
            error=m.call('ffsake_git_operation_review',{'operation':{'action':'switch','branch':'unsafe'}},False)
            assert 'symlink' in str(error) and not (sym/'link').is_symlink()
        finally:m.close()
        linked=base/'linked';git(root,'worktree','add','-qb','linked',str(linked),'main');m=MCP(binary,linked)
        try:
            assert m.call('ffsake_git_branches',{})['current']=='linked'
            op={'action':'create','branch':'linked-child'};r=m.call('ffsake_git_operation_review',{'operation':op});m.call('ffsake_git_operation',{'review':r['review'],'operation':op})
            assert git(linked,'show-ref','--verify','refs/heads/linked-child').returncode==0
            op={'action':'switch','branch':'linked-child'};r=m.call('ffsake_git_operation_review',{'operation':op});m.call('ffsake_git_operation',{'review':r['review'],'operation':op})
            assert m.call('ffsake_git_branches',{})['current']=='linked-child'
        finally:m.close()
        print(json.dumps({'branches_and_switch':True,'dirty_preserved':True,'fetch':True,'first_push_and_upstream':True,'fast_forward':True,'non_fast_forward_rejected':True,'stale_rejected':True,'credentials_not_exposed':True,'worktree':True},indent=2))
if __name__=='__main__':main()
