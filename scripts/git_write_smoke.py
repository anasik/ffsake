#!/usr/bin/env python3
"""Actual MCP checks for file-limited Git operations in disposable repositories."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from workflow_smoke import MCP


def git(root, *args, check=True):
    return subprocess.run(['git', *args], cwd=root, check=check, capture_output=True, text=True)


def configure(root):
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'FFSAKE test')
    git(root, 'config', 'user.email', 'test@example.invalid')


def review(m, *paths):
    # A concurrent index refresh is a correctly rejected review; retry rather
    # than weakening the snapshot check. Repeated failures remain failures.
    for attempt in range(3):
        try:
            return m.call('ffsake_git_review', {'paths':list(paths)})
        except AssertionError as error:
            if 'Git state changed during review' not in str(error) or attempt == 2:
                raise


def action(m, review_result, action_name, message=None, ok=True):
    args={'review':review_result['review'], 'action':action_name}
    if message is not None: args['message']=message
    return m.call('ffsake_git_mutate', args, ok)


def main():
    binary=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-git-write-') as td:
        base=Path(td).resolve();root=base/'repo';root.mkdir();configure(root)
        (root/'file with spaces.txt').write_text('old\n');(root/'unrelated').write_text('baseline\n')
        git(root, 'add', '--all');git(root, 'commit', '-qm', 'initial')
        (root/'unrelated').write_text('unrelated staged\n');git(root, 'add', 'unrelated');(root/'unrelated').write_text('unrelated worktree\n')
        (root/'file with spaces.txt').write_text('desired\n');(root/'new [x].txt').write_text('new file\n')
        m=MCP(binary,root)
        try:
            name='file with spaces.txt';other='new [x].txt'
            r=review(m,name,other);action(m,r,'stage')
            r=review(m,name,other);assert '+desired' in r['staged_diff'] and '+new file' in r['staged_diff']
            assert 'unrelated staged' not in r['staged_diff']
            index_before=(root/'.git/index').read_bytes()
            action(m,r,'commit','reviewed change')
            assert (root/'.git/index').read_bytes()==index_before
            assert git(root,'show','--format=','--name-only','HEAD').stdout.splitlines()==[name,other]
            assert git(root,'show',f'HEAD:{name}').stdout=='desired\n'
            assert git(root,'diff','--cached','--','unrelated').stdout.find('+unrelated staged')>=0
            assert (root/'unrelated').read_text()=='unrelated worktree\n'
            strange=':(literal) name\nwith newline.txt';(root/strange).write_text('odd path\n')
            action(m,review(m,strange),'stage');r=review(m,strange);assert '+odd path' in r['staged_diff']
            action(m,r,'commit','add unusual filename');assert git(root,'show',f'HEAD:{strange}').stdout=='odd path\n'
            (root/strange).unlink();action(m,review(m,strange),'stage');r=review(m,strange)
            assert '-odd path' in r['staged_diff'];action(m,r,'commit','delete unusual filename')
            assert git(root,'cat-file','-e',f'HEAD:{strange}',check=False).returncode!=0
            (root/name).write_text('next\n');action(m,review(m,name),'stage');action(m,review(m,name),'unstage')
            assert git(root,'diff','--cached','--',name).stdout==''
            r=review(m,name);(root/name).write_text('stale\n');action(m,r,'stage',ok=False)
            assert git(root,'diff','--cached','--',name).stdout==''
            r=review(m,name);git(root,'add','unrelated');action(m,r,'stage',ok=False)
            assert git(root,'diff','--cached','--',name).stdout==''
            r=review(m,name);old=git(root,'rev-parse','HEAD').stdout.strip()
            tree=git(root,'rev-parse','HEAD^{tree}').stdout.strip()
            advanced=git(root,'commit-tree',tree,'-p',old,'-m','external change').stdout.strip()
            git(root,'update-ref','HEAD',advanced,old);action(m,r,'stage',ok=False)
            assert git(root,'diff','--cached','--',name).stdout==''
            for invalid in ['../escape','.git/config','.ffsake/foo','/tmp/x']:
                m.call('ffsake_git_review',{'paths':[invalid]},False)
            (root/'escape').symlink_to(base,target_is_directory=True)
            m.call('ffsake_git_review',{'paths':['escape/nope']},False)
            hook=root/'.git/hooks/pre-commit';hook.write_text(f'#!/bin/sh\ntouch {base}/hook-ran\n');hook.chmod(0o755)
            m.call('ffsake_git_review',{'paths':[name]},False);assert not (base/'hook-ran').exists();hook.unlink()
            git(root,'config','core.fsmonitor',f'touch {base}/monitor-ran')
            m.call('ffsake_git_review',{'paths':[name]},False);assert not (base/'monitor-ran').exists()
            git(root,'config','--unset','core.fsmonitor')
            hook=root/'.git/hooks/reference-transaction';hook.write_text(f'#!/bin/sh\ntouch {base}/reference-ran\n');hook.chmod(0o755)
            m.call('ffsake_git_review',{'paths':[name]},False);assert not (base/'reference-ran').exists();hook.unlink()
            git(root,'config','filter.set.clean',f'touch {base}/filter-ran')
            (root/'.gitattributes').write_text(f'{name.replace(" ", "?")} filter=set\n')
            m.call('ffsake_git_review',{'paths':[name]},False);assert not (base/'filter-ran').exists()
            git(root,'config','--unset','filter.set.clean');(root/'.gitattributes').unlink()
            git(root,'config','commit.gpgsign','true');r=review(m,name);action(m,r,'commit','signed',False);git(root,'config','--unset','commit.gpgsign')
            action(m,review(m,name),'stage')
            git(root,'update-index','--assume-unchanged',name)
            (root/name).write_text('hidden unreviewed change\n')
            assert git(root,'diff','--',name).stdout==''
            r=review(m,name);before=git(root,'rev-parse','HEAD').stdout
            action(m,r,'commit','must reject hidden change',False)
            assert git(root,'rev-parse','HEAD').stdout==before
            git(root,'update-index','--no-assume-unchanged',name)
            (root/name).write_text('stale\n')
            git(root,'update-index','--skip-worktree',name)
            action(m,review(m,name),'commit','must reject skip-worktree',False)
            git(root,'update-index','--no-skip-worktree',name)
            r=review(m,name);before=git(root,'rev-parse','HEAD').stdout
            staged=git(root,'diff','--cached','--',name).stdout
            ref=git(root,'symbolic-ref','HEAD').stdout.strip()
            lock=root/'.git'/(ref+'.lock');lock.write_text('held by another process')
            action(m,r,'commit','blocked reference',False);lock.unlink()
            assert git(root,'rev-parse','HEAD').stdout==before
            assert git(root,'diff','--cached','--',name).stdout==staged
            m.call('ffsake_git_review',{'paths':[name]},True)
        finally:m.close()
        unborn=base/'unborn';unborn.mkdir();configure(unborn);(unborn/'first').write_text('first\n')
        m=MCP(binary,unborn)
        try:
            action(m,review(m,'first'),'stage');action(m,review(m,'first'),'unstage')
            action(m,review(m,'first'),'stage');action(m,review(m,'first'),'commit','first commit')
            assert git(unborn,'show','HEAD:first').stdout=='first\n'
        finally:m.close()
        wt=base/'linked';git(root,'config','user.email','test@example.invalid');git(root,'worktree','add','-qb','secondary',str(wt))
        (wt/'linked file').write_text('linked\n');m=MCP(binary,wt)
        try:
            action(m,review(m,'linked file'),'stage');action(m,review(m,'linked file'),'commit','linked worktree')
            assert git(wt,'show','HEAD:linked file').stdout=='linked\n'
        finally:m.close()
        print(json.dumps({'selected_commit':True,'unrelated_preserved':True,'stage_unstage':True,'stale_rejected':True,'hook_and_fsmonitor_refused':True,'unborn':True,'worktree':True},indent=2))

if __name__=='__main__':main()
