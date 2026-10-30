"""Bounded GitHub release metadata, archive validation and installation."""
import hashlib, io, json, os, platform, re, shutil, subprocess, tarfile, tempfile, urllib.request
from pathlib import Path

API='https://api.github.com/repos/anasik/ffsake/releases'
TAG=re.compile(r'^v(\d+)\.(\d+)\.(\d+)-alpha\.(\d+)$')
MAX_ARCHIVE=128*1024*1024
BINARIES=('ffsake','ffsake-mcp')

def version_key(tag):
    match=TAG.fullmatch(tag)
    if not match: raise ValueError('release version must be vMAJOR.MINOR.PATCH-alpha.N')
    return tuple(map(int,match.groups()))

def platform_key(system=None,machine=None):
    pair=(system or platform.system(),machine or platform.machine())
    result={('Darwin','arm64'):'macos-arm64',('Linux','x86_64'):'linux-x86_64'}.get(pair)
    if result is None: raise ValueError('unsupported release platform: '+str(pair))
    return result

def get(url,limit):
    request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'ffsake-installer'})
    with urllib.request.urlopen(request,timeout=10) as response:
        data=response.read(limit+1)
        if len(data)>limit: raise ValueError('release response exceeds size limit')
        return data

def latest():
    data=json.loads(get(API+'?per_page=100',1024*1024))
    if not isinstance(data,list) or not all(isinstance(r,dict) for r in data): raise ValueError('invalid release listing')
    platform_name=platform_key()
    versions=[]
    for r in data:
        tag=r.get('tag_name','')
        names={a.get('name') for a in r.get('assets',[]) if isinstance(a,dict)}
        if not r.get('draft') and TAG.fullmatch(tag) and f'ffsake-{tag}-{platform_name}.tar.gz' in names and 'SHA256SUMS' in names:
            versions.append(tag)
    if not versions: raise ValueError('no published alpha release is available; use --source')
    return max(versions,key=version_key)

def release(version):
    version_key(version)
    data=json.loads(get(API+'/tags/'+version,1024*1024))
    if not isinstance(data,dict) or data.get('draft') or data.get('tag_name')!=version or not isinstance(data.get('assets'),list): raise ValueError('release metadata does not match version')
    assets={a['name']:a['browser_download_url'] for a in data.get('assets',[]) if isinstance(a,dict) and 'name' in a and 'browser_download_url' in a}
    name='ffsake-'+version+'-'+platform_key()+'.tar.gz'
    if name not in assets or 'SHA256SUMS' not in assets: raise ValueError('release does not provide this platform or checksum file')
    for url in (assets[name],assets['SHA256SUMS']):
        if not url.startswith('https://github.com/anasik/ffsake/releases/download/'+version+'/'): raise ValueError('unexpected release asset URL')
    sums=get(assets['SHA256SUMS'],65536).decode('ascii').splitlines()
    matches=[line.split()[0] for line in sums if len(line.split())==2 and line.split()[1].lstrip('*')==name]
    if len(matches)!=1 or not re.fullmatch('[0-9a-f]{64}',matches[0]): raise ValueError('missing or ambiguous archive checksum')
    archive=get(assets[name],MAX_ARCHIVE)
    if hashlib.sha256(archive).hexdigest()!=matches[0]: raise ValueError('release archive checksum mismatch')
    return name,archive

def unpack(archive,destination):
    seen=set()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive),mode='r|gz') as tar:
            for m in tar:
                if m.name not in BINARIES or m.name in seen: raise ValueError('archive must contain exactly the two binaries')
                if not m.isfile() or m.issym() or m.islnk() or m.size<=0 or m.size>256*1024*1024: raise ValueError('unsafe or oversized archive entry')
                seen.add(m.name);source=tar.extractfile(m)
                if source is None: raise ValueError('missing archive entry')
                with (destination/m.name).open('xb') as out: shutil.copyfileobj(source,out,1024*1024)
                (destination/m.name).chmod(0o755)
        if seen!=set(BINARIES): raise ValueError('archive must contain exactly the two binaries')
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError("invalid release archive") from exc

