"""Frozen synthetic workflow fixtures and external verifiers for RC studies."""
import json
import os
from pathlib import Path
import subprocess
import sys

import agent_benchmark_suite as suite


def command(root, *args):
    return subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=15,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def cross_fixture(root):
    suite.write_files(root, {
        'pricing.py': 'def final_price(price, rate):\n    return round(price - rate, 2)\n',
        'checkout.py': 'from pricing import final_price\n\ndef total(price, discount_percent):\n    return final_price(price, discount_percent / 100)\n',
        'test_pricing.py': 'import unittest\nfrom checkout import total\nfrom pricing import final_price\n\nclass Prices(unittest.TestCase):\n    def test_discount(self):\n        for price, percent, expected in [(80, 25, 60.0), (20, 100, 0.0), (200, 2.5, 195.0)]:\n            self.assertEqual(total(price, percent), expected)\n            self.assertEqual(final_price(price, percent), expected)\n    def test_none(self):\n        self.assertEqual(total(15, 0), 15.0)\n',
        'README.md': '# Pricing\nDiscounts are percentages; checkout converts them to fractions.\n',
        'sentinel.txt': 'unrelated content\n',
    })
    suite.git_init(root)
    return {}


def cross_verify(root, state):
    result = command(root, sys.executable, '-B', '-m', 'unittest', 'test_pricing')
    return result.returncode == 0, {'test_exit': result.returncode, 'test_stderr': result.stderr}


def build_fixture(root):
    suite.write_files(root, {
        'calculator.py': 'def total(left, right):\n    return left - right\n',
        'test_calculator.py': 'import unittest\nfrom calculator import total\nclass TestTotal(unittest.TestCase):\n    def test_total(self):\n        self.assertEqual(total(2, 3), 5)\n',
        'slow_test.py': 'import subprocess, sys, time\nprint("TEST STARTED", flush=True)\ntime.sleep(5)\nr = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_calculator"], text=True)\nprint("TEST FINISHED", flush=True)\nraise SystemExit(r.returncode)\n',
        'README.md': '# Calculator\nRun the Test job to check the calculator.\n',
    })
    definition = {'label': 'Test', 'type': 'process', 'command': sys.executable,
                  'args': ['-u', 'slow_test.py'], 'group': 'test', 'options': {}}
    suite.write_files(root, {'.vscode/tasks.json': json.dumps({'version': '2.0.0', 'tasks': [definition]}, indent=2) + '\n'})
    suite.git_init(root)
    return {'definition': definition}


def build_verify(root, state):
    result = command(root, sys.executable, '-B', '-m', 'unittest', 'test_calculator')
    try:
        defs = json.loads((root / '.vscode/tasks.json').read_text())
        task_preserved = defs == {'version': '2.0.0', 'tasks': [state['definition']]}
    except Exception:
        task_preserved = False
    return result.returncode == 0 and task_preserved, {'test_exit': result.returncode, 'task_definition_preserved': task_preserved}


def git_fixture(root):
    suite.write_files(root, {'calculator.py': 'def total(left, right):\n    return left - right\n',
        'test_calculator.py': 'import unittest\nfrom calculator import total\nclass TestTotal(unittest.TestCase):\n    def test_total(self):\n        self.assertEqual(total(2, 3), 5)\n',
        'other.txt': 'original\n', 'README.md': '# Calculator\n', })
    suite.git_init(root)
    (root / 'other.txt').write_text('unrelated staged\n')
    subprocess.run(['git', 'add', 'other.txt'], cwd=root, check=True)
    (root / 'other.txt').write_text('unrelated staged plus unstaged\n')
    return {'staged': 'unrelated staged\n', 'unstaged': 'unrelated staged plus unstaged\n'}


def git_verify(root, state):
    changed = command(root, 'git', 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD')
    msg = command(root, 'git', 'log', '-1', '--format=%s')
    staged = command(root, 'git', 'show', ':other.txt')
    staged_names = command(root, 'git', 'diff', '--cached', '--name-only')
    unstaged_names = command(root, 'git', 'diff', '--name-only')
    test = command(root, sys.executable, '-B', '-m', 'unittest', 'test_calculator')
    checks = {'only_intended_commit': changed.returncode == 0 and changed.stdout.splitlines() == ['calculator.py'],
              'message': msg.stdout.strip() == 'Fix total calculation',
              'unrelated_index': staged.stdout == state['staged'],
              'unrelated_worktree': (root / 'other.txt').read_text() == state['unstaged'],
              'index_membership': staged_names.stdout.splitlines() == ['other.txt'],
              'worktree_membership': unstaged_names.stdout.splitlines() == ['other.txt'],
              'test': test.returncode == 0}
    return all(checks.values()), checks


def debug_fixture(root):
    source = (suite.ROOT / 'tests/fixtures/debug-python-payroll/payroll.py').read_text()
    suite.write_files(root, {'payroll.py': source, 'README.md': '# Payroll\nRun payroll.py to reproduce the bug.\n'})
    suite.git_init(root)
    return {}


def debug_verify(root, state):
    result = command(root, sys.executable, '-B', 'payroll.py')
    return result.returncode == 0 and 'ada' in result.stdout and '1000' in result.stdout, {'exit_code': result.returncode, 'stdout': result.stdout}


TASKS = {
    'cross': {'fixture': cross_fixture, 'verify': cross_verify,
              'prompt': 'Explore this pricing project and fix the discount calculation. Both checkout and the pricing function should accept discounts as percentages, and final prices must be correct. Keep tests and unrelated files unchanged. Finish when the existing tests pass.',
              'allowed_changes': {'pricing.py', 'checkout.py'}},
    'build': {'fixture': build_fixture, 'verify': build_verify,
              'prompt': 'Run the existing Test task, observe its output while it is still running, diagnose the failure, repair the calculator and rerun the test successfully. Preserve the task definition and unrelated files.',
              'allowed_changes': {'calculator.py', '.vscode/tasks.json'}},
    'git': {'fixture': git_fixture, 'verify': git_verify,
            'prompt': 'Fix the calculator so its existing test passes. Review the exact staged change and commit only the fix with message "Fix total calculation". Preserve the unrelated staged and unstaged work exactly.',
            'allowed_changes': {'calculator.py'}},
    'debug': {'fixture': debug_fixture, 'verify': debug_verify,
              'prompt': 'Use a debugger to diagnose the payroll failure. Observe and report the relevant runtime state and its cause, make the smallest fix, and run the program to verify it. Preserve unrelated files.',
              'allowed_changes': {'payroll.py'}},
}
