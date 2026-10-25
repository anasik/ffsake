#!/usr/bin/env python3
"""Shared semantic MCP contract against real rust-analyzer and clangd, no models."""
import json, os, select, subprocess, sys, tempfile
from pathlib import Path
from search_archive_smoke import MCP as BaseMCP
class MCP(BaseMCP):
    def __init__(self,binary,root):
        self.proc=subprocess.Popen([str(binary),str(root)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.number=0
    def call(self,name,args,ok=True):
        self.number+=1
        self.proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':self.number,'method':'tools/call','params':{'name':name,'arguments':args}})+'\n');self.proc.stdin.flush()
        assert select.select([self.proc.stdout],[],[],40)[0],name
        raw=json.loads(self.proc.stdout.readline());v=json.loads(raw['result']['content'][0]['text'])
        assert v['ok']==ok,v
        return v.get('result',v)
def failure_checks(binary):
    """Second adapter rejects malformed/out-of-scope responses and concurrent changes."""
    with tempfile.TemporaryDirectory(prefix='ffsake-clangd-failures-') as td:
        base=Path(td); root=base/'workspace'; root.mkdir(); source=root/'main.cpp'
        fake=base/'clangd'
        fake.write_text("""#!/usr/bin/env python3
import json,os,sys,pathlib
def send(v):
 b=json.dumps(dict(jsonrpc='2.0',**v)).encode();sys.stdout.buffer.write(('Content-Length: %d\\r\\n\\r\\n'%len(b)).encode()+b);sys.stdout.buffer.flush()
while True:
 headers={}
 while True:
  line=sys.stdin.buffer.readline()
  if not line:sys.exit(0)
  if line==b'\\r\\n':break
  k,v=line.split(b':',1);headers[k.lower()]=v.strip()
 m=json.loads(sys.stdin.buffer.read(int(headers[b'content-length'])))
 if m.get('method')=='initialize':send({'id':m['id'],'result':{'capabilities':{'positionEncoding':'utf-16'}}})
 elif m.get('method')=='textDocument/didOpen':
  uri=m['params']['textDocument']['uri']
  send({'method':'textDocument/clangd.fileStatus','params':{'uri':uri,'state':'idle'}})
 elif m.get('method')=='textDocument/references':
  mode=os.environ['FFSAKE_CPP_FAILURE']
  if mode=='stale':pathlib.Path(os.environ['FFSAKE_CPP_SOURCE']).write_text('// preserved host change\\n')
  uri='file:///outside.cpp' if mode=='outside' else m['params']['textDocument']['uri']
  result=[{'uri':uri,'range':{'start':{'line':0,'character':4},'end':{'line':0,'character':999 if mode=='range' else 9}}}]
  send({'id':m['id'],'result':result})
""")
        fake.chmod(0o755)
        env=os.environ.copy()
        try:
            os.environ['PATH']=str(base)+os.pathsep+env.get('PATH','')
            os.environ['FFSAKE_CPP_SOURCE']=str(source)
            for mode in ['outside','range','stale']:
                source.write_text('int value;\n'); os.environ['FFSAKE_CPP_FAILURE']=mode
                m=MCP(binary,root)
                try:
                    m.call('ffsake_semantic',{'request':{'files':['main.cpp'],'path':'main.cpp','position':{'line':1,'column':4},'query':'references','context':{'language':'cpp','standard':'c++17'},'limit':10}},False)
                    assert source.read_text()==('// preserved host change\n' if mode=='stale' else 'int value;\n')
                finally:m.close()
        finally:
            os.environ.clear();os.environ.update(env)
def main():
    binary=Path(sys.argv[1] if len(sys.argv)>1 else 'target/debug/ffsake-mcp').resolve()
    failure_checks(binary)
    with tempfile.TemporaryDirectory(prefix='ffsake-semantic-') as td:
        root=Path(td)
        original={'context.hpp':'#pragma once\n// unchanged context\n','budget.hpp':'#pragma once\r\nnamespace plan { int budget(int input); }\r\n',
          'budget.cpp':'#include "budget.hpp"\r\nnamespace plan { int budget(int input) { return input + 3; } }\r\n',
          'main.cpp':'#include "budget.hpp"\r\n// 🚀 unrelated same-spelling symbol\r\nnamespace other { int budget() { return 9; } }\r\nint main() { return plan::budget(4) == 7 && other::budget() == 9 ? 0 : 1; }\r\n'}
        for p,s in original.items():(root/p).write_bytes(s.encode())
        m=MCP(binary,root)
        args={'files':list(original),'path':'budget.cpp','position':{'line':2,'column':21},'context':{'language':'cpp','standard':'c++17'},'limit':100}
        try:
            reports={}
            for query in ['definition','references','hover','symbols','diagnostics','inspect']:
                reports[query]=m.call('ffsake_semantic',{'request':dict(args,query=query)})
            assert reports['references']['matches']>=3,reports
            assert reports['diagnostics']['diagnostic_count']==0,reports['diagnostics']
            report=m.call('ffsake_semantic',{'request':dict(args,query='rename',new_name='joinBudget')})
            assert report['proposal']['text_edit_count']==3,report
            previewargs={'rename_set':report['rename_set'],'expected_path':report['path'],'expected_start_byte':report['prepared']['start_byte'],'expected_old_text':report['prepared']['old_text'],'expected_new_name':'joinBudget','expected_edits':3}
            preview=m.call('ffsake_preview_semantic_rename',previewargs)
            # Unedited context is guarded as well as edited files.
            (root/'context.hpp').write_bytes((original['context.hpp']+'// host edit\n').encode())
            m.call('ffsake_apply',{'handle':preview['handle']},False)
            assert (root/'budget.cpp').read_bytes()==original['budget.cpp'].encode()
            m.call('ffsake_preview_semantic_rename',previewargs,False)
            (root/'context.hpp').write_bytes(original['context.hpp'].encode())
            transaction=m.call('ffsake_apply',{'handle':preview['handle']})['transaction']
            assert 'other::budget()' in (root/'main.cpp').read_text()
            subprocess.run(['clang++','-std=c++17','budget.cpp','main.cpp','-o',str(root/'program')],cwd=root,check=True,capture_output=True)
            subprocess.run([str(root/'program')],check=True)
            after={p:(root/p).read_bytes() for p in original}
            m.call('ffsake_undo',{'transaction':transaction});assert all((root/p).read_bytes()==s.encode() for p,s in original.items())
            m.call('ffsake_redo',{'transaction':transaction});assert all((root/p).read_bytes()==s for p,s in after.items())
            m.call('ffsake_undo',{'transaction':transaction})
            (root/'sample.rs').write_text('fn value() -> i32 { 7 }\nfn main() { let _ = value(); }\n')
            rust={'files':['sample.rs'],'path':'sample.rs','position':{'line':1,'column':4},'context':{'language':'rust','crate_root':'sample.rs','edition':'2021'},'limit':100,'query':'inspect'}
            r=m.call('ffsake_semantic',{'request':rust}); assert r['references']['matches']==2,r
            for invalid in [dict(args,files=['../outside.cpp']),dict(args,context={'language':'cpp','standard':'bad'}),dict(args,files=['budget.cpp','budget.cpp']),dict(args,limit=0)]:
                m.call('ffsake_semantic',{'request':dict(invalid,query='references')},False)
            print(json.dumps({'servers':['rust-analyzer','clangd'],'cpp_references':reports['references']['matches'],'rename_edits':3,'external_compile_runtime':True,'stale_preview_and_apply':True,'history_exact':True,'model_calls':0},indent=2))
        finally:m.close()
if __name__=='__main__':main()
