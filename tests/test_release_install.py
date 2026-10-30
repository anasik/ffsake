"""Release installation through a synthetic HTTPS response and real MCP binaries."""
import hashlib,io,json,os,subprocess,sys,tarfile,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import ffsake_install as installer
import ffsake_releases as releases

class ReleaseTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  target=Path(os.environ.get('FFSAKE_TEST_BINARY_DIR',ROOT/'target/debug'))
  cls.archive=io.BytesIO()
  with tarfile.open(fileobj=cls.archive,mode='w:gz',compresslevel=1) as tar:
   for n in releases.BINARIES:tar.add(target/n,arcname=n)
  cls.data=cls.archive.getvalue();cls.asset_platform=releases.platform_key()
 def fake_get(self,url,limit):
  tag='v0.1.0-alpha.2' if 'alpha.2' in url else 'v0.1.0-alpha.1'
  name=f'ffsake-{tag}-{self.asset_platform}.tar.gz';base=f'https://github.com/anasik/ffsake/releases/download/{tag}/'
  if '/tags/' in url:return json.dumps({'draft':False,'tag_name':tag,'assets':[{'name':name,'browser_download_url':base+name},{'name':'SHA256SUMS','browser_download_url':base+'SHA256SUMS'}]}).encode()
  if url.endswith('SHA256SUMS'):return f'{hashlib.sha256(self.data).hexdigest()}  {name}\n'.encode()
  return self.data
 def test_lifecycle_failure_and_rollback(self):
  with tempfile.TemporaryDirectory() as td, patch.object(releases,'get',side_effect=self.fake_get):
   prefix=(Path(td)/'install').resolve();install=lambda version,update=False,pin=False: releases.install(prefix,version,update,pin,installer.current,installer.digest)
   a='v0.1.0-alpha.1';b='v0.1.0-alpha.2'
   self.assertEqual(install(a)['status'],'installed');before=(prefix/'current').resolve()
   self.assertEqual(install(a)['status'],'unchanged');self.assertEqual(install(a,pin=True)['status'],'pin_change_available')
   self.assertEqual(install(a,True,True)['status'],'pin_updated');self.assertEqual(len(list((prefix/'versions').iterdir())),1)
   self.assertEqual(install(b)['status'],'update_available')
   original=releases.get
   def corrupt(url,limit):
    data=self.fake_get(url,limit)
    return data+b'broken' if url.endswith('.tar.gz') else data
   with patch.object(releases,'get',side_effect=corrupt):
    with self.assertRaisesRegex(ValueError,'checksum mismatch'):install(b,True)
   self.assertEqual((prefix/'current').resolve(),before)
   project=Path(td)/'project';project.mkdir()
   running=subprocess.Popen([str(prefix/'current/ffsake-mcp'),str(project),'--profile','core'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   try:
    self.assertEqual(install(b,True,True)['status'],'updated');self.assertTrue(installer.current(prefix)[1]['pinned'])
    running.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list','params':{}})+'\n');running.stdin.flush()
    self.assertTrue(json.loads(running.stdout.readline())['result']['tools'])
   finally:
    running.stdin.close();running.wait(timeout=10);running.stdout.close();running.stderr.close()
   self.assertTrue(before.exists());self.assertEqual(releases.rollback(prefix,a,installer.current,installer.validate_generation)['status'],'rolled_back')
   self.assertEqual((prefix/'current').resolve(),before)
 def test_latest_requires_compatible_assets(self):
  platform_name=releases.platform_key()
  listing=[{'draft':False,'tag_name':'v0.1.0-alpha.2','assets':[{'name':'SHA256SUMS'}]},
           {'draft':False,'tag_name':'v0.1.0-alpha.1','assets':[{'name':'SHA256SUMS'},{'name':f'ffsake-v0.1.0-alpha.1-{platform_name}.tar.gz'}]}]
  with patch.object(releases,'get',return_value=json.dumps(listing).encode()):
   self.assertEqual(releases.latest(),'v0.1.0-alpha.1')
 def test_invalid_platform_and_archive(self):
  with self.assertRaisesRegex(ValueError,'unsupported'):releases.platform_key('Linux','arm64')
  for value in ('../../x','v0.1.0','v0.1.0-alpha.1/evil'):
   with self.assertRaises(ValueError):releases.version_key(value)
  with tempfile.TemporaryDirectory() as td:
   buf=io.BytesIO()
   with tarfile.open(fileobj=buf,mode='w:gz') as tar:
    item=tarfile.TarInfo('../evil');item.size=1;tar.addfile(item,io.BytesIO(b'x'))
   with self.assertRaises(ValueError):releases.unpack(buf.getvalue(),Path(td))
if __name__=='__main__':unittest.main()
