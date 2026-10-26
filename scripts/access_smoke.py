#!/usr/bin/env python3
"""Host-exported scopes through real JSONL/MCP; no model calls."""
import json, os, subprocess, sys, tempfile, zipfile
from pathlib import Path

class Client:
    def __init__(self,binary,root,policy=None,mcp=False,session='test-host-session'):
        args=[str(binary),str(root)] if mcp else [str(binary),'serve',str(root)]
        if policy:args+=['--access',str(policy)]
        self.p=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env={**os.environ,'FFSAKE_HOST_SESSION_ID':session})
        self.mcp=mcp;self.n=0
    def send(self,v):self.p.stdin.write(json.dumps(v)+'\n');self.p.stdin.flush()
    def receive(self):
        line=self.p.stdout.readline()
        assert line,(self.p.poll(),self.p.stderr.read())
        return json.loads(line)
    def call(self,op,ok=True,**kw):
        self.n+=1
        self.send({'jsonrpc':'2.0','id':self.n,'method':'tools/call','params':{'name':'ffsake_'+op,'arguments':kw}} if self.mcp else {'op':op,**kw})
        r=self.receive()
        if self.mcp:r=json.loads(r['result']['content'][0]['text'])
        assert r['ok']==ok,(op,kw,r)
        return r.get('result',r)
    def close(self):
        self.p.stdin.close();self.p.wait(timeout=10)
        assert self.p.returncode==0,self.p.stderr.read()

