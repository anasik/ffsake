#!/usr/bin/env python3
"""Disposable end-to-end installation/configuration/update/uninstall checks."""
import json, os, shutil, subprocess, sys, tempfile, tomllib
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]

def command(args,cwd=None,env=None,ok=True):
    p=subprocess.run(args,cwd=cwd,env=env,text=True,capture_output=True,timeout=300)
    if (p.returncode==0)!=ok:raise AssertionError((args,p.returncode,p.stdout[-2000:],p.stderr[-2000:]))
    return p

def main():
    with tempfile.TemporaryDirectory(prefix='ffsake-alpha-smoke-') as td:
        base=Path(td).resolve();clone=base/'source';prefix=base/'install'
        command(['git','clone','--quiet','--no-hardlinks',str(SOURCE),str(clone)])
        # Include the working installer when exercising uncommitted development changes.
        for name in ('ffsake_install.py','ffsake_releases.py'):
            shutil.copy2(SOURCE/'scripts'/name,clone/'scripts'/name)
        command(['git','config','user.name','Alpha Fixture'],clone);command(['git','config','user.email','fixture@example.invalid'],clone)
        command(['git','add','scripts/ffsake_install.py','scripts/ffsake_releases.py'],clone)
        if subprocess.run(['git','diff','--cached','--quiet'],cwd=clone).returncode:
            command(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false','commit','-qm','installer fixture'],clone)
        env={**os.environ,'CARGO_TARGET_DIR':os.environ.get('FFSAKE_ALPHA_TEST_TARGET',str(base/'cargo-target'))}
        env.update({key:str(base/value) for key,value in [('CLAUDE_CONFIG_DIR','host-claude'),('CODEX_HOME','host-codex'),('XDG_CONFIG_HOME','host-xdg')]})
        for key in ('OPENCODE_CONFIG','OPENCODE_CONFIG_CONTENT','OPENCODE_CONFIG_DIR'): env.pop(key,None)
        for key in ('CLAUDE_CONFIG_DIR','CODEX_HOME','XDG_CONFIG_HOME'): Path(env[key]).mkdir()
        def tool(*args,ok=True):
            p=command([sys.executable,str(clone/'scripts/ffsake_install.py'),'--prefix',str(prefix),*args],cwd=clone,env=env,ok=ok)
            return json.loads(p.stdout) if ok else p.stderr
        installed=tool('install','--source','--profile','debug');assert installed['status']=='installed'
        binary=prefix/'current/ffsake-mcp';assert binary.is_file()
        before=binary.read_bytes();again=tool('install','--source','--profile','debug');assert again['status']=='unchanged' and binary.read_bytes()==before
        # Reuse compatible user-scope entries; refuse conflicts and duplicates.
        for host in ('claude','codex','opencode'):
            root=base/('global-'+host);root.mkdir()
            args=[str(root),'--profile','core']; executable=str(binary)
            if host=='claude':
                config=Path(env['CLAUDE_CONFIG_DIR'])/'.claude.json'
                config.write_text(json.dumps({'mcpServers':{'ffsake':{'command':executable,'args':args}}}))
            elif host=='codex':
                config=Path(env['CODEX_HOME'])/'config.toml'
                config.write_text('[mcp_servers.ffsake]\ncommand = '+json.dumps(executable)+'\nargs = '+json.dumps(args)+'\n')
            else:
                config=Path(env['XDG_CONFIG_HOME'])/'opencode/opencode.jsonc';config.parent.mkdir()
                entry={'type':'local','command':[executable,*args]}
                config.write_text('{// https://example.invalid\n  "mcp": { "ffsake": '+json.dumps(entry)+', /* comment */ },\n}')
            original=config.read_bytes()
            assert tool('configure','--host',host,'--project',str(root),'--apply')['status']=='unchanged'
            assert tool('doctor','--host',host,'--project',str(root))['ok'] and config.read_bytes()==original
            assert not (root/({'claude':'.mcp.json','codex':'.codex/config.toml','opencode':'opencode.json'}[host])).exists()
            config.unlink()
        root=base/'global-conflict';root.mkdir();config=Path(env['CLAUDE_CONFIG_DIR'])/'.claude.json'
        config.write_text(json.dumps({'mcpServers':{'ffsake':{'command':'other'}}}))
        assert 'differs' in tool('configure','--host','claude','--project',str(root),'--apply',ok=False)
        assert not (root/'.mcp.json').exists();config.unlink()
        root=base/'alternate';root.mkdir();(root/'.mcp.json').write_text(json.dumps({'mcpServers':{'alternate':{'command':str(binary)}}}))
        assert 'another name' in tool('configure','--host','claude','--project',str(root),'--apply',ok=False)
        projects={name:base/name for name in ('claude','codex','opencode')}
        for p in projects.values():p.mkdir()
        c=projects['claude'];(c/'.claude').mkdir();(c/'.claude/settings.json').write_text('{"permissions":{"deny":["Bash(rm *)"]},"custom":true}\n')
        settings=(c/'.claude/settings.json').read_bytes();(c/'.mcp.json').write_text('{"mcpServers":{"other":{"command":"other"}},"custom":1}\n')
        d=projects['codex'];(d/'.codex').mkdir();(d/'.codex/config.toml').write_text('# existing comment\nmodel = "fixture"\n[mcp_servers.other]\ncommand = "other"\n')
        o=projects['opencode'];(o/'opencode.json').write_text('{"mcp":{"other":{"type":"local","command":["other"]}},"permission":{"*":"allow"},"custom":true}\n')
        for host,root in projects.items():
            guidance=root/('CLAUDE.md' if host=='claude' else 'AGENTS.md');guidance.write_text('# Existing instructions\nKeep me.\n')
            names={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            plan=tool('configure','--host',host,'--project',str(root),'--guidance');assert plan['status']=='planned' and any('guidance' in str(item['added']) for item in plan['changes'])
            assert names=={p:p.read_bytes() for p in names}
            assert tool('configure','--host',host,'--project',str(root),'--guidance','--apply')['status']=='applied'
            saved={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            assert tool('configure','--host',host,'--project',str(root),'--guidance','--apply')['status']=='unchanged'
            assert guidance.read_text().startswith('# Existing instructions\nKeep me.\n')
            assert guidance.read_text().count('<!-- ffsake-guidance:start -->') == 1
            assert saved=={p:p.read_bytes() for p in saved}
            checked=tool('doctor','--host',host,'--project',str(root));assert checked['ok'] and checked['mcp_initialized'] and checked['model_calls']==0
        assert (c/'.claude/settings.json').read_bytes()==settings
        assert json.loads((c/'.mcp.json').read_text())['mcpServers']['other']['command']=='other'
        assert tomllib.loads((d/'.codex/config.toml').read_text())['mcp_servers']['other']['command']=='other'
        assert json.loads((o/'opencode.json').read_text())['permission']=={'*':'allow'}
        full=base/'full-profile';full.mkdir()
        assert tool('configure','--host','claude','--project',str(full),'--tool-profile','all','--apply')['status']=='applied'
        assert tool('doctor','--host','claude','--project',str(full),'--tool-profile','all')['tool_count']>36
        conflict=base/'conflict';conflict.mkdir();(conflict/'.mcp.json').write_text('{"mcpServers":{"ffsake":{"command":"other"}}}')
        data=(conflict/'.mcp.json').read_bytes();assert 'differs' in tool('configure','--host','claude','--project',str(conflict),'--apply',ok=False);assert (conflict/'.mcp.json').read_bytes()==data
        duplicate=base/'duplicate';duplicate.mkdir();(duplicate/'.mcp.json').write_text('{"other":1,"other":2}')
        data=(duplicate/'.mcp.json').read_bytes();assert 'duplicate key' in tool('configure','--host','claude','--project',str(duplicate),'--apply',ok=False);assert (duplicate/'.mcp.json').read_bytes()==data
        jsonc=base/'jsonc';jsonc.mkdir();(jsonc/'opencode.jsonc').write_text('// preserve me\n{}')
        assert 'manual review' in tool('configure','--host','opencode','--project',str(jsonc),'--apply',ok=False) and not (jsonc/'opencode.json').exists()
        # A source update is detected but never installed by install.
        lib=clone/'crates/fff-workspace/src/lib.rs';lib.write_text(lib.read_text()+'\n// isolated update fixture\n')
        command(['git','add','crates/fff-workspace/src/lib.rs'],clone)
        command(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false','commit','-qm','update fixture'],clone)
        notice=tool('install','--source','--profile','debug');assert notice['status']=='update_available' and binary.read_bytes()==before
        child=subprocess.Popen([str(binary),str(c),'--profile','core'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        updated=tool('update','--source','--profile','debug');assert updated['status']=='updated' and updated['reconnect']
        assert len(list((prefix/'versions').iterdir()))==2 and updated['previous_generation_retained']
        child.stdin.write('{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n');child.stdin.flush();assert 'ffsake_read' in child.stdout.readline()
        child.stdin.close();assert child.wait(timeout=10)==0;child.stdout.close();child.stderr.close()
        assert tool('rollback','--revision',installed['source_revision'])['status']=='rolled_back'
        assert tool('rollback','--revision',updated['source_revision'])['status']=='rolled_back'
        assert tool('doctor','--host','claude','--project',str(c))['ok']
        journal=c/'.ffsake/journal';journal.mkdir(parents=True,exist_ok=True);(journal/'keep').write_text('do not delete')
        extra=(prefix/'current').resolve()/'user-file';extra.write_text('keep')
        assert 'extra files' in tool('uninstall',ok=False) and extra.read_text()=='keep'
        extra.unlink()
        assert tool('uninstall')['status']=='uninstalled' and (journal/'keep').read_text()=='do not delete' and (c/'.mcp.json').exists()
        assert tool('uninstall')['status']=='not_installed'
        print(json.dumps({'install':True,'repeat_no_change':True,'three_host_configurations':True,'existing_settings_preserved':True,'conflict_refused':True,'mcp_initialize_and_discovery':True,'no_silent_upgrade':True,'atomic_update_and_running_server':True,'safe_uninstall':True},indent=2))
if __name__=='__main__':main()
