"""Shared strict assertions for MCP startup metadata in integration tests."""
import json


def assert_only_empty_access_store(root):
    """Default MCP may initialize access storage, but no journal or grants."""
    metadata = root / '.ffsake'
    expected = {'native-access', 'native-access/grants.json', 'native-access/store.lock'}
    assert {str(p.relative_to(metadata)) for p in metadata.rglob('*')} == expected
    assert (metadata / 'native-access').is_dir()
    for name in ('grants.json', 'store.lock'):
        path = metadata / 'native-access' / name
        assert path.is_file() and not path.is_symlink()
    assert json.loads((metadata / 'native-access/grants.json').read_text()) == {
        'version': 1, 'project': str(root.resolve()),
        'primary_mode': 'read_write', 'grants': [],
    }
    assert (metadata / 'native-access/store.lock').read_bytes() == b''
