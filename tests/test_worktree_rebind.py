"""Only a reviewed, worktree-local server root changes; permission rules remain exact."""
import json,os,subprocess,sys,tempfile,tomllib,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import ffsake_install as install

def git(root,*args):
 p=subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,check=True);return p.stdout.strip()

class RebindTests(unittest.TestCase):
 def test_all_hosts_preserve_approvals_and_other_settings(self):
  for host in ('claude','codex','opencode'):
   with self.subTest(host=host),tempfile.TemporaryDirectory() as td:
    base=Path(td).resolve();parent=base/'parent';parent.mkdir();child=base/'child';prefix=base/'installed';(prefix/'current').mkdir(parents=True)
    git(parent,'init','-q','-b','main');git(parent,'config','user.name','Fixture');git(parent,'config','user.email','fixture@example.invalid')
    (parent/'data').write_text('original');git(parent,'add','data');git(parent,'commit','-qm','fixture');git(parent,'worktree','add','-qb','child',str(child))
    dirs={k:str(base/k) for k in ('CLAUDE_CONFIG_DIR','CODEX_HOME','XDG_CONFIG_HOME')}
    with patch.dict(os.environ,dirs,clear=False):
     config=install.desired_config(prefix,host,parent,'core');rel={'claude':'.mcp.json','codex':'.codex/config.toml','opencode':'opencode.json'}[host];path=child/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(config[rel])
     if host=='claude':
      path.write_text(json.dumps({'mcpServers':{'ffsake':json.loads(path.read_text())['mcpServers']['ffsake'],'other':{'command':'keep'}},'other':'keep'}))
     if host=='opencode':
      d=json.loads(path.read_text());d['permission']['ffsake_ffsake_run_task']='deny';d['other']='keep';path.write_text(json.dumps(d))
     before=path.read_bytes();plan=install.plan_rebind(prefix,host,child,parent)
     self.assertEqual(path.read_bytes(),before);self.assertEqual(len(plan),1);self.assertEqual(plan[0][3][0]['new_root'],str(child))
     path.write_bytes(plan[0][2]);self.assertEqual(install.plan_rebind(prefix,host,child,parent),[])
     if host=='codex':
      old=tomllib.loads(before.decode());new=tomllib.loads(path.read_text());old['mcp_servers']['ffsake']['args'][0]=str(child);self.assertEqual(new,old)
      self.assertEqual(new['mcp_servers']['ffsake']['tools']['ffsake_run_task']['approval_mode'],'prompt')
     else:
      old=json.loads(before);new=json.loads(path.read_text())
      if host=='claude':old['mcpServers']['ffsake']['args'][0]=str(child)
      else:old['mcp']['ffsake']['command'][1]=str(child);self.assertEqual(new['permission']['ffsake_ffsake_run_task'],'deny')
      self.assertEqual(new,old)
 def test_unrelated_repository_refused(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td).resolve();a=root/'a';b=root/'b';a.mkdir();b.mkdir()
   for p in (a,b):git(p,'init','-q','-b','main')
   with self.assertRaisesRegex(ValueError,'same Git repository'):install.plan_rebind(root/'prefix','codex',b,a)
 def test_wrong_root_and_disabled_server_refused(self):
  with tempfile.TemporaryDirectory() as td:
   base=Path(td).resolve();parent=base/'parent';child=base/'child';parent.mkdir();prefix=base/'prefix'
   git(parent,'init','-q','-b','main');git(parent,'config','user.name','Fixture');git(parent,'config','user.email','fixture@example.invalid')
   (parent/'data').write_text('original');git(parent,'add','data');git(parent,'commit','-qm','fixture');git(parent,'worktree','add','-qb','child',str(child))
   with patch.dict(os.environ,{'CODEX_HOME':str(base/'codex-home')},clear=False):
    path=child/'.codex/config.toml';path.parent.mkdir();content=install.desired_config(prefix,'codex',parent,'core')['.codex/config.toml']
    path.write_text(content.replace(str(parent),str(base/'wrong')))
    with self.assertRaisesRegex(ValueError,'expected parent'):install.plan_rebind(prefix,'codex',child,parent)
    path.write_text(content.replace('[mcp_servers.ffsake]\n','[mcp_servers.ffsake]\nenabled = false\n'))
    with self.assertRaisesRegex(ValueError,'disabled'):install.plan_rebind(prefix,'codex',child,parent)
    self.assertIn('enabled = false',path.read_text())
if __name__=='__main__':unittest.main()
