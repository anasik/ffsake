#!/usr/bin/env python3
"""Opt-in model-free clangd lifecycle timings; not a persistent production implementation.
Measures the same references request on pinned TinyXML2 source. Warm measurements
reuse unchanged documents only: they do not establish edit-invalidation correctness.
"""
import argparse,json,statistics,subprocess,tempfile,time
from pathlib import Path
from rust_lsp_probe import ProbeClient
class Client(ProbeClient):
    def __init__(self,root,sources):
        self.options={};self.deadline=time.monotonic()+30;self.buffer=bytearray();self.received=0;self.next_id=1
        self.proc=subprocess.Popen(['clangd','--enable-config=0','--background-index=0','--clang-tidy=0','--header-insertion=never','-j=2'],cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        init=self.request('initialize',{'processId':None,'rootUri':root.as_uri(),'capabilities':{'general':{'positionEncodings':['utf-16']},'offsetEncoding':['utf-16']},'initializationOptions':{'clangdFileStatus':True}})
        assert init['capabilities'].get('positionEncoding','utf-16')=='utf-16'
        self.send({'method':'initialized','params':{}})
        for p,s in sources.items():self.send({'method':'textDocument/didOpen','params':{'textDocument':{'uri':(root/p).as_uri(),'languageId':'cpp','version':1,'text':s}}})
        ready=set()
        while len(ready)<len(sources):
            m=self.receive()
            if m.get('method')=='textDocument/clangd.fileStatus' and m['params']['state']=='idle':ready.add(m['params']['uri'])
    def references(self,root):
        r=self.request('textDocument/references',{'textDocument':{'uri':(root/'tinyxml2.h').as_uri()},'position':{'line':1841,'character':16},'context':{'includeDeclaration':True}})
        assert len(r)==28,r
        return sorted((v['uri'],json.dumps(v['range'],sort_keys=True)) for v in r)
def main():
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('--iterations',type=int,default=5);a=p.parse_args();assert 1<=a.iterations<=10
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.source,text=True).strip();assert revision=='9148bdf719e997d1f474be6bcc7943881046dba1'
    sources={p:subprocess.check_output(['git','show','HEAD:'+p],cwd=a.source).decode() for p in ['tinyxml2.h','tinyxml2.cpp','xmltest.cpp']}
    with tempfile.TemporaryDirectory(prefix='ffsake-lifecycle-') as td:
        root=Path(td).resolve()
        for p,s in sources.items():(root/p).write_bytes(s.encode())
        commands=[{'directory':str(root),'file':str(root/p),'arguments':['clang++','-std=c++17','-D_FILE_OFFSET_BITS=64','-x','c++','-I',str(root),'-c',str(root/p)]} for p in sources]
        (root/'compile_commands.json').write_text(json.dumps(commands));cold=[];warm=[];baseline=None
        for _ in range(a.iterations):
            start=time.monotonic();c=Client(root,sources)
            try:
                result=c.references(root)
                if baseline is None:baseline=result
                assert result==baseline
            finally:c.close()
            cold.append(time.monotonic()-start)
        start=time.monotonic();c=Client(root,sources);startup=time.monotonic()-start
        try:
            for _ in range(a.iterations):
                start=time.monotonic();assert c.references(root)==baseline;warm.append(time.monotonic()-start)
        finally:c.close()
        print(json.dumps({'upstream_revision':revision,'clangd_version':subprocess.check_output(['clangd','--version'],text=True).strip(),'iterations':a.iterations,'files':len(sources),'source_bytes':sum(len(s.encode()) for s in sources.values()),'references':28,'cold_seconds':cold,'warm_seconds':warm,'retained_startup_seconds':startup,'cold_median_seconds':statistics.median(cold),'warm_median_seconds':statistics.median(warm),'equivalent_results':True,'unchanged_documents_only':True,'persistent_edit_invalidation_tested':False,'model_calls':0},indent=2))
if __name__=='__main__':main()