def main():
    binary=Path(sys.argv[1] if len(sys.argv)>1 else 'target/debug/ffsake-mcp').resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-access-') as temp:
        base=Path(temp).resolve();root=base/'primary';root.mkdir();outside=base/'external';outside.mkdir();directory=base/'directory';directory.mkdir()
        file=outside/'approved.txt';neighbor=outside/'unapproved.txt';file.write_text('alpha\n');neighbor.write_text('private neighbor\n');(root/'local.txt').write_text('local alpha\n');(directory/'source.txt').write_text('directory alpha\n')
        policy=base/'host-authorization.json'
        def grant(path,mode='read_write',kind='file',**extra):return dict(path=str(path),mode=mode,kind=kind,lifetime='project',**extra)
        def export(grants,primary_mode='read_write'):
            replacement=policy.with_suffix('.tmp');replacement.write_text(json.dumps({'version':1,'project':str(root),'primary_mode':primary_mode,'grants':grants}));replacement.replace(policy)
        grants=[grant(file),grant(directory,kind='directory')];export(grants)
        c=Client(binary.with_name('ffsake'),root,policy)
        assert c.call('read',path=str(file))['text']=='alpha'
        c.call('read',False,path=str(neighbor));c.call('list',False,path=str(outside));c.call('read',False,path=str(outside/'..'/'external'/'approved.txt'))
        assert c.call('read',path=str(root/'local.txt'))['path']=='local.txt'
        assert c.call('search',pattern='alpha',target={'include':[{'Exact':str(file)}],'exclude':[]},details=True)['matches']==1
        assert c.call('search',pattern='directory',target={'include':[{'Exact':str(directory)}],'exclude':[]})['matches']==1
        assert c.call('search',pattern='directory')['matches']==0
        c.call('preview_edit',False,path=str(neighbor),old_text='private',new_text='changed',expected_matches=1)
        preview=c.call('preview_edit',path=str(file),old_text='alpha',new_text='beta',expected_matches=1)
        export([]);c.call('apply',False,handle=preview['handle']);assert file.read_text()=='alpha\n'
        export(grants);file.write_text('host change\n');c.call('apply',False,handle=preview['handle']);assert file.read_text()=='host change\n'
        preview=c.call('preview_edit',path=str(file),old_text='host change',new_text='beta',expected_matches=1)
        result=c.call('apply',handle=preview['handle']);tx=result['transaction'];tx=tx['id'] if isinstance(tx,dict) else tx
        export([grant(file,'read_only'),grant(directory,kind='directory')]);c.call('undo',False,transaction=tx);assert file.read_text()=='beta\n'
        c.call('preview_edit',False,path=str(file),old_text='beta',new_text='gamma',expected_matches=1)
        c.call('preview_delete',False,path=str(file));c.call('preview_create',False,path=str(file),content='x')
        export(grants);c.call('undo',transaction=tx);assert file.read_text()=='host change\n'
        export([]);c.call('redo',False,transaction=tx);assert file.read_text()=='host change\n'
        export(grants);c.call('redo',transaction=tx);assert file.read_text()=='beta\n'
        c.call('preview_copy',False,**{'from':str(file),'to':str(directory/'copy.txt')})
        c.call('preview_files',False,operations=[{'op':'create','path':'local-new','content':'x'},{'op':'create','path':str(directory/'new'),'content':'x'}]);assert not (root/'local-new').exists()
        p=c.call('preview_create',path=str(directory/'newparent'/'new.txt'),content='new')
        tx2=c.call('apply',handle=p['handle'])['transaction'];c.call('undo',transaction=tx2);assert not (directory/'newparent').exists();c.call('redo',transaction=tx2)
        (directory/'alias').symlink_to(neighbor);c.call('read',False,path=str(directory/'alias'));c.call('preview_create',False,path=str(directory/'alias'/'child'),content='x')
        c.call('read',False,path=str(root/'.ffsake'/'journal'))
        c.call('read',False,path=str(policy));c.call('preview_edit',False,path=str(policy),old_text='project',new_text='anything',expected_matches=2)
        # Invalid host updates fail closed, then recover only after host correction.
        policy.write_text('{}');c.call('read',False,path=str(file));export(grants);assert c.call('read',path=str(file))['text']=='beta'
        jar=directory/'source.jar'
        with zipfile.ZipFile(jar,'w') as z:z.writestr('pkg/Main.java','class Main {}\n')
        c.call('archive',path=str(jar),entry='pkg/Main.java')
        # Cross-root semantic context is rejected; real servers use safe relative
        # snapshots but return absolute external source identities.
        cpp=directory/'sample.cpp';cpp.write_text('int value() { return 7; }\nint main() { return value()==7 ? 0 : 1; }\n')
        args={'files':[str(cpp)],'path':str(cpp),'position':{'line':1,'column':5},'context':{'language':'cpp','standard':'c++17'},'query':'rename','new_name':'renamed','limit':100}
        report=c.call('semantic',request=args);assert report['path']==str(cpp),report
        preview=c.call('preview_rust_rename',rename_set=report['rename_set'],expected_path=report['path'],expected_start_byte=report['prepared']['start_byte'],expected_old_text='value',expected_new_name='renamed',expected_edits=2)
        transaction=c.call('apply',handle=preview['handle'])['transaction']
        subprocess.run(['clang++',str(cpp),'-o',str(base/'program')],check=True,capture_output=True);subprocess.run([str(base/'program')],check=True)
        c.call('undo',transaction=transaction)
        rust=directory/'sample.rs';rust.write_text('fn value() -> i32 { 7 }\nfn main() { let _ = value(); }\n')
        rustargs={'files':[str(rust)],'path':str(rust),'position':{'line':1,'column':4},'context':{'language':'rust','crate_root':str(rust),'edition':'2021'},'query':'inspect','limit':100}
        report=c.call('semantic',request=rustargs);assert report['references']['matches']==2 and all(r['path']==str(rust) for r in report['references']['ranges']),report
        (root/'other.cpp').write_text('int other;\n')
        c.call('semantic',False,request={**args,'files':[str(cpp),str(root/'other.cpp')]})
        # Mixed transactions recheck every member before touching any source.
        p=c.call('preview_files',operations=[{'op':'edit','path':str(directory/'source.txt'),'old_text':'alpha','new_text':'mixed','expected_matches':1},{'op':'create_directory','path':str(directory/'mixed-dir')}])
        mixed=c.call('apply',handle=p['handle'])['transaction'];before=(directory/'source.txt').read_bytes()
        export([]);c.call('undo',False,transaction=mixed);assert (directory/'source.txt').read_bytes()==before and (directory/'mixed-dir').exists()
        export(grants);c.call('undo',transaction=mixed)
        export(grants,primary_mode='read_only');c.call('read',path='local.txt');c.call('preview_edit',False,path='local.txt',old_text='alpha',new_text='bad',expected_matches=1)
        export(grants)
        c.close()
        # Persistent project grant and journal identities survive restart.
        c=Client(binary.with_name('ffsake'),root,policy);c.call('undo',transaction=tx2);c.call('read',path=str(file));c.close()
        session=grant(file);session.update(lifetime='session',session_id='test-host-session');export([session])
        c=Client(binary.with_name('ffsake'),root,policy);c.call('read',path=str(file));c.close()
        c=Client(binary.with_name('ffsake'),root,policy,session='other-session');c.call('read',False,path=str(file));c.close()
        once={**session,'lifetime':'once','approval_id':'unique-approval-test-0001'};export([once])
        c=Client(binary.with_name('ffsake'),root,policy);c.call('read',path=str(file));c.call('read',False,path=str(file));c.close()
        c=Client(binary.with_name('ffsake'),root,policy);c.call('read',False,path=str(file));c.close()
        # Actual MCP roots/list exchange, notification revocation before response,
        # and no rights gained from roots absent an explicit host export.
        export([grant(file,require_mcp_root=True)])
        c=Client(binary,root,policy,mcp=True)
        c.send({'jsonrpc':'2.0','id':0,'method':'initialize','params':{'capabilities':{'roots':{'listChanged':True}}}});assert c.receive()['result']['protocolVersion']=='2025-11-25'
        c.send({'jsonrpc':'2.0','method':'notifications/initialized'});req=c.receive();assert req['method']=='roots/list'
        c.send({'jsonrpc':'2.0','id':req['id'],'result':{'roots':[{'uri':root.as_uri()},{'uri':outside.as_uri()}]}})
        c.call('read',path=str(file));c.call('read',False,path=str(neighbor))
        c.send({'jsonrpc':'2.0','method':'notifications/roots/list_changed'});req=c.receive()
        c.call('read',False,path=str(file))
        c.send({'jsonrpc':'2.0','id':req['id'],'result':{'roots':[{'uri':root.as_uri()}]}});c.call('read',False,path=str(file))
        c.send({'jsonrpc':'2.0','method':'notifications/roots/list_changed'});req=c.receive()
        c.send({'jsonrpc':'2.0','id':req['id'],'result':{'roots':[{'uri':outside.as_uri()}]}});c.call('read',path=str(file))
        c.call('read',False,path=str(file),grant=True)
        c.close()
        print(json.dumps({'absolute_and_exact_file_access':True,'neighbor_denied':True,'read_only':True,'revocation_apply_history':True,'stale_preview':True,'cross_root_rejected':True,'persistent_journal':True,'session_lifetime':True,'once_survives_restart':True,'live_mcp_roots':True,'manual_host_export_retry':True,'native_host_approval_retry':'unsupported; not simulated'},indent=2))
if __name__=='__main__':main()
