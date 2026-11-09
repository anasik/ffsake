#!/usr/bin/env python3
"""Model-free one-shot CLI semantic-rename integration test.

Exercises the real rust-analyzer adapter through `ffsake . --request` and
`--apply`: same-spelling locals and parameters must stay untouched, the
guarded preview must write nothing, apply must update every intended binding,
undo must restore byte-exact sources, and a changed analysis source must reject
at apply. No model calls.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


FILES = {
    'Cargo.toml': '[package]\nname = "scope-demo"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n',
    'src/lib.rs': 'pub mod billing;\npub mod shipping;\n\n/// total sums all entries in the report.\npub fn total(items: &[u32]) -> u32 {\n    items.iter().copied().sum()\n}\n',
    'src/billing.rs': 'use crate::total;\n\npub fn invoice(values: &[u32]) -> u32 {\n    total(values)\n}\n\npub fn label(total: u32) -> String {\n    // total is a parameter here, not the crate helper.\n    format!("billing:{total}")\n}\n',
    'src/shipping.rs': 'use crate::total;\n\npub fn quote(values: &[u32]) -> u32 {\n    let total = values.iter().copied().max().unwrap_or(0);\n    let _ = total;\n    1\n}\n',
    'src/main.rs': 'use scope_demo::{billing, shipping, total};\n\nfn main() {\n    let total = total(&[1, 2, 3]);\n    println!("{total} {} {}", billing::invoice(&[4, 5]), shipping::quote(&[6]));\n}\n',
}

LIB_ONLY = ['src/lib.rs', 'src/billing.rs', 'src/shipping.rs']
ALL = ['src/lib.rs', 'src/billing.rs', 'src/shipping.rs', 'src/main.rs']
def context(files):
    value = {'language': 'rust', 'crate_root': 'src/lib.rs', 'edition': '2021'}
    if 'src/main.rs' in files:
        value['crates'] = [
            {'root': 'src/lib.rs'},
            {'root': 'src/main.rs', 'dependencies': {'scope_demo': 'src/lib.rs'}},
        ]
    return value


def request(binary, root, value):
    completed = subprocess.run([str(binary), '.', '--request', json.dumps(value)],
                               cwd=root, text=True, capture_output=True, timeout=180)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f'non-JSON CLI output: {completed.stdout!r} {completed.stderr!r}')


def apply(binary, root, token):
    completed = subprocess.run([str(binary), '.', '--apply', token],
                               cwd=root, text=True, capture_output=True, timeout=180)
    return json.loads(completed.stdout)


def run(command, root):
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=300)
    return completed.returncode, completed.stdout, completed.stderr


def snapshot(root):
    return {name: (root / name).read_bytes() for name in FILES}


def write_fixture(root):
    for name, text in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    run(['git', 'init', '-q'], root)


def semantic(query, files, new_name=None):
    value = {
        'op': 'semantic',
        'request': {'files': files, 'path': 'src/lib.rs',
                    'position': {'line': 5, 'column': 7}, 'query': query,
                    'context': context(files), 'limit': 100},
    }
    if new_name is not None:
        value['request']['new_name'] = new_name
    return value


def preview_request(files, expected_edits):
    return {
        'op': 'preview_semantic_rename',
        'request': semantic('rename', files, 'sum_all')['request'],
        'expected_path': 'src/lib.rs',
        'expected_start_byte': 85,
        'expected_old_text': 'total',
        'expected_new_name': 'sum_all',
        'expected_edits': expected_edits,
    }


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else 'target/debug/ffsake').resolve(strict=True)
    if shutil.which('rust-analyzer') is None or shutil.which('cargo') is None:
        print(json.dumps({'ok': True, 'skipped': 'rust-analyzer or cargo unavailable',
                          'model_calls': 0}, indent=2))
        return
    checks = {}
    with tempfile.TemporaryDirectory(prefix='ffsake-cli-semantic-') as temporary:
        root = Path(temporary).resolve()
        write_fixture(root)
        original = snapshot(root)

        references = request(binary, root, semantic('references', LIB_ONLY))
        assert references['ok'], references
        ranges = {(item['path'], item['line'], item['start_byte'], item['end_byte'])
                  for item in references['result']['ranges']}
        checks['same_spelling_references_isolated'] = ranges == {
            ('src/lib.rs', 5, 85, 90),
            ('src/billing.rs', 1, 11, 16),
            ('src/billing.rs', 4, 63, 68),
            ('src/shipping.rs', 1, 11, 16),
        }
        assert checks['same_spelling_references_isolated'], ranges

        legacy = request(binary, root, {
            'op': 'preview_rust_rename', 'rename_set': 1,
            'expected_path': 'src/lib.rs', 'expected_start_byte': 85,
            'expected_old_text': 'total', 'expected_new_name': 'sum_all',
            'expected_edits': 6,
        })
        checks['resident_handle_error_is_actionable'] = (
            not legacy['ok'] and 'preview_semantic_rename' in legacy['error'])
        assert checks['resident_handle_error_is_actionable'], legacy

        preview = request(binary, root, preview_request(ALL, 6))
        assert preview['ok'], preview
        checks['preview_token_issued'] = isinstance(preview.get('token'), str)
        checks['preview_changed_four_files'] = preview['result']['preview']['changed_files'] == 4
        assert checks['preview_token_issued'] and checks['preview_changed_four_files'], preview
        checks['preview_wrote_nothing'] = snapshot(root) == original
        assert checks['preview_wrote_nothing']
        diffs = {entry['path']: entry['unified_diff']
                 for entry in preview['result']['preview']['files']}
        checks['preview_preserves_locals'] = (
            'pub fn label(total: u32)' in diffs['src/billing.rs']
            and 'let total = values.iter().copied()' in diffs['src/shipping.rs']
            and '+    let total = sum_all(&[1, 2, 3]);' in diffs['src/main.rs'])
        assert checks['preview_preserves_locals'], diffs

        applied = apply(binary, root, preview['token'])
        checks['apply_ok'] = applied['ok'] and applied.get('changed_files') == 4
        assert checks['apply_ok'], applied
        checks['intended_bindings_renamed'] = (
            (root / 'src/lib.rs').read_text().count('sum_all') == 1
            and 'use crate::sum_all;' in (root / 'src/billing.rs').read_text()
            and 'use crate::sum_all;' in (root / 'src/shipping.rs').read_text()
            and 'use scope_demo::{billing, shipping, sum_all};' in (root / 'src/main.rs').read_text())
        checks['locals_unchanged'] = (
            'pub fn label(total: u32)' in (root / 'src/billing.rs').read_text()
            and 'let total = values.iter().copied()' in (root / 'src/shipping.rs').read_text()
            and 'let total = sum_all(&[1, 2, 3]);' in (root / 'src/main.rs').read_text())
        assert checks['intended_bindings_renamed'] and checks['locals_unchanged']

        code, stdout, stderr = run(['cargo', 'build', '--quiet'], root)
        checks['cargo_build'] = code == 0
        assert checks['cargo_build'], (stdout, stderr)
        code, stdout, stderr = run(['cargo', 'test', '--quiet'], root)
        checks['cargo_test'] = code == 0
        assert checks['cargo_test'], (stdout, stderr)

        transaction = applied['transaction']
        undone = request(binary, root, {'op': 'undo', 'transaction': transaction})
        assert undone['ok'], undone
        checks['undo_byte_exact'] = snapshot(root) == original
        assert checks['undo_byte_exact']

        stale = request(binary, root, preview_request(ALL, 6))
        assert stale['ok'], stale
        with (root / 'src/billing.rs').open('a') as handle:
            handle.write('# unrelated change\n')
        rejected = apply(binary, root, stale['token'])
        checks['stale_source_rejected'] = (
            not rejected['ok'] and 'changed since preview' in rejected['error']
            and (root / 'src/lib.rs').read_text() == original['src/lib.rs'].decode())
        assert checks['stale_source_rejected'], rejected

    print(json.dumps({**checks, 'model_calls': 0}, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