def install(prefix,version,update,explicit,current,digest):
    version_key(version); old=current(prefix)
    if old and old[1].get('release_version')==version:
        if old[1].get('pinned',False)==explicit:
            return {'status':'unchanged','installed_version':version,'binary':str(prefix/'current/ffsake-mcp')}
        if not update:
            return {'status':'pin_change_available','installed_version':version,'pinned':old[1].get('pinned',False),'next':'Run explicit update to change the pin.'}
        lock=prefix/'.install.lock'
        try: lock.mkdir()
        except FileExistsError: raise ValueError('installation lock exists; inspect it before removing it')
        try:
            if current(prefix)!=old: raise ValueError('installation changed concurrently; retry')
            manifest={**old[1], 'pinned':explicit}; path=old[0]/'manifest.json'
            fd,tmp=tempfile.mkstemp(prefix='.manifest-',dir=old[0])
            try:
                with os.fdopen(fd,'w') as out: json.dump(manifest,out,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
                if current(prefix)!=old: raise ValueError('installation changed concurrently; retry')
                os.replace(tmp,path)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
            return {'status':'pin_updated','installed_version':version,'pinned':explicit,'reconnect':True}
        finally:lock.rmdir()
    if old and not update: return {'status':'update_available','installed_version':old[1].get('release_version'),'available_version':version,'next':'Review and run explicit update.'}
    if not old and update: raise ValueError('no existing installation; run install first')
    prefix.mkdir(parents=True,exist_ok=True);versions=prefix/'versions'
    if versions.is_symlink(): raise ValueError('versions directory cannot be a symlink')
    versions.mkdir(exist_ok=True);lock=prefix/'.install.lock'
    try: lock.mkdir()
    except FileExistsError: raise ValueError('installation lock exists; inspect it before removing it')
    try:
        again=current(prefix)
        if (old[1] if old else None)!=(again[1] if again else None): raise ValueError('installation changed concurrently; retry')
        generation=versions/('release-'+version+('-pinned' if explicit else '-channel'))
        for suffix in ('-pinned','-channel'):
            candidate=versions/('release-'+version+suffix)
            if candidate.exists() or candidate.is_symlink(): raise ValueError('generation already exists; use rollback instead')
        _,archive=release(version)
        with tempfile.TemporaryDirectory(prefix='.new-',dir=versions) as td:
            temp=Path(td);unpack(archive,temp)
            with tempfile.TemporaryDirectory(prefix='ffsake-release-handshake-') as work:
                messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'ffsake-install','version':'1'}}}, {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
                init=subprocess.run([str(temp/'ffsake-mcp'),work,'--profile','core'],input=''.join(json.dumps(m)+'\n' for m in messages),text=True,capture_output=True,timeout=20,check=True)
                responses=[json.loads(line) for line in init.stdout.splitlines()]
                if len(responses)!=2 or responses[0].get('id')!=1 or responses[0].get('result',{}).get('serverInfo',{}).get('name')!='ffsake' or responses[1].get('id')!=2: raise ValueError('release MCP initialization failed')
                tools=responses[1].get('result',{}).get('tools',[])
                names=[tool.get('name') for tool in tools if isinstance(tool,dict)] if isinstance(tools,list) else []
                if not {'ffsake_read','ffsake_list','ffsake_search'}.issubset(names) or len(names)>256 or len(names)!=len(set(names)): raise ValueError('release MCP catalog is invalid')
            manifest={'format':1,'release_version':version,'source_revision':None,'package_version':version,'release_channel':'alpha','pinned':explicit,'sha256':{n:digest(temp/n) for n in BINARIES}}
            (temp/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
            os.replace(temp,generation)
        pending=prefix/('.current-'+str(os.getpid()))
        if pending.exists() or pending.is_symlink(): raise ValueError('temporary installation link already exists')
        try: os.symlink(os.path.relpath(generation,prefix),pending);os.replace(pending,prefix/'current')
        finally:
            if pending.is_symlink(): pending.unlink()
        return {'status':'updated' if old else 'installed','installed_version':version,'pinned':explicit,'reconnect':bool(old),'previous_generation_retained':bool(old),'binary':str(prefix/'current/ffsake-mcp')}
    finally: lock.rmdir()

def rollback(prefix,version,current,validate_generation,source=False):
    if source:
        if not re.fullmatch(r'[0-9a-f]{40}(?:-dirty-[0-9a-f]{16})?',version): raise ValueError('invalid source revision')
    else: version_key(version)
    old=current(prefix)
    if old is None: raise ValueError('no existing installation')
    versions=(prefix/'versions').resolve()
    candidates=[versions/('v-'+version)] if source else [versions/('release-'+version+suffix) for suffix in ('-pinned','-channel')]
    candidates=[p for p in candidates if p.exists() or p.is_symlink()]
    if len(candidates)!=1: raise ValueError('no unique retained generation for requested version')
    target,manifest=validate_generation(prefix,candidates[0])
    if manifest.get('source_revision' if source else 'release_version')!=version: raise ValueError('retained generation identity mismatch')
    if target==old[0]: return {'status':'unchanged','installed_version':version}
    lock=prefix/'.install.lock'
    try:lock.mkdir()
    except FileExistsError:raise ValueError('installation lock exists; inspect it before removing it')
    try:
        if current(prefix)!=old:raise ValueError('installation changed concurrently; retry')
        validate_generation(prefix,target)
        link=prefix/('.current-'+str(os.getpid()))
        if link.exists() or link.is_symlink():raise ValueError('temporary installation link already exists')
        try:os.symlink(os.path.relpath(target,prefix),link);os.replace(link,prefix/'current')
        finally:
            if link.is_symlink():link.unlink()
        return {'status':'rolled_back','installed_version':version,'pinned':manifest.get('pinned',False),'reconnect':True}
    finally:lock.rmdir()
