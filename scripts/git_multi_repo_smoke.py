#!/usr/bin/env python3
"""Actual MCP Git calls over separately authorized repositories and linked worktrees."""
import json, subprocess, sys, tempfile
from pathlib import Path
from workflow_smoke import MCP
from access_smoke import Client

def git(root,*args):
 p=subprocess.run(['git',*args],cwd=root,text=True,capture_output=True)
 if p.returncode: raise AssertionError((args,p.stderr))
 return p.stdout

def init(root):
 root.mkdir();git(root,'init','-q','-b','main');git(root,'config','user.name','Fixture');git(root,'config','user.email','test@example.invalid')
 (root/'tracked.txt').write_text('base\n');(root/'unrelated.txt').write_text('initial\n')
 git(root,'add','--all');git(root,'commit','-qm','initial')

def main():
 binary=Path(sys.argv[1]).resolve()
 with tempfile.TemporaryDirectory(prefix='ffsake-git-multi-') as td:
  base=Path(td).resolve();primary=base/'primary';one=base/'one';two=base/'two repo';linked=base/'linked';bare=base/'remote.git'
  for repo in (primary,one,two):init(repo)
  (primary/'.git/info/exclude').write_text('.ffsake/\n')
  git(base,'init','--bare','-q',str(bare));git(one,'remote','add','origin',str(bare));git(one,'worktree','add','-q','-b','linked',str(linked))
  (primary/'tracked.txt').write_text('primary untouched\n');git(primary,'add','tracked.txt');(primary/'unrelated.txt').write_text('primary working\n')
  primary_state=(git(primary,'status','--porcelain=v1'),git(primary,'rev-parse','HEAD'),(primary/'.git/index').read_bytes())
  (one/'tracked.txt').write_text('one change\n');(one/'unrelated.txt').write_text('staged other\n');git(one,'add','unrelated.txt');(one/'unrelated.txt').write_text('working other\n')
  (two/'tracked.txt').write_text('two change\n');(linked/'tracked.txt').write_text('linked change\n')
  listing=subprocess.run([str(binary),str(primary),'--profile','core'],input=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'})+'\n',capture_output=True,text=True,check=True,timeout=10)
  tools={x['name']:x for x in json.loads(listing.stdout)['result']['tools']}
  for name in ('ffsake_git','ffsake_git_review','ffsake_git_mutate','ffsake_git_branches','ffsake_git_operation_review','ffsake_git_operation'):
   schema=tools[name]['inputSchema'];assert 'repository' in schema['properties'] and 'repository' not in schema.get('required',[])
  m=MCP(binary,primary)
  try:
   def call(tool,repo=None,ok=True,**kw):
    if repo is not None:kw['repository']=str(repo)
    return m.call('ffsake_'+tool,kw,ok)
   def grant(repo,mode='read_write'):call('open_access',path=str(repo),kind='directory',mode=mode)
   def revoke(repo):call('remove_access',path=str(repo))
   def review(repo,paths=['tracked.txt']):return call('git_review',repo,paths=paths)
   def mutate(repo,r,action,ok=True,**kw):return call('git_mutate',repo,ok,review=r['review'],action=action,**kw)
   def op_review(repo,op):return call('git_operation_review',repo,operation=op)
   def op_apply(repo,r,op,ok=True,**kw):return call('git_operation',repo,ok,review=r['review'],operation=op,**kw)
   call('git',one,False,query='status');call('git',two,False,query='status')
   call('open_access',path=str(one/'tracked.txt'),kind='file',mode='read_write')
   call('git',one,False,query='status');call('remove_access',path=str(one/'tracked.txt'))
   grant(one);call('git',two,False,query='status');grant(two);grant(linked)
   assert '+one change' in call('git',one,query='diff',paths=['tracked.txt'])['diff']
   assert '+two change' in call('git',two,query='diff',paths=['tracked.txt'])['diff']
   assert '+linked change' in call('git',linked,query='diff',paths=['tracked.txt'])['diff']
   child=one/'nested';child.mkdir();call('git',child,False,query='status')
   revoke(two);forged=base/'forged';forged.mkdir();(forged/'.git').write_text('gitdir: '+str(two/'.git')+'\n');grant(forged);call('git',forged,False,query='status');grant(two)
   r=review(one);assert r['repository']==str(one);mutate(two,r,'stage',False)
   r=review(one);mutate(one,r,'stage');r=review(one);assert '+one change' in r['staged_diff'];mutate(one,r,'commit',message='one reviewed')
   assert git(one,'show','HEAD:tracked.txt')=='one change\n' and '+staged other' in git(one,'diff','--cached','--','unrelated.txt') and (one/'unrelated.txt').read_text()=='working other\n'
   r=review(two);mutate(two,r,'stage');mutate(two,review(two),'unstage');assert not git(two,'diff','--cached','--','tracked.txt')
   r=review(two);(two/'tracked.txt').write_text('stale change\n');mutate(two,r,'stage',False);assert not git(two,'diff','--cached','--','tracked.txt')
   create={'action':'create','branch':'topic'};r=op_review(one,create);op_apply(two,r,create,False)
   op_apply(one,op_review(one,create),create)
   switch={'action':'switch','branch':'topic'};op_apply(one,op_review(one,switch),switch)
   assert call('git_branches',one)['current']=='topic'
   push={'action':'push','branch':'topic','remote':'origin','remote_branch':'topic','set_upstream':True};r=op_review(one,push);op_apply(one,r,push,source_commit=r['source_commit'])
   fetch={'action':'fetch','remote':'origin'};op_apply(one,op_review(one,fetch),fetch)
   assert git(bare,'rev-parse','refs/heads/topic')==git(one,'rev-parse','topic')
   revoke(one);call('git',one,False,query='status')
   assert call('git_branches',linked)['current']=='linked';r=review(linked);mutate(linked,r,'stage');assert '+linked change' in review(linked)['staged_diff'];mutate(linked,review(linked),'commit',message='linked reviewed')
   linked_push={'action':'push','branch':'linked','remote':'origin','remote_branch':'linked','set_upstream':True};r=op_review(linked,linked_push);op_apply(linked,r,linked_push,source_commit=r['source_commit'])
   op_apply(linked,op_review(linked,fetch),fetch);assert git(bare,'rev-parse','refs/heads/linked')==git(linked,'rev-parse','HEAD')
   linked_create={'action':'create','branch':'linked-child'};op_apply(linked,op_review(linked,linked_create),linked_create)
   linked_switch={'action':'switch','branch':'linked-child'};op_apply(linked,op_review(linked,linked_switch),linked_switch);assert call('git_branches',linked)['current']=='linked-child'
   r=review(two);r_op=op_review(two,create);revoke(two);mutate(two,r,'stage',False);op_apply(two,r_op,create,False);call('git',two,False,query='status');assert not git(two,'diff','--cached','--','tracked.txt')
   grant(two,'read_only');assert call('git',two,query='status')['entries'];r=review(two);mutate(two,r,'stage',False);r=op_review(two,create);op_apply(two,r,create,False)
   # Explicit root aliases and neighboring repositories do not bypass grants.
   alias=one/'alias';alias.symlink_to(two,target_is_directory=True);call('git',alias,False,query='status')
   assert (git(primary,'status','--porcelain=v1'),git(primary,'rev-parse','HEAD'),(primary/'.git/index').read_bytes())==primary_state
  finally:m.close()
  # An explicit read-only primary policy does not eliminate a separately granted
  # external write scope; ordinary Git state in the primary remains untouched.
  policy=base/'host-policy.json'
  policy.write_text(json.dumps({'version':1,'project':str(primary),'primary_mode':'read_only','grants':[{'path':str(one),'kind':'directory','mode':'read_write','lifetime':'project'}]}))
  c=Client(binary,primary,policy,mcp=True)
  try:
   (one/'tracked.txt').write_text('external write under read-only primary\n')
   r=c.call('git_review',repository=str(one),paths=['tracked.txt']);c.call('git_mutate',repository=str(one),review=r['review'],action='stage')
   r=c.call('git_review',repository=str(one),paths=['tracked.txt']);c.call('git_mutate',repository=str(one),review=r['review'],action='commit',message='independent scope')
   assert git(one,'show','HEAD:tracked.txt')=='external write under read-only primary\n'
   assert (git(primary,'status','--porcelain=v1'),git(primary,'rev-parse','HEAD'),(primary/'.git/index').read_bytes())==primary_state
  finally:c.close()
  print(json.dumps({'separate_git_repositories':True,'linked_worktree':True,'review_identity':True,'revocation_readonly':True,'stage_commit_branches_fetch_push':True,'primary_preserved':True,'readonly_primary_external_write':True}))
if __name__=='__main__':main()
