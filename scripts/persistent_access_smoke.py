#!/usr/bin/env python3
"""Model-free MCP access integration. Host approval is tested separately."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


class Client:
    def __init__(self, binary, root, trusted=False):
        args = [str(binary), str(root)]
        if trusted:
            args += ['--trusted-host', 'claude-code']
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        self.n = 0

    def rpc(self, method, **params):
        self.n += 1
        self.p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.n,
                                      'method': method, 'params': params}) + '\n')
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        assert line, self.p.stderr.read()
        return json.loads(line)['result']

    def call(self, name, ok=True, **arguments):
        r = self.rpc('tools/call', name='ffsake_' + name, arguments=arguments)
        value = json.loads(r['content'][0]['text'])
        assert value['ok'] == ok, (name, value)
        return value.get('result', value)

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)
        assert self.p.returncode == 0, self.p.stderr.read()


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else 'target/debug/ffsake-mcp').resolve()
    with tempfile.TemporaryDirectory(prefix='ffsake-persistent-') as td:
        base = Path(td).resolve()
        root = base / 'primary'; root.mkdir()
        other = base / 'other'; other.mkdir()
        external = base / 'external'; external.mkdir()
        file = external / 'approved.txt'; file.write_text('alpha\n')
        neighbor = external / 'neighbor.txt'; neighbor.write_text('PRIVATE_NEIGHBOR\n')
        (root / 'local.txt').write_text('local\n')
        c = Client(binary, root)
        d = Client(binary, root)
        catalog = {t['name']: t for t in c.rpc('tools/list')['tools']}
        for name in ('open_access', 'remove_access'):
            assert '_meta' not in catalog['ffsake_' + name]
        assert catalog['ffsake_list_access']['annotations']['readOnlyHint'] is True
        c.call('read', False, path=str(file))
        # A denied host prompt means NO dispatch. No grant appears spontaneously.
        assert c.call('list_access')['grants'] == []
        c.call('open_access', False, path=str(file), kind='file', mode='read_only', approved=True)
        c.call('open_access', False, path='relative', kind='file', mode='read_only')
        c.call('open_access', path=str(file), kind='file', mode='read_only')
        assert c.call('read', path=str(file))['text'] == 'alpha'
        d.call('read', path=str(file))
        c.call('read', False, path=str(neighbor))
        c.call('list', False, path=str(external))
        c.call('read', False, path=str(external / '..' / 'external' / 'approved.txt'))
        c.call('preview_edit', False, path=str(file), old_text='alpha', new_text='beta', expected_matches=1)
        c.call('open_access', path=str(file), kind='file', mode='read_write')
        edit = dict(path=str(file), old_text='alpha', new_text='beta', expected_matches=1)
        preview = c.call('preview_edit', **edit)
        d.call('remove_access', path=str(file))
        error = c.call('apply', False, handle=preview['handle'])
        assert 'alpha' not in json.dumps(error) and file.read_text() == 'alpha\n'
        c.call('open_access', path=str(file), kind='file', mode='read_write')
        preview = c.call('preview_edit', **edit)
        file.write_text('host changed\n')
        c.call('apply', False, handle=preview['handle'])
        assert file.read_text() == 'host changed\n'
        file.write_text('alpha\n')
        preview = c.call('preview_edit', **edit)
        tx = c.call('apply', handle=preview['handle'])['transaction']
        d.call('remove_access', path=str(file))
        error = c.call('undo', False, transaction=tx)
        assert 'beta' not in json.dumps(error) and file.read_text() == 'beta\n'
        c.call('open_access', path=str(file), kind='file', mode='read_only')
        c.call('undo', False, transaction=tx)
        c.call('open_access', path=str(file), kind='file', mode='read_write')
        c.call('undo', transaction=tx)
        d.call('remove_access', path=str(file))
        c.call('redo', False, transaction=tx)
        assert file.read_text() == 'alpha\n'
        c.call('open_access', path=str(file), kind='file', mode='read_write')
        c.close()
        c = Client(binary, root)
        c.call('read', path=str(file))
        c.call('redo', transaction=tx)
        isolated = Client(binary, other)
        isolated.call('read', False, path=str(file)); isolated.close()
        legacy = Client(binary, root, trusted=True)
        legacy.call('read', path=str(file)); legacy.close()
        store = root / '.ffsake/native-access/grants.json'
        c.call('read', False, path=str(store))
        c.call('preview_create', False, path=str(store), content='{}')
        c.call('open_access', False, path=str(base), kind='directory', mode='read_write')
        c.call('preview_copy', False, **{'from': str(file), 'to': 'copied.txt'})
        folder = base / '.hidden'; folder.mkdir()
        source = folder / 'source.txt'; source.write_text('find_me\n')
        jar = folder / 'sources.jar'
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('Demo.java', 'class Demo {}')
        c.call('open_access', path=str(folder), kind='directory', mode='read_write')
        c.call('search', pattern='find_me', target={'include': [{'Exact': str(folder)}], 'exclude': []})
        c.call('archive', path=str(jar), entry='Demo.java')
        # Cached search handles must reauthorize before disclosing a preview.
        search = c.call('search', pattern='find_me', target={'include': [{'Exact': str(source)}], 'exclude': []})
        cpp = folder / 'sample.cpp'
        cpp.write_text('int value() { return 7; }\nint main() { return value()==7 ? 0 : 1; }\n')
        args = {'files': [str(cpp)], 'path': str(cpp), 'position': {'line': 1, 'column': 5},
                'context': {'language': 'cpp', 'standard': 'c++17'}, 'query': 'rename',
                'new_name': 'renamed', 'limit': 100}
        report = c.call('semantic', request=args)
        d.call('remove_access', path=str(folder))
        c.call('semantic', False, request=args)
        error = c.call('preview_semantic_rename', False, rename_set=report['rename_set'],
                      expected_path=report['path'], expected_start_byte=report['prepared']['start_byte'],
                      expected_old_text='value', expected_new_name='renamed', expected_edits=2)
        assert 'return 7' not in json.dumps(error) and cpp.read_text().startswith('int value()')
        c.call('archive', False, path=str(jar), entry='Demo.java')
        error = c.call('preview_matches', False, handle=search['handle'], replacement='bad', expected_matches=1)
        assert 'find_me' not in json.dumps(error)
        if os.name == 'posix':
            alias = base / 'alias'; alias.symlink_to(external, target_is_directory=True)
            c.call('open_access', False, path=str(alias / 'approved.txt'), kind='file', mode='read_only')
            assert store.stat().st_mode & 0o077 == 0
        c.close(); d.close()
        policy = base / 'host.json'
        policy.write_text(json.dumps({'version': 1, 'project': str(root), 'primary_mode': 'read_only', 'grants': []}))
        # Both legacy and current configurations must honor restrictive exports.
        for legacy in ([], ['--trusted-host', 'claude-code']):
            client = Client.__new__(Client); client.n = 0
            client.p = subprocess.Popen([str(binary), str(root), '--access', str(policy), *legacy],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert 'ffsake_open_access' not in {t['name'] for t in client.rpc('tools/list')['tools']}
            client.call('read', False, path=str(file))
            client.call('open_access', False, path=str(file), kind='file', mode='read_write')
            client.call('preview_edit', False, path='local.txt', old_text='local', new_text='bad', expected_matches=1)
            client.close()
    print('PASS persistent grants: restart, live revocation, isolation, rights, stale/history, archives, protected store. Native approval is tested separately.')


if __name__ == '__main__':
    main()
