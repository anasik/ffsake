#!/usr/bin/env python3
"""User-local source installation, reviewable project setup and model-free doctor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

from agent_benchmark_suite import mcp_profile_preflight
from ffsake_setup import configurations

SOURCE = Path(__file__).resolve().parents[1]
BINARIES = ('ffsake', 'ffsake-mcp')


def fail(message):
    raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def revision():
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
    diff = subprocess.check_output(['git', 'diff', '--no-ext-diff', '--no-textconv', '--binary', 'HEAD', '--', 'Cargo.toml', 'Cargo.lock', 'crates'], cwd=SOURCE)
    names = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z', '--', 'crates'], cwd=SOURCE).split(b'\0')
    h = hashlib.sha256(diff)
    for item in sorted(filter(None, names)):
        p = SOURCE / os.fsdecode(item)
        if p.is_symlink() or not p.is_file(): fail('untracked source must be a regular file')
        h.update(item + b'\0'); h.update(bytes.fromhex(digest(p)))
    return commit + ('-dirty-' + h.hexdigest()[:16] if diff or any(names) else '')


def prefix_path(value):
    p = Path(value).expanduser()
    if not p.is_absolute(): fail('installation prefix must be absolute')
    if p.is_symlink(): fail('installation prefix cannot be a symlink')
    return p.resolve()


def current(prefix):
    link = prefix / 'current'
    if not link.exists() and not link.is_symlink(): return None
    if not link.is_symlink(): fail('existing current path is not an FFSAKE installation symlink')
    versions = prefix / 'versions'
    if versions.is_symlink(): fail('versions directory cannot be a symlink')
    target = link.resolve(strict=True)
    if target.parent != versions.resolve() or not target.is_dir(): fail('installation link points outside its managed versions directory')
    manifest_path = target / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file(): fail('installation manifest is missing or unsafe')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('format') != 1 or set(manifest.get('sha256', {})) != set(BINARIES): fail('installation manifest is invalid')
    for name in BINARIES:
        p = target / name
        if p.is_symlink() or not p.is_file() or not os.access(p, os.X_OK) or digest(p) != manifest['sha256'][name]: fail('installed binary is missing, not executable, or differs from its manifest: ' + name)
    return target, manifest


def replace_generation(prefix, profile, update):
    old = current(prefix)
    wanted = revision()
    if old and old[1]['source_revision'] == wanted:
        return {'status': 'unchanged', 'installed_revision': wanted, 'binary': str(prefix / 'current/ffsake-mcp')}
    if old and not update:
        return {'status': 'update_available', 'installed_revision': old[1]['source_revision'], 'source_revision': wanted, 'binary': str(prefix / 'current/ffsake-mcp'), 'next': 'Request host approval, then run the explicit update command.'}
    if update and not old: fail('no existing installation; run install first')
    prefix.mkdir(parents=True, exist_ok=True)
    versions = prefix / 'versions'
    if versions.is_symlink(): fail('versions directory cannot be a symlink')
    versions.mkdir(exist_ok=True)
    lock = prefix / '.install.lock'
    try: lock.mkdir()
    except FileExistsError: fail('installation lock exists; inspect it before removing it')
    try:
        # Recheck under the lock. Never silently replace a concurrent install.
        again = current(prefix)
        if (old[1] if old else None) != (again[1] if again else None): fail('installation changed concurrently; retry')
        command = ['cargo', 'build', '--locked', '--bin', 'ffsake', '--bin', 'ffsake-mcp']
        if profile == 'release': command.append('--release')
        with tempfile.TemporaryDirectory(prefix="ffsake-build-") as build_temp:
            build_env = {**os.environ, "CARGO_TARGET_DIR": os.environ.get("CARGO_TARGET_DIR", build_temp)}
            subprocess.run(command, cwd=SOURCE, env=build_env, check=True)
            # The source can change during a build. Do not publish an ambiguous binary.
            if revision() != wanted: fail('source changed during build; installation was not updated')
            target_dir = Path(build_env['CARGO_TARGET_DIR']).expanduser()
            if not target_dir.is_absolute(): target_dir = SOURCE / target_dir
            built = target_dir / profile
            with tempfile.TemporaryDirectory(prefix='.new-', dir=versions) as td:
                temp = Path(td)
                for name in BINARIES:
                    src = built / name
                    if not src.is_file() or not os.access(src, os.X_OK): fail('build did not produce executable ' + name)
                    shutil.copy2(src, temp / name)
                manifest = {'format': 1, 'source_revision': wanted, 'build_profile': profile,
                            'package_version': tomllib.loads((SOURCE / 'crates/fff-workspace/Cargo.toml').read_text())['package']['version'],
                            'sha256': {name: digest(temp / name) for name in BINARIES}}
                (temp / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
                result = mcp_profile_preflight('core', temp / 'ffsake-mcp')
                if not result['ok']: fail('built MCP catalog failed: ' + str(result.get('error')))
                generation = versions / ('v-' + wanted)
                if generation.exists() or generation.is_symlink(): fail('target generation already exists; inspect it before retrying')
                os.replace(temp, generation)
        pending = prefix / ('.current-' + str(os.getpid()))
        if pending.exists() or pending.is_symlink(): fail('temporary installation link already exists')
        try:
            os.symlink(os.path.relpath(generation, prefix), pending)
            os.replace(pending, prefix / 'current')
        finally:
            if pending.is_symlink(): pending.unlink()
        # Delete only an intact generation owned by this installer, never unknown files.
        retained = False
        if old:
            try:
                intact = (set(p.name for p in old[0].iterdir()) == set(BINARIES) | {'manifest.json'}
                          and not (old[0]/'manifest.json').is_symlink()
                          and json.loads((old[0]/'manifest.json').read_text()) == old[1]
                          and all(not (old[0]/name).is_symlink() and digest(old[0]/name) == old[1]['sha256'][name] for name in BINARIES))
                if intact:
                    shutil.rmtree(old[0])
                else:
                    retained = True
            except (OSError, ValueError):
                retained = True
        return {'status': 'updated' if old else 'installed', 'source_revision': wanted,
                'binary': str(prefix / 'current/ffsake-mcp'), 'reconnect': bool(old),
                'previous_generation_retained': retained}
    finally:
        lock.rmdir()


def json_content(path):
    if path.is_symlink(): fail('configuration path cannot be a symlink: ' + str(path))
    if not path.exists(): return {}
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result: fail('configuration contains duplicate key ' + key + ': ' + str(path))
            result[key] = value
        return result
    if path.stat().st_size > 1024 * 1024: fail('configuration exceeds 1 MiB: ' + str(path))
    value = json.loads(path.read_text(), object_pairs_hook=unique_object)
    if not isinstance(value, dict): fail('configuration must be a JSON object: ' + str(path))
    return value


def read_jsonc(path):
    """Read JSON-with-comments without rewriting it; strings remain untouched."""
    if path.is_symlink(): fail('configuration path cannot be a symlink: ' + str(path))
    if path.stat().st_size > 1024 * 1024: fail('configuration exceeds 1 MiB: ' + str(path))
    source = path.read_text(); out=[]; i=0; quoted=False; escape=False
    while i < len(source):
        c=source[i]
        if quoted:
            out.append(c)
            if escape: escape=False
            elif c=='\\': escape=True
            elif c=='"': quoted=False
            i+=1;continue
        if c=='"': quoted=True;out.append(c);i+=1;continue
        if source.startswith('//',i):
            end=source.find('\n',i+2);i=len(source) if end<0 else end;continue
        if source.startswith('/*',i):
            end=source.find('*/',i+2)
            if end<0: fail('unterminated JSONC comment: ' + str(path))
            i=end+2;continue
        out.append(c);i+=1
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: fail('configuration contains duplicate key ' + key + ': ' + str(path))
            result[key]=value
        return result
    source=''.join(out); out=[]; i=0; quoted=False; escape=False
    while i<len(source):
        c=source[i]
        if quoted:
            out.append(c)
            if escape: escape=False
            elif c=='\\': escape=True
            elif c=='"': quoted=False
        elif c=='"': quoted=True; out.append(c)
        elif c==',':
            j=i+1
            while j<len(source) and source[j].isspace(): j+=1
            if j==len(source) or source[j] not in '}]': out.append(c)
        else: out.append(c)
        i+=1
    result=json.loads(''.join(out),object_pairs_hook=unique)
    if not isinstance(result,dict): fail('configuration must be an object: ' + str(path))
    return result


def get_entries(data, key, location):
    entries=data.get(key,{})
    if not isinstance(entries,dict): fail('invalid MCP entries in '+str(location))
    for name, entry in entries.items():
        if name == 'ffsake' or not isinstance(entry,dict): continue
        cmd=entry.get('command'); executable=cmd[0] if isinstance(cmd,list) and cmd else cmd
        if isinstance(executable,str) and Path(executable).name == 'ffsake-mcp':
            fail('FFSAKE already registered under another name in '+str(location)+'; review manually')
    return entries

def outside_entries(host, project):
    """Inspect standard user/local scopes only; never print unrelated settings."""
    found=[]
    if host=='claude':
        configured=os.environ.get('CLAUDE_CONFIG_DIR')
        path=(Path(configured).expanduser()/'.claude.json') if configured else Path.home()/'.claude.json'
        if path.exists() or path.is_symlink():
            data=json_content(path)
            global_entry=get_entries(data,'mcpServers',path).get('ffsake')
            projects=data.get('projects',{})
            if not isinstance(projects,dict) or not isinstance(projects.get(str(project),{}),dict): fail('invalid Claude project configuration')
            local_entry=get_entries(projects.get(str(project),{}),'mcpServers',path).get('ffsake')
            if global_entry is not None: found.append((str(path)+' (user)',global_entry))
            if local_entry is not None: found.append((str(path)+' (project-local)',local_entry))
    elif host=='codex':
        path=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))).expanduser()/'config.toml'
        if path.exists() or path.is_symlink():
            if path.is_symlink(): fail('user Codex config is a symlink; review it manually')
            data=tomllib.loads(path.read_text());entry=get_entries(data,'mcp_servers',path).get('ffsake')
            if entry is not None: found.append((str(path),entry))
    else:
        config=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config'))).expanduser()/'opencode'
        for path in (config/'opencode.json',config/'opencode.jsonc'):
            if path.exists() or path.is_symlink():
                data=read_jsonc(path);entry=get_entries(data,'mcp',path).get('ffsake')
                if entry is not None: found.append((str(path),entry))
        for variable in ('OPENCODE_CONFIG','OPENCODE_CONFIG_CONTENT','OPENCODE_CONFIG_DIR'):
            if os.environ.get(variable): fail(variable+' may contain an overriding FFSAKE entry; review host configuration manually')
    return found


def project_entry(host, project):
    if host=='claude':
        path=project/'.mcp.json'
        return get_entries(json_content(path),'mcpServers',path).get('ffsake') if path.exists() or path.is_symlink() else None
    if host=='codex':
        path=project/'.codex/config.toml'
        if not path.exists() and not path.is_symlink(): return None
        if path.is_symlink(): fail('configuration file cannot be a symlink')
        return get_entries(tomllib.loads(path.read_text()),'mcp_servers',path).get('ffsake')
    found=[]
    for path in (project/'opencode.json',project/'opencode.jsonc',project/'.opencode/opencode.json',project/'.opencode/opencode.jsonc'):
        if path.exists() or path.is_symlink():
            entry=get_entries(read_jsonc(path),'mcp',path).get('ffsake')
            if entry is not None:
                if path.parent != project: fail('OpenCode FFSAKE entry in .opencode requires manual review')
                found.append(entry)
    if len(found)>1: fail('duplicate OpenCode FFSAKE project entries; review manually')
    return found[0] if found else None


def existing_scope(prefix, host, project, profile):
    outside=outside_entries(host,project);inside=project_entry(host,project)
    if len(outside)>1 or (outside and inside is not None):
        fail('duplicate FFSAKE MCP entries already exist across host scopes; review them manually')
    if not outside: return None
    location,entry=outside[0]
    wanted=desired_config(prefix,host,project,profile)
    if host=='codex':
        expected=tomllib.loads(wanted['.codex/config.toml'])['mcp_servers']['ffsake']
        matches=isinstance(entry,dict) and entry.get('command')==expected['command'] and entry.get('args')==expected['args'] and entry.get('enabled',True) is not False
    elif host=='claude':
        expected=json.loads(wanted['.mcp.json'])['mcpServers']['ffsake']
        matches=isinstance(entry,dict) and entry.get('command')==expected['command'] and entry.get('args')==expected['args'] and entry.get('type','stdio')=='stdio'
    else:
        expected=json.loads(wanted['opencode.json'])['mcp']['ffsake']
        matches=isinstance(entry,dict) and entry.get('type')=='local' and entry.get('command')==expected['command'] and entry.get('enabled',True) is not False
    if not matches: fail('existing FFSAKE MCP entry in '+location+' differs; review it manually before adding another')
    return location,entry

def desired_config(prefix, host, project, profile):
    return configurations(host, project, prefix / 'current/ffsake-mcp', profile=profile)


def plan_config(prefix, host, project, profile):
    desired = desired_config(prefix, host, project, profile)
    changes = []
    for rel, generated in desired.items():
        path = project / rel
        if path.parent.is_symlink(): fail('configuration directory cannot be a symlink: ' + str(path.parent))
        before = path.read_bytes() if path.exists() and not path.is_symlink() else None
        if host == 'codex':
            if path.is_symlink(): fail('configuration file cannot be a symlink')
            existing = tomllib.loads(before.decode()) if before is not None else {}
            wanted = tomllib.loads(generated)
            servers = existing.get('mcp_servers', {})
            if not isinstance(servers, dict): fail('Codex mcp_servers must be a table')
            old = servers.get('ffsake')
            expected = wanted['mcp_servers']['ffsake']
            if old is not None:
                if not isinstance(old, dict): fail('existing Codex FFSAKE entry must be a table')
                if old.get('command') != expected['command'] or old.get('args') != expected['args']: fail('existing Codex FFSAKE entry differs; review it manually')
                if old.get('enabled', True) is False: fail('existing Codex FFSAKE entry is disabled; review it manually')
                continue
            text = (before.decode().rstrip() + '\n\n' if before else '') + generated
            merged = tomllib.loads(text)
            for key, value in existing.items():
                if key != 'mcp_servers' and merged.get(key) != value: fail('cannot safely merge existing Codex configuration')
            for key, value in existing.get('mcp_servers', {}).items():
                if merged['mcp_servers'].get(key) != value: fail('cannot safely merge existing MCP server')
            after = text.encode(); added = [{'mcp_servers.ffsake': expected}]
        else:
            if host == 'opencode' and (project / 'opencode.jsonc').exists(): fail('existing opencode.jsonc requires manual review and merge; no file changed')
            if rel == '.claude/settings.json' and before is not None:
                # Existing user permission rules are authoritative and untouched.
                continue
            existing = json_content(path)
            wanted = json.loads(generated)
            added = []
            if rel == '.mcp.json':
                entries = existing.setdefault('mcpServers', {})
                if not isinstance(entries, dict): fail('mcpServers must be an object')
                if 'ffsake' in entries:
                    old = entries['ffsake']; expected = wanted['mcpServers']['ffsake']
                    if not isinstance(old, dict) or old.get('command') != expected['command'] or old.get('args') != expected['args'] or old.get('type', 'stdio') != 'stdio': fail('existing Claude FFSAKE entry differs; review it manually')
                if 'ffsake' not in entries: entries['ffsake'] = wanted['mcpServers']['ffsake']; added.append({'mcpServers.ffsake': entries['ffsake']})
            elif rel == '.claude/settings.json':
                # Do not change an existing settings file: its permissions may be deliberate.
                existing = wanted; added.append({'permissions.allow': wanted['permissions']['allow']})
            else:
                entries = existing.setdefault('mcp', {})
                if not isinstance(entries, dict): fail('OpenCode mcp must be an object')
                if 'ffsake' in entries:
                    old = entries['ffsake']; expected = wanted['mcp']['ffsake']
                    if not isinstance(old, dict) or old.get('type') != 'local' or old.get('command') != expected['command'] or old.get('enabled', True) is False: fail('existing OpenCode FFSAKE entry differs or is disabled; review it manually')
                if 'ffsake' not in entries: entries['ffsake'] = wanted['mcp']['ffsake']; added.append({'mcp.ffsake': entries['ffsake']})
                # Existing permission policy is authoritative. Do not override wildcard rules.
                if 'permission' not in existing:
                    existing['permission'] = wanted['permission']; added.append({'permission': wanted['permission']})
            if not added: continue
            after = (json.dumps(existing, indent=2, ensure_ascii=False) + '\n').encode()
        changes.append((path, before, after, added))
    return changes


def configure(prefix, host, project, profile, apply):
    if current(prefix) is None: fail('FFSAKE is not installed')
    existing = existing_scope(prefix, host, project, profile)
    changes = [] if existing else plan_config(prefix, host, project, profile)
    summary = [{'file': str(p), 'added': added} for p, _, _, added in changes]
    print(json.dumps({'status': 'planned' if changes and not apply else 'applied' if changes else 'unchanged',
                      'host': host, 'project': str(project), 'binary': str(prefix / 'current/ffsake-mcp'),
                      'server_args': [str(project), '--profile', profile], 'changes': summary, 'existing_scope': existing[0] if existing else None,
                      'note': 'Existing host permission settings and broader host policies remain authoritative.'}, indent=2))
    if not apply: return
    for path, before, after, _ in changes:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or (path.read_bytes() if path.exists() else None) != before: fail('configuration changed during review: ' + str(path))
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        fd, temporary = tempfile.mkstemp(prefix='.ffsake-config-', dir=path.parent)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, 'wb') as out: out.write(after); out.flush(); os.fsync(out.fileno())
            if path.is_symlink() or (path.read_bytes() if path.exists() else None) != before: fail('configuration changed before publication: ' + str(path))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)


def doctor(prefix, host, project, profile):
    item = current(prefix)
    if item is None: fail('FFSAKE is not installed')
    existing = existing_scope(prefix, host, project, profile)
    changes = [] if existing else plan_config(prefix, host, project, profile)
    if any(any(key in item for item in added for key in ('mcpServers.ffsake', 'mcp_servers.ffsake', 'mcp.ffsake')) for _, _, _, added in changes):
        fail('project configuration is incomplete; run configure to review changes')
    extras = {'codex': ('env', 'env_vars', 'cwd'), 'claude': ('env', 'cwd'), 'opencode': ('environment', 'cwd')}[host]
    if existing:
        entry = existing[1]
    elif host == 'codex':
        entry = tomllib.loads((project/'.codex/config.toml').read_text())['mcp_servers']['ffsake']
        extras = ('env', 'env_vars', 'cwd')
    elif host == 'claude':
        entry = json_content(project/'.mcp.json')['mcpServers']['ffsake']
        extras = ('env', 'cwd')
    else:
        entry = json_content(project/'opencode.json')['mcp']['ffsake']
        extras = ('environment', 'cwd')
    if any(entry.get(key) for key in extras):
        fail('existing FFSAKE entry has environment or cwd overrides; verify its effective connection in the host')
    binary = prefix / 'current/ffsake-mcp'
    # Use a disposable workspace: initialization must not write journal files in the user's project.
    with tempfile.TemporaryDirectory(prefix='ffsake-doctor-') as td:
        messages = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'ffsake-doctor','version':'1'}}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
        run = subprocess.run([str(binary), td, '--profile', profile], input=''.join(json.dumps(m)+'\n' for m in messages), capture_output=True, text=True, timeout=20)
        if run.returncode != 0: fail('MCP initialization failed (output withheld)')
        responses = [json.loads(line) for line in run.stdout.splitlines()]
        init = next((r for r in responses if r.get('id') == 1), None)
        listing = next((r for r in responses if r.get('id') == 2), None)
        if not init or init.get('result', {}).get('serverInfo', {}).get('name') != 'ffsake' or not listing or 'tools' not in listing.get('result', {}): fail('MCP handshake or discovery failed')
    catalog = mcp_profile_preflight(profile, binary)
    if not catalog['ok']: fail('MCP catalog check failed: ' + str(catalog.get('error')))
    source_revision = revision()
    print(json.dumps({'ok': True, 'model_calls': 0, 'installed_revision': item[1]['source_revision'],
                      'source_revision': source_revision, 'update_available': source_revision != item[1]['source_revision'],
                      'package_version': item[1]['package_version'],
                      'binary': str(binary), 'host': host, 'project': str(project),
                      'host_cli_available': bool(shutil.which({'claude':'claude','codex':'codex','opencode':'opencode'}[host])),
                      'mcp_initialized': True, 'tool_count': len(listing['result']['tools']),
                      'note': 'Project config checked; host trust, global overrides and current-session connection were not verified.'}, indent=2))


def uninstall(prefix):
    if not prefix.exists(): return {'status':'not_installed'}
    lock = prefix / '.install.lock'
    try: lock.mkdir()
    except FileExistsError: fail('installation lock exists; inspect it before removing it')
    try:
        old = current(prefix)
        if not old: return {'status':'not_installed'}
        if set(p.name for p in old[0].iterdir()) != set(BINARIES) | {'manifest.json'}: fail('installed generation has extra files; inspect before uninstalling')
        (prefix/'current').unlink(); shutil.rmtree(old[0])
        return {'status':'uninstalled', 'note':'Project files, journals and host configuration were not removed.'}
    finally:
        lock.rmdir()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', default=str(Path.home()/'.local/share/ffsake'), help='Absolute user-local installation directory')
    sub=parser.add_subparsers(dest='action', required=True)
    for name in ('install','update'):
        p=sub.add_parser(name);p.add_argument('--profile', choices=('release','debug'),default='release')
    for name in ('configure','doctor'):
        p=sub.add_parser(name);p.add_argument('--host', choices=('claude','codex','opencode'),required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--tool-profile',choices=('core','all'),default='core')
        if name=='configure':p.add_argument('--apply',action='store_true',help='Apply the reviewed project configuration')
    sub.add_parser('uninstall')
    args=parser.parse_args()
    try:
        prefix=prefix_path(args.prefix)
        if args.action in ('install','update'):
            print(json.dumps(replace_generation(prefix,args.profile,args.action=='update'),indent=2))
        elif args.action in ('configure','doctor'):
            project=args.project.expanduser().resolve(strict=True)
            if not project.is_dir():fail('project must be an existing directory')
            if args.action=='configure': configure(prefix,args.host,project,args.tool_profile,args.apply)
            else:doctor(prefix,args.host,project,args.tool_profile)
        else:print(json.dumps(uninstall(prefix),indent=2))
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError, tomllib.TOMLDecodeError) as e:
        print('ffsake-install: ' + str(e),file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
