#!/usr/bin/env python3
"""Model-free hidden/ignored search, bounded archive, and guarded-history checks."""
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import warnings
import zipfile


class MCP:
    def __init__(self, binary, root):
        self.proc = subprocess.Popen([str(binary), str(root), '--profile', 'core'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.number = 0
    def call(self, name, arguments, ok=True):
        self.number += 1
        self.proc.stdin.write(json.dumps({'jsonrpc':'2.0','id':self.number,'method':'tools/call',
            'params':{'name':name,'arguments':arguments}})+'\n')
        self.proc.stdin.flush()
        assert select.select([self.proc.stdout], [], [], 15)[0], name
        response = json.loads(self.proc.stdout.readline())
        value = json.loads(response['result']['content'][0]['text'])
        assert response['id'] == self.number and value['ok'] == ok, value
        return value.get('result', value)
    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill(); self.proc.wait()
            raise
        assert self.proc.returncode == 0, self.proc.stderr.read()


def main():
    binary = Path(sys.argv[1] if len(sys.argv)>1 else 'target/debug/ffsake-mcp').resolve()
    matrix = []
    for git, rule in [(False,None),(True,None),(True,'.gitignore'),(False,'.ignore')]:
        with tempfile.TemporaryDirectory(prefix='ffsake-hidden-') as td:
            root = Path(td)
            (root/'.scratch_calcite').mkdir()
            (root/'.scratch_calcite/Probe.java').write_text('class Needle {}\n')
            if git: subprocess.run(['git','init','-q',td],check=True)
            if rule: (root/rule).write_text('.scratch_calcite/\n')
            m = MCP(binary, root)
            try:
                assert m.call('ffsake_read',{'path':'.scratch_calcite/Probe.java'})
                indexed=m.call('ffsake_search',{'pattern':'Needle','details':True})
                direct=m.call('ffsake_search',{'pattern':'Needle','target':{'include':[{'Exact':'.scratch_calcite'}],'exclude':[]},'details':True})
                assert direct['matches']==1 and direct['ranges'][0]['start_byte']==6, direct
                assert direct['coverage']['backend']=='explicit_paths'
                excluded=m.call('ffsake_search',{'pattern':'Needle','target':{'include':[{'Exact':'.scratch_calcite'}],'exclude':[{'Glob':'**/*.java'}]}})
                assert excluded['matches']==0 and excluded['coverage']['target_exclusions']==1
                m.call('ffsake_search',{'pattern':'Needle','offset':1,'target':{'include':[{'Exact':'.scratch_calcite'}],'exclude':[]}},False)
                (root/'.scratch_calcite/Probe.java').write_text('class HostChanged {}\n')
                m.call('ffsake_preview_matches',{'handle':direct['handle'],'replacement':'Other','expected_matches':1},False)
                assert (root/'.scratch_calcite/Probe.java').read_text()=='class HostChanged {}\n'
                matrix.append({'git':git,'ignore_file':rule,'indexed_matches':indexed['matches'],'explicit_matches':direct['matches']})
            finally: m.close()
    with tempfile.TemporaryDirectory(prefix='ffsake-archive-') as td:
        root=Path(td)
        java='package demo;\n// café\npublic class Planner {\n  public static final int LIMIT = 37;\n}\n'
        def archive(name, entries, compression=zipfile.ZIP_DEFLATED):
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                with zipfile.ZipFile(root/name,'w',compression=compression) as z:
                    for key,value in entries: z.writestr(key,value)
        archive('source.jar',[('demo/',b''),('demo/Planner.java',java),('binary.class',b'\x00\xff')])
        before=(root/'source.jar').read_bytes()
        m=MCP(binary,root)
        rejected=0
        try:
            listing=m.call('ffsake_archive',{'path':'source.jar','limit':1})
            assert listing['total_entries']==3 and listing['next_offset']==1
            page=m.call('ffsake_archive',{'path':'source.jar','offset':1,'limit':2})
            assert page['next_offset'] is None
            source=m.call('ffsake_archive',{'path':'source.jar','entry':'demo/Planner.java','start':2,'end':4,'expected_archive_hash':listing['archive_hash']})
            assert source['text']==''.join(java.splitlines(keepends=True)[1:4]) and source['read_only']
            assert not (root/'demo').exists() and (root/'source.jar').read_bytes()==before
            for args in [{'entry':'../escape'},{'entry':'/escape'},{'entry':'binary.class'},{'entry':'demo/'},{'entry':'missing'}, {'entry':'demo/Planner.java','start':0}, {'entry':'demo/Planner.java','end':3000}, {'offset':99}, {'limit':201}]:
                m.call('ffsake_archive',{'path':'source.jar',**args},False);rejected+=1
            for index,name in enumerate(['../escape','/escape','..\\escape','C:/escape','a/../b','a//b']):
                archive(f'bad{index}.jar',[(name,'x')])
                m.call('ffsake_archive',{'path':f'bad{index}.jar'},False);rejected+=1
            archive('duplicate.zip',[('a','x'),('a','y')])
            link=zipfile.ZipInfo('link');link.create_system=3;link.external_attr=0o120777<<16
            archive('symlink.zip',[(link,'../outside')])
            archive('bomb.zip',[('bomb','x'*(1024*1024))])
            archive('large.zip',[('large','x'*(1024*1024+1))],zipfile.ZIP_STORED)
            archive('output.zip',[('line','x'*(300*1024))],zipfile.ZIP_STORED)
            archive('many.zip',[(f'f{i}','') for i in range(4097)])
            for filename,args in [('duplicate.zip',{}),('symlink.zip',{}),('bomb.zip',{}),('large.zip',{'entry':'large'}),('output.zip',{'entry':'line'}),('many.zip',{})]:
                m.call('ffsake_archive',{'path':filename,**args},False);rejected+=1
            archive('forged.zip',[('a','x'*(2*1024*1024))])
            data=bytearray((root/'forged.zip').read_bytes());central=data.index(b'PK\x01\x02')
            data[22:26]=(1).to_bytes(4,'little');data[central+24:central+28]=(1).to_bytes(4,'little')
            (root/'forged.zip').write_bytes(data)
            m.call('ffsake_archive',{'path':'forged.zip','entry':'a'},False);rejected+=1
            for filename,field,value in [('encrypted.zip',6,1),('method.zip',8,99)]:
                archive(filename,[('a','content')],zipfile.ZIP_STORED)
                data=bytearray((root/filename).read_bytes());central=data.index(b'PK\x01\x02')
                data[field:field+2]=value.to_bytes(2,'little')
                data[central+field+2:central+field+4]=value.to_bytes(2,'little')
                (root/filename).write_bytes(data)
                m.call('ffsake_archive',{'path':filename},False);rejected+=1
            with (root/'oversized.jar').open('wb') as handle: handle.truncate(32*1024*1024+1)
            m.call('ffsake_archive',{'path':'oversized.jar'},False);rejected+=1
            archive('wide.zip',[(str(i)+'x'*3000,'') for i in range(100)],zipfile.ZIP_STORED)
            m.call('ffsake_archive',{'path':'wide.zip','limit':100},False);rejected+=1
            assert len(m.call('ffsake_archive',{'path':'wide.zip','limit':10})['entries'])==10
            archive('crc.zip',[('a','original')],zipfile.ZIP_STORED)
            damaged=bytearray((root/'crc.zip').read_bytes());damaged[31]^=1;(root/'crc.zip').write_bytes(damaged)
            m.call('ffsake_archive',{'path':'crc.zip','entry':'a'},False);rejected+=1
            with zipfile.ZipFile(root/'source.jar','a') as z:z.writestr('new.txt','new')
            m.call('ffsake_archive',{'path':'source.jar','entry':'demo/Planner.java','expected_archive_hash':listing['archive_hash']},False);rejected+=1
            if hasattr(os,'symlink'):
                (root/'alias.jar').symlink_to(root/'source.jar')
                m.call('ffsake_archive',{'path':'alias.jar'},False);rejected+=1
            for path in ['../outside.jar','.ffsake/hidden.jar']:
                m.call('ffsake_archive',{'path':path},False);rejected+=1
            # Whole-transaction history must not undo through a later edit.
            (root/'history.txt').write_text('first=old\nsecond=old\n')
            def edit(old,new):
                p=m.call('ffsake_preview_edit',{'path':'history.txt','old_text':old,'new_text':new,'expected_matches':1})
                return m.call('ffsake_apply',{'handle':p['handle']})['transaction']
            earlier=edit('first=old','first=new');edit('second=old','second=new')
            m.call('ffsake_undo',{'transaction':earlier},False)
            assert (root/'history.txt').read_text()=='first=new\nsecond=new\n'
            inverse=edit('first=new','first=old')
            assert (root/'history.txt').read_text()=='first=old\nsecond=new\n'
            m.call('ffsake_undo',{'transaction':inverse})
            assert (root/'history.txt').read_text()=='first=new\nsecond=new\n'
        finally: m.close()
    print(json.dumps({'ok':True,'search_matrix':matrix,'archive_rejections':rejected,'no_extraction':True,'stale_match_rejection':True,'guarded_selective_inverse':True,'model_calls':0},indent=2))

if __name__=='__main__': main()
