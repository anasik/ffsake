#!/usr/bin/env python3
"""Model-free setup, runtime and verifier preflight for unfamiliar debug fixtures."""
import json
from pathlib import Path
import sys
import tempfile

import agent_benchmark_suite as suite
import debug_smoke
import rc_benchmark as rc

CASES = {
    'debug_inventory': {'path': 'inventory/discounts.py', 'line': 2,
                        'marker': ('quantity', '10')},
    'debug_intervals': {'path': 'intervals.py', 'line': 4,
                        'marker': ('end', '3')},
    'debug_cache': {'path': 'cache.py', 'line': 9,
                    'marker': ('key', 'beta'), 'stops': 4},
    'debug_ledger': {'path': 'ledger.py', 'line': 4,
                     'marker': ('index', '1'), 'stops': 2},
    'debug_nested': {'path': 'settings.py', 'line': 5,
                     'marker': ('key', 'limits')},
    'debug_expand': {'path': 'tiers.py', 'line': 9,
                     'marker': ('index', '1'), 'stops': 2},
    'debug_retry': {'path': 'retry.py', 'line': 7,
                    'marker': ('attempt', '1'), 'stops': 2},
    'debug_cursor': {'path': 'cursor.py', 'line': 2,
                     'marker': ('cursor', '0'), 'stops': 2},
}


def marker_seen(inspection, marker):
    name, expected = marker
    for scope in inspection.get('scopes', []):
        for variable in scope.get('variables', []):
            if variable.get('name') == name and rc._runtime_value_matches(variable.get('value'), expected):
                return True
    return False


def main():
    binary = Path(sys.argv[1]).resolve(strict=True)
    debug_python = rc.adapter_python(Path(sys.argv[2]))
    results = {}
    for task_name, case in CASES.items():
        with tempfile.TemporaryDirectory(prefix=f'ffsake-preflight-{task_name}-') as td:
            base = Path(td).resolve()
            template = base / 'template'
            template.mkdir()
            root = base / 'root'
            root.mkdir()
            task = suite.TASKS[task_name]
            state = task['fixture'](template)
            bare = rc.bare_template(template)
            rc.make_worktree_fixture(bare, state, debug_python, task['debug_program'])(root)
            assert not task['verify'](root, state)[0], f'{task_name}: fixture unexpectedly passes'
            policy = root / '.ffsake/rc-debug.json'
            configured = json.loads(policy.read_text())['configurations'][Path(task['debug_program']).stem]
            assert configured['argv'][0] == str(debug_python), configured
            client = debug_smoke.Client(binary, root, policy, profile='core')
            session = None
            launched = {}
            try:
                arguments = {'configuration': Path(task['debug_program']).stem,
                             'breakpoints': [{'path': case['path'], 'lines': case['line']}]}
                if 'stops' in case:
                    arguments['stops'] = case['stops']
                launched = client.call('debug_probe', **arguments)
                session = launched['session']
                assert launched.get('cleaned') is True, launched
                inspections = [launched.get('result', {}).get('result', {}).get('inspection', {}),
                               launched.get('next_inspection', {}), launched.get('next2_inspection', {})]
                inspections.extend(value for key, value in launched.items()
                                   if key.startswith('stop_inspection_'))
                assert any(marker_seen(item, case['marker']) for item in inspections), (
                    task_name, case['marker'], inspections)
            finally:
                if client.p.poll() is None:
                    if session and launched.get('cleaned') is not True:
                        try:
                            client.stop()
                        except Exception:
                            pass
                    client.close()
            for path, content in state['reference'].items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            assert task['verify'](root, state)[0], f'{task_name}: reference fix fails verification'
            results[task_name] = {'adapter': str(debug_python), 'marker': list(case['marker']),
                                  'initial_verifier': 'fails', 'reference_verifier': 'passes',
                                  'adapter_cleaned': True}
    for edit_task in ('edit_batch', 'edit_multi', 'edit_units', 'edit_settings'):
        with tempfile.TemporaryDirectory(prefix=f'ffsake-preflight-{edit_task}-') as td:
            root = Path(td).resolve()
            task = suite.TASKS[edit_task]
            state = task['fixture'](root)
            assert not task['verify'](root, state)[0], f'{edit_task}: fixture unexpectedly passes'
            for path, content in state['reference'].items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            assert task['verify'](root, state)[0], f'{edit_task}: reference fix fails verification'
            results[edit_task] = {'initial_verifier': 'fails', 'reference_verifier': 'passes'}
    print(json.dumps({'ok': True, 'cases': results}, indent=2))


if __name__ == '__main__':
    main()
