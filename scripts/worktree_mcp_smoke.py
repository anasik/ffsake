#!/usr/bin/env python3
"""Check independently rooted MCP servers in disposable linked worktrees."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from workflow_smoke import MCP


def main():
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-child-worktree-') as temporary:
        base = Path(temporary).resolve()
        parent, child = base / 'parent', base / 'child'
        parent.mkdir()
        def git(*args):
            return subprocess.run(['git', '-C', str(parent), *args], check=True, capture_output=True)
        git('init', '-q', '-b', 'main')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        (parent / 'value.txt').write_text('before\n')
        git('add', 'value.txt')
        git('commit', '-qm', 'fixture')
        git('worktree', 'add', '-qb', 'child', str(child))
        policy = base / 'tasks.json'
        policy.write_text(json.dumps({'version': 1, 'tasks': {'test': {
            'kind': 'test', 'argv': [sys.executable, '-c', 'from pathlib import Path; assert Path("value.txt").read_text()=="after\\n"']}}}))
        p = MCP(binary, parent, policy)
        c = MCP(binary, child, policy)
        try:
            assert Path(p.call('ffsake_workspace_info', {})['root']) == parent
            assert Path(c.call('ffsake_workspace_info', {})['root']) == child
            c.call('ffsake_read', {'path': 'value.txt'})
            preview = c.call('ffsake_preview_edit', {'path': 'value.txt', 'old_text': 'before', 'new_text': 'after', 'expected_matches': 1})
            c.call('ffsake_apply', {'handle': preview['handle']})
            assert c.call('ffsake_run_task', {'name': 'test'})['success']
            assert (parent / 'value.txt').read_text() == 'before\n'
            assert (child / 'value.txt').read_text() == 'after\n'
        finally:
            p.close()
            c.close()
    print('MCP worktree roots, child edit, test task and parent isolation passed')


if __name__ == '__main__':
    main()
