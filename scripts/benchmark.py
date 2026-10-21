#!/usr/bin/env python3
"""Real executable workflow, JSONL protocol safety checks, and equal-phase shell baseline."""
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

BINARY = str(Path(sys.argv[1] if len(sys.argv) > 1 else 'target/debug/ffsake').resolve())


class Client:
    def __init__(self, root):
        self.process = subprocess.Popen([BINARY, 'serve', str(root)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.calls = 0

    def raw(self, line):
        self.process.stdin.write(line + '\n')
        self.process.stdin.flush()
        self.calls += 1
        response = self.process.stdout.readline()
        if not response:
            raise AssertionError(self.process.stderr.read())
        return json.loads(response)

    def call(self, **request):
        response = self.raw(json.dumps(request))
        assert response['ok'], response
        return response['result']

    def reject(self, **request):
        response = self.raw(json.dumps(request))
        assert not response['ok'], response

    def close(self):
        self.process.stdin.close()
        assert self.process.wait(timeout=30) == 0, self.process.stderr.read()


def check(root, original):
    assert {p.name: p.read_bytes() for p in root.glob('*.txt')} == original


def safety(root):
    client = Client(root)
    try:
        assert not client.raw('{broken')['ok']
        assert not client.raw('{"op":"refresh","unexpected":true}')['ok']
        for path in ['../escape.txt', str(root / 'a.txt'), '.ffsake/journal/a']:
            client.reject(op='read', path=path)
        client.reject(op='undo', transaction='../escape')
        client.reject(op='search', pattern='needle', limit=0)
        client.reject(op='apply', handle=9999)
        assert not client.raw('x' * (1024 * 1024 + 1))['ok']
        assert client.call(op='read', path='a.txt')['text'] == 'needle needle'
        # Scoped MatchSet, count guard, read-only preview, stale apply, undo conflict.
        target = {'include': [{'Exact': 'a.txt'}], 'exclude': []}
        found = client.call(op='search', pattern='needle', target=target, details=True)
        assert len(found['ranges']) == 2
        client.reject(op='preview_matches', handle=found['handle'], replacement='new', expected_matches=3)
        plan = client.call(op='preview_matches', handle=found['handle'], replacement='new', expected_matches=2)
        assert (root / 'a.txt').read_text() == 'needle needle\n'
        (root / 'a.txt').write_text('external\n')
        client.reject(op='apply', handle=plan['handle'])
        client.reject(op='preview_matches', handle=found['handle'], replacement='new', expected_matches=2)
        (root / 'a.txt').write_text('needle needle\n')
        transaction = client.call(op='apply', handle=plan['handle'])['transaction']
        assert (root / 'a.txt').read_text() == 'new new\n'
        client.reject(op='apply', handle=plan['handle'])
        (root / 'a.txt').write_text('conflict\n')
        client.reject(op='undo', transaction=transaction)
        assert (root / 'a.txt').read_text() == 'conflict\n'
        (root / 'a.txt').write_text('new new\n')
        client.call(op='undo', transaction=transaction)
        client.call(op='redo', transaction=transaction)
        client.call(op='undo', transaction=transaction)
        # Generic transform preview freezes source bytes as well.
        generic = client.call(op='preview', transform={'target': target, 'operations': [
            {'Replace': {'mode': 'Literal', 'pattern': 'needle', 'replacement': 'new', 'expected_matches': 2}}
        ], 'max_files': 1})
        (root / 'a.txt').write_text('different\n')
        client.reject(op='apply', handle=generic['handle'])
        (root / 'a.txt').write_text('needle needle\n')
        if hasattr(os, 'symlink'):
            with tempfile.TemporaryDirectory() as outside:
                external = Path(outside) / 'outside.txt'
                external.write_text('outside\n')
                (root / 'link').symlink_to(external)
                client.reject(op='read', path='link')
                client.reject(op='preview', transform={'target': {'include': [{'Exact': 'link'}], 'exclude': []}, 'operations': [{'DeleteFiles': None}], 'max_files': 1})
                assert external.read_text() == 'outside\n'
                (root / 'link').unlink()
        # A stale file rejects an entire multi-file preview before writing any file.
        (root / 'b.txt').write_text('needle\n')
        multi = client.call(op='preview', transform={'target': {'include': ['All'], 'exclude': []}, 'operations': [
            {'Replace': {'mode': 'Literal', 'pattern': 'needle', 'replacement': 'new', 'expected_matches': 3}}
        ], 'max_files': 2})
        (root / 'b.txt').write_text('outside edit\n')
        client.reject(op='apply', handle=multi['handle'])
        assert (root / 'a.txt').read_text() == 'needle needle\n'
        assert (root / 'b.txt').read_text() == 'outside edit\n'
        (root / 'b.txt').unlink()
        client.reject(op='preview', transform={'target': target, 'operations': [], 'max_files': 0})
        client.call(op='release', handle=found['handle'])
        client.reject(op='preview_matches', handle=found['handle'], replacement='new', expected_matches=2)
    finally:
        client.close()


# Each subprocess below is one shell tool request. Staging is explicit for review/undo.
SHELL_PREVIEW = '''
from pathlib import Path
import difflib
import shutil
root=Path('.')
stage=root/'.stage'; stage.mkdir()
for name in Path('.matches').read_text().splitlines():
 p=root/name
 before=p.read_text(); after=before.replace('needle','replacement')
 shutil.copyfile(p,stage/(p.name+'.before'))
 (stage/(p.name+'.after')).write_text(after)
 print(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=name,tofile=name)),end='')
'''
SHELL_APPLY = '''
from pathlib import Path
import shutil
for p in Path('.stage').glob('*.after'):
 dst=Path(p.name[:-6]); before=p.with_suffix('.before').read_bytes()
 assert dst.read_bytes()==before
 shutil.copyfile(p,dst)
 assert dst.read_bytes()==p.read_bytes()
'''
SHELL_UNDO = '''
from pathlib import Path
import shutil
for p in Path('.stage').glob('*.before'):
 dst=Path(p.name[:-7]); assert dst.read_bytes()==p.with_suffix('.after').read_bytes()
 shutil.copyfile(p,dst)
 assert dst.read_bytes()==p.read_bytes()
shutil.rmtree('.stage'); Path('.matches').unlink()
'''


def shell(root, code):
    return subprocess.run(['sh', '-c', code], cwd=root, check=True, capture_output=True).stdout


def python_shell(code):
    import shlex
    return 'python3 -c ' + shlex.quote(code)


def main():
    with tempfile.TemporaryDirectory(prefix='ffsake-bench-') as temp:
        base = Path(temp)
        safe = base / 'safety'; safe.mkdir()
        (safe / 'a.txt').write_text('needle needle\n')
        safety(safe)
        print('SAFETY: protocol recovery, scope, counts, preview purity, stale MatchSet/preview, replay rejection, undo conflicts, redo, symlink escape: PASS', flush=True)
        root = base / 'fixture'; root.mkdir()
        for i in range(200):
            (root / f'f{i:03}.txt').write_text(('α needle and needle\n' if i < 20 else 'ordinary text\n') * 10)
        original = {p.name: p.read_bytes() for p in root.glob('*.txt')}
        client = Client(root)
        resident_times, shell_times = [], []
        start = time.perf_counter()
        client.call(op='read', path='f000.txt', start=1, end=1)
        startup_ms = (time.perf_counter() - start) * 1000
        try:
            for _ in range(10):
                start = time.perf_counter()
                found = client.call(op='search', pattern='needle', limit=1000)
                assert found['matches'] == 400 and 'ranges' not in found, found
                plan = client.call(op='preview_matches', handle=found['handle'], replacement='replacement', expected_matches=400)
                assert plan['preview']['changed_files'] == 20
                check(root, original)
                result = client.call(op='apply', handle=plan['handle'])
                assert result['changed_files'] == 20
                for name, content in original.items():
                    assert (root / name).read_bytes() == content.replace(b'needle', b'replacement')
                client.call(op='undo', transaction=result['transaction'])
                resident_times.append((time.perf_counter() - start) * 1000)
                check(root, original)
                start = time.perf_counter()
                shell(root, "grep -l 'needle' ./*.txt > .matches")
                preview = shell(root, python_shell(SHELL_PREVIEW))
                assert b'-' in preview and b'+' in preview
                check(root, original)
                shell(root, python_shell(SHELL_APPLY))
                for name, content in original.items():
                    assert (root / name).read_bytes() == content.replace(b'needle', b'replacement')
                shell(root, python_shell(SHELL_UNDO))
                shell_times.append((time.perf_counter() - start) * 1000)
                check(root, original)
        finally:
            client.close()
        print(json.dumps({'files': 200, 'changed_files': 20, 'matches': 400, 'iterations': 10,
                          'resident_round_trips': 4, 'shell_round_trips': 4,
                          'resident_startup_ms': round(startup_ms, 3),
                          'resident_median_ms': round(statistics.median(resident_times), 3),
                          'shell_median_ms': round(statistics.median(shell_times), 3),
                          'resident_samples_ms': resident_times, 'shell_samples_ms': shell_times}, indent=2))


if __name__ == '__main__':
    main()
