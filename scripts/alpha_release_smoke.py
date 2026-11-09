#!/usr/bin/env python3
"""Verify one packaged release archive and a clean CLI-first installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ffsake_releases


def run(command, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, timeout=300, **kwargs)
    if result.returncode != 0:
        raise AssertionError((command, result.stdout[-2000:], result.stderr[-2000:]))
    return result


def tool(installer, prefix, *args, env):
    result = run([sys.executable, str(installer), '--prefix', str(prefix), *args], env=env)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--sums', type=Path)
    parser.add_argument('--installer', type=Path,
                        default=Path(__file__).with_name('ffsake_install.py'))
    args = parser.parse_args()

    archive = args.archive.read_bytes()
    ffsake_releases.version_key(args.version)
    if args.sums:
        entries = [line.split() for line in args.sums.read_text().splitlines() if line.strip()]
        matches = [value for value, name in entries if name.lstrip('*') == args.archive.name]
        assert len(matches) == 1, 'missing or ambiguous checksum entry'
        assert hashlib.sha256(archive).hexdigest() == matches[0], 'archive checksum mismatch'

    with tempfile.TemporaryDirectory(prefix='ffsake-release-smoke-') as temporary:
        base = Path(temporary).resolve()
        prefix = base / 'prefix'
        generation = prefix / 'versions' / ('release-' + args.version + '-channel')
        generation.mkdir(parents=True)
        ffsake_releases.unpack(archive, generation)
        manifest = {
            'format': 1, 'release_version': args.version, 'source_revision': None,
            'package_version': args.version, 'release_channel': 'alpha', 'pinned': False,
            'sha256': {name: hashlib.sha256((generation / name).read_bytes()).hexdigest()
                       for name in ffsake_releases.BINARIES},
        }
        (generation / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (prefix / 'current').symlink_to(Path('versions') / generation.name)

        env = {**os.environ,
               'XDG_CONFIG_HOME': str(base / 'xdg'),
               'CLAUDE_CONFIG_DIR': str(base / 'claude-home'),
               'CODEX_HOME': str(base / 'codex-home')}
        for name in ('xdg', 'claude-home', 'codex-home'):
            (base / name).mkdir()

        projects = {}
        for host in ('claude', 'opencode'):
            project = base / ('project-' + host)
            project.mkdir()
            if host == 'claude':
                (project / '.claude').mkdir()
                (project / '.claude' / 'settings.json').write_text('{"custom":true}\n')
            else:
                (project / '.opencode').mkdir()
                (project / '.opencode' / 'opencode.json').write_text('{"custom":true,"permission":{"edit":"ask"}}\n')
            projects[host] = project
            planned = tool(args.installer, prefix, 'configure', '--host', host,
                           '--project', str(project), '--cli-first', env=env)
            assert planned['status'] == 'planned'
            applied = tool(args.installer, prefix, 'configure', '--host', host,
                           '--project', str(project), '--cli-first', '--apply', env=env)
            assert applied['status'] == 'applied', applied
            doctor = tool(args.installer, prefix, 'doctor', '--host', host,
                          '--project', str(project), '--cli-first', env=env)
            assert doctor['ok'] and doctor['cli_first']['guidance'] and doctor['cli_first']['redirect'], doctor
            assert json.loads((project / ('.claude/settings.json' if host == 'claude'
                                          else '.opencode/opencode.json')).read_text())['custom'] is True

        project = projects['claude']
        (project / 'app.py').write_text('value = 1\n')
        cli = prefix / 'current' / 'ffsake'
        read = json.loads(run([str(cli), '.', '--request',
                               '{"op":"read","path":"app.py"}'], cwd=project).stdout)
        assert read['ok'] and 'value = 1' in read['result']['text']
        preview = json.loads(run([str(cli), '.', '--request',
                                  '{"op":"preview_edit","path":"app.py","old_text":"value = 1",'
                                  '"new_text":"value = 2","expected_matches":1}'], cwd=project).stdout)
        assert preview['ok'] and (project / 'app.py').read_text() == 'value = 1\n'
        applied = json.loads(run([str(cli), '.', '--apply', preview['token']], cwd=project).stdout)
        assert applied['ok'] and (project / 'app.py').read_text() == 'value = 2\n'
        undone = json.loads(run([str(cli), '.', '--request',
                                 json.dumps({'op': 'undo', 'transaction': applied['transaction']})],
                                cwd=project).stdout)
        assert undone['ok'] and (project / 'app.py').read_text() == 'value = 1\n'

        for host, project in projects.items():
            removed = tool(args.installer, prefix, 'configure', '--host', host,
                           '--project', str(project), '--remove', '--apply', env=env)
            assert removed['status'] == 'applied', removed
        tool(args.installer, prefix, 'uninstall', env=env)

    print(json.dumps({
        'archive': args.archive.name,
        'version': args.version,
        'checksum_verified': bool(args.sums),
        'mcp_initialized': True,
        'claude_cli_first': True,
        'opencode_cli_first': True,
        'clean_edit_and_undo': True,
        'project_removal': True,
        'uninstall': True,
        'model_calls': 0,
    }, indent=2))


if __name__ == '__main__':
    main()
