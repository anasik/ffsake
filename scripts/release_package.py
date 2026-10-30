#!/usr/bin/env python3
"""Package two already-built native binaries and validate the packaged MCP server."""
import argparse, hashlib, json, os, platform, tarfile, tempfile
from pathlib import Path
from agent_benchmark_suite import mcp_profile_preflight
from ffsake_releases import platform_key, unpack, version_key

def main():
 p=argparse.ArgumentParser();p.add_argument('--version',required=True);p.add_argument('--binary-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 version_key(a.version);machine=platform_key();a.output.mkdir(parents=True,exist_ok=True)
 archive=a.output/f'ffsake-{a.version}-{machine}.tar.gz'
 with tarfile.open(archive,'w:gz') as tar:
  for name in ('ffsake','ffsake-mcp'):
   path=a.binary_dir/name
   if not path.is_file() or not os.access(path,os.X_OK):raise ValueError('missing executable '+str(path))
   tar.add(path,arcname=name,recursive=False)
 with tempfile.TemporaryDirectory(prefix='ffsake-package-check-') as td:
  temp=Path(td);unpack(archive.read_bytes(),temp)
  check=mcp_profile_preflight('core',temp/'ffsake-mcp')
  if not check['ok']:raise ValueError('packaged MCP tool discovery failed: '+str(check))
  import subprocess
  messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'release-check','version':'1'}}},{'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
  result=subprocess.run([str(temp/'ffsake-mcp'),td,'--profile','core'],input=''.join(json.dumps(x)+'\n' for x in messages),text=True,capture_output=True,timeout=30,check=True)
  responses=[json.loads(line) for line in result.stdout.splitlines()]
  assert responses[0]['result']['serverInfo']['name']=='ffsake' and responses[1]['result']['tools']
  if os.environ.get('FFSAKE_RELEASE_VERSION'):
   assert responses[0]['result']['serverInfo']['version']==a.version, 'packaged binary release version mismatch'
 sha=hashlib.sha256(archive.read_bytes()).hexdigest()
 print(json.dumps({'archive':str(archive),'sha256':sha,'platform':machine,'mcp_initialized':True,'tools':len(responses[1]['result']['tools'])},indent=2))
if __name__=='__main__':main()
