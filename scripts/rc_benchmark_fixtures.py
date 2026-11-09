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


def debug_shipping_fixture(root):
    source = (suite.ROOT / 'tests/fixtures/debug-python-shipping/shipping.py').read_text()
    suite.write_files(root, {'shipping.py': source, 'README.md': '# Shipping\nRun shipping.py to reproduce the bug.\n'})
    suite.git_init(root)
    return {}


def debug_shipping_verify(root, state):
    result = command(root, sys.executable, '-B', 'shipping.py')
    return result.returncode == 0 and 'ana' in result.stdout and '50.0' in result.stdout, {'exit_code': result.returncode, 'stdout': result.stdout}


def debug_inventory_fixture(root):
    files = {
        'inventory/__init__.py': '"""Small inventory package."""\n',
        'inventory/catalog.py': "UNIT_PRICES = {'widget': 10.0, 'gadget': 25.0, 'cable': 2.5}\n",
        'inventory/pricing.py': 'def subtotal(quantity, unit_price):\n    return round(quantity * unit_price, 2)\n',
        'inventory/discounts.py': 'def volume_rate(quantity):\n    if quantity > 10:\n        return 0.10\n    return 0.0\n',
        'inventory/orders.py': 'from .discounts import volume_rate\nfrom .pricing import subtotal\n\ndef calculate_order(order, prices):\n    amount = subtotal(order["quantity"], prices[order["sku"]])\n    return round(amount * (1.0 - volume_rate(order["quantity"])), 2)\n',
        'inventory/reports.py': 'def describe(order):\n    return f"{order[\'quantity\']} x {order[\'sku\']}"\n',
        'inventory/validation.py': 'def valid_order(order):\n    return order.get("quantity", 0) > 0 and "sku" in order\n',
        'main.py': 'from inventory.catalog import UNIT_PRICES\nfrom inventory.orders import calculate_order\n\norder = {"sku": "widget", "quantity": 10}\nactual = calculate_order(order, UNIT_PRICES)\nexpected = 90.0\nprint(f"actual={actual:.2f}")\nprint(f"expected={expected:.2f}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/__init__.py': '',
        'tests/test_orders.py': 'import unittest\nfrom inventory.catalog import UNIT_PRICES\nfrom inventory.orders import calculate_order\n\nclass Orders(unittest.TestCase):\n    def test_volume_boundary(self):\n        self.assertEqual(calculate_order({"sku": "widget", "quantity": 10}, UNIT_PRICES), 90.0)\n',
        'README.md': '# Inventory\nRun main.py to reproduce the order failure.\n',
        'sentinel.txt': 'unrelated inventory notes\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['inventory/discounts.py'] = 'def volume_rate(quantity):\n    if quantity >= 10:\n        return 0.10\n    return 0.0\n'
    return {'reference': reference}


def debug_inventory_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=90.00' in program.stdout
            and 'expected=90.00' in program.stdout and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def debug_intervals_fixture(root):
    files = {
        'intervals.py': 'def merge(intervals):\n    result = []\n    for start, end in sorted(intervals):\n        if not result or start >= result[-1][1]:\n            result.append((start, end))\n        else:\n            result[-1] = (result[-1][0], max(result[-1][1], end))\n    return result\n',
        'main.py': 'from intervals import merge\n\nactual = merge([(1, 3), (3, 5), (8, 9)])\nexpected = [(1, 5), (8, 9)]\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/test_intervals.py': 'import unittest\nfrom intervals import merge\n\nclass Intervals(unittest.TestCase):\n    def test_adjacent(self):\n        self.assertEqual(merge([(1, 3), (3, 5)]), [(1, 5)])\n',
        'README.md': '# Intervals\nRun main.py to reproduce the merge failure.\n',
        'notes.txt': 'Schedule windows are inclusive at both ends.\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['intervals.py'] = 'def merge(intervals):\n    result = []\n    for start, end in sorted(intervals):\n        if not result or start > result[-1][1]:\n            result.append((start, end))\n        else:\n            result[-1] = (result[-1][0], max(result[-1][1], end))\n    return result\n'
    return {'reference': reference}


def debug_intervals_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=[(1, 5), (8, 9)]' in program.stdout
            and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def debug_cache_fixture(root):
    files = {
        'cache.py': 'class Cache:\n    def __init__(self):\n        self.data = {}\n\n    def put(self, key, value):\n        self.data[key] = value\n\n    def get(self, key, default=None):\n        return self.data[key]\n',
        'config.py': 'DEFAULT_TTL = 30\nDEFAULT_NAMESPACE = "primary"\n',
        'main.py': 'from cache import Cache\n\ncache = Cache()\ncache.put("alpha", 1)\nactual = cache.get("beta", 0)\nexpected = 0\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/test_cache.py': 'import unittest\nfrom cache import Cache\n\nclass CacheTests(unittest.TestCase):\n    def test_missing_default(self):\n        self.assertEqual(Cache().get("missing", 0), 0)\n',
        'README.md': '# Cache\nRun main.py to reproduce the missing-key failure.\n',
        'notes.txt': 'Missing keys should return the caller default.\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['cache.py'] = 'class Cache:\n    def __init__(self):\n        self.data = {}\n\n    def put(self, key, value):\n        self.data[key] = value\n\n    def get(self, key, default=None):\n        return self.data.get(key, default)\n'
    return {'reference': reference}


def debug_cache_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=0' in program.stdout
            and 'expected=0' in program.stdout and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})

def edit_batch_fixture(root):
    files = {
        'retail.py': 'def total(price, percent):\n    return round(price - percent, 2)\n',
        'wholesale.py': 'def total(price, percent):\n    return round(price - percent, 2)\n',
        'member.py': 'def total(price, percent):\n    return round(price - percent, 2)\n',
        'tests/__init__.py': '',
        'tests/test_pricing.py': 'import unittest\nfrom retail import total as retail\nfrom wholesale import total as wholesale\nfrom member import total as member\n\nclass Pricing(unittest.TestCase):\n    def test_percentages(self):\n        self.assertEqual(retail(80, 25), 60.0)\n        self.assertEqual(wholesale(40, 50), 20.0)\n        self.assertEqual(member(100, 10), 90.0)\n',
        'README.md': '# Checkout\nPercentage discounts are applied in three customer modules.\n',
        'sentinel.txt': 'unrelated checkout notes\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    for path in ('retail.py', 'wholesale.py', 'member.py'):
        reference[path] = 'def total(price, percent):\n    return round(price * (1 - percent / 100), 2)\n'
    return {'reference': reference}


def edit_batch_verify(root, state):
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return tests.returncode == 0, {'test_exit': tests.returncode, 'test_stderr': tests.stderr}


def debug_ledger_fixture(root):
    files = {
        'ledger.py': 'def net_total(entries, fee):\n    total = sum(entries)\n    for index in range(len(entries) - 1):\n        total -= fee\n    return total\n',
        'main.py': 'from ledger import net_total\n\nactual = net_total([100, 50, 25], 5)\nexpected = 160\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/test_ledger.py': 'import unittest\nfrom ledger import net_total\n\nclass Ledger(unittest.TestCase):\n    def test_all_entries(self):\n        self.assertEqual(net_total([100, 50, 25], 5), 160)\n',
        'README.md': '# Ledger\nRun main.py to reproduce the fee failure.\n',
        'notes.txt': 'Every entry should receive the fee.\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['ledger.py'] = 'def net_total(entries, fee):\n    total = sum(entries)\n    for index in range(len(entries)):\n        total -= fee\n    return total\n'
    return {'reference': reference}


def debug_ledger_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=160' in program.stdout
            and 'expected=160' in program.stdout and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def debug_nested_fixture(root):
    files = {
        'settings.py': 'DEFAULTS = {"limits": {"burst": 4, "rate": 2}}\n\ndef merge(base, override):\n    merged = dict(base)\n    for key, value in override.items():\n        merged[key] = value\n    return merged\n',
        'main.py': 'from settings import DEFAULTS, merge\n\nsettings = merge(DEFAULTS, {"limits": {"rate": 5}})\nactual = settings["limits"]["burst"]\nexpected = 4\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/test_settings.py': 'import unittest\nfrom settings import DEFAULTS, merge\n\nclass Settings(unittest.TestCase):\n    def test_nested_default(self):\n        self.assertEqual(merge(DEFAULTS, {"limits": {"rate": 5}})["limits"]["burst"], 4)\n',
        'README.md': '# Settings\nRun main.py to reproduce the nested merge failure.\n',
        'notes.txt': 'Nested defaults should survive partial overrides.\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['settings.py'] = 'DEFAULTS = {"limits": {"burst": 4, "rate": 2}}\n\ndef merge(base, override):\n    merged = {key: dict(value) if isinstance(value, dict) else value for key, value in base.items()}\n    for key, value in override.items():\n        if isinstance(value, dict) and isinstance(merged.get(key), dict):\n            merged[key].update(value)\n        else:\n            merged[key] = value\n    return merged\n'
    return {'reference': reference}


def debug_nested_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=4' in program.stdout
            and 'expected=4' in program.stdout and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})

def edit_feature_fixture(root):
    files = {
        'tracker/__init__.py': '',
        'tracker/store.py': 'def add(title, items=None):\n    items = [] if items is None else items\n    items.append({"title": title, "done": False})\n    return items\n\ndef complete(title, items):\n    for item in items:\n        if item["title"] == title:\n            item["done"] = True\n    return items\n',
        'main.py': 'from tracker.store import add, complete, pending, summary\n\nitems = add("write")\nadd("review", items)\ncomplete("write", items)\nprint(f"pending={pending(items)}")\nprint(f"summary={summary(items)}")\n',
        'tests/__init__.py': '',
        'tests/test_tracker.py': 'import unittest\nfrom tracker.store import add, complete, summary, pending\n\nclass Tracker(unittest.TestCase):\n    def test_pending(self):\n        items = add("a")\n        add("b", items)\n        complete("a", items)\n        self.assertEqual(pending(items), ["b"])\n    def test_summary(self):\n        items = add("a")\n        add("b", items)\n        complete("a", items)\n        self.assertEqual(summary(items), {"total": 2, "done": 1, "pending": 1})\n    def test_empty(self):\n        self.assertEqual(pending([]), [])\n        self.assertEqual(summary([]), {"total": 0, "done": 0, "pending": 0})\n    def test_complete_missing_is_noop(self):\n        items = add("a")\n        self.assertEqual(complete("missing", items), items)\n',
        'README.md': '# Tracker\nRun the tests and main.py.\n',
        'notes.txt': 'Titles are unique within a list.\n',
        '.gitignore': '.ffsake/\n__pycache__/\n*.pyc\n.pytest_cache/\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['tracker/store.py'] = ('def add(title, items=None):\n    items = [] if items is None else items\n'
        '    items.append({"title": title, "done": False})\n    return items\n\n'
        'def complete(title, items):\n    for item in items:\n        if item["title"] == title:\n            item["done"] = True\n    return items\n\n'
        'def pending(items):\n    return [item["title"] for item in items if not item["done"]]\n\n'
        'def summary(items):\n    total = len(items)\n    done = sum(1 for item in items if item["done"])\n    return {"total": total, "done": done, "pending": total - done}\n')
    return {'reference': reference}


def edit_feature_verify(root, state):
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    program = command(root, sys.executable, '-B', 'main.py')
    return (tests.returncode == 0 and program.returncode == 0
            and 'pending=[\'review\']' in program.stdout,
            {'test_exit': tests.returncode, 'test_stderr': tests.stderr,
             'program_exit': program.returncode, 'stdout': program.stdout})


def rename_scope_rust_fixture(root):
    files = {
        'Cargo.toml': '[package]\nname = "scope-demo"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n',
        'Cargo.lock': '# This file is automatically @generated by Cargo.\n# It is not intended for manual editing.\nversion = 4\n\n[[package]]\nname = "scope-demo"\nversion = "0.1.0"\n',
        '.gitignore': '/target\n',
        'src/lib.rs': 'pub mod billing;\npub mod shipping;\n\n/// total sums all entries in the report.\npub fn total(items: &[u32]) -> u32 {\n    items.iter().copied().sum()\n}\n',
        'src/billing.rs': 'use crate::total;\n\npub fn invoice(values: &[u32]) -> u32 {\n    total(values)\n}\n\npub fn label(total: u32) -> String {\n    // total is a parameter here, not the crate helper.\n    format!("billing:{total}")\n}\n',
        'src/shipping.rs': 'use crate::total;\n\npub fn quote(values: &[u32]) -> u32 {\n    let total = values.iter().copied().max().unwrap_or(0);\n    let _ = total;\n    1\n}\n',
        'src/main.rs': 'use scope_demo::{billing, shipping, total};\n\nfn main() {\n    let total = total(&[1, 2, 3]);\n    println!("{total} {} {}", billing::invoice(&[4, 5]), shipping::quote(&[6]));\n}\n',
        'tests/test_scope.rs': 'use scope_demo::{billing, total};\n\n#[test]\nfn totals() {\n    assert_eq!(total(&[1, 2, 3]), 6);\n    assert_eq!(billing::invoice(&[4, 5]), 9);\n}\n',
        'README.md': '# Scope demo\nRun cargo test. The crate helper and several locals share a name.\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['src/lib.rs'] = reference['src/lib.rs'].replace('pub fn total(', 'pub fn sum_all(')
    reference['src/billing.rs'] = (reference['src/billing.rs']
                                   .replace('use crate::total;', 'use crate::sum_all;')
                                   .replace('total(values)', 'sum_all(values)'))
    reference['src/shipping.rs'] = reference['src/shipping.rs'].replace('use crate::total;', 'use crate::sum_all;')
    reference['src/main.rs'] = (reference['src/main.rs']
                                .replace('{billing, shipping, total}', '{billing, shipping, sum_all}')
                                .replace('let total = total(', 'let total = sum_all('))
    reference['tests/test_scope.rs'] = (reference['tests/test_scope.rs']
                                        .replace('{billing, total}', '{billing, sum_all}')
                                        .replace('total(&[1, 2, 3])', 'sum_all(&[1, 2, 3])'))
    return {'reference': reference}


def rename_scope_rust_verify(root, state):
    build = command(root, 'cargo', 'build', '--quiet')
    tests = command(root, 'cargo', 'test', '--quiet')
    books = {name: (root / name).read_text() for name in
             ('src/lib.rs', 'src/billing.rs', 'src/shipping.rs', 'src/main.rs', 'tests/test_scope.rs')}
    checks = {
        'declaration': 'pub fn sum_all(items: &[u32]) -> u32' in books['src/lib.rs'],
        'billing_call': 'use crate::sum_all;' in books['src/billing.rs']
                        and 'sum_all(values)' in books['src/billing.rs'],
        'billing_parameter': 'pub fn label(total: u32)' in books['src/billing.rs'],
        'shipping_import': 'use crate::sum_all;' in books['src/shipping.rs'],
        'shipping_local': 'let total = values.iter().copied()' in books['src/shipping.rs'],
        'main_binding': 'let total = sum_all(&[1, 2, 3])' in books['src/main.rs'],
        'test_updated': 'use scope_demo::{billing, sum_all};' in books['tests/test_scope.rs']
                        and 'sum_all(&[1, 2, 3])' in books['tests/test_scope.rs'],
        'no_stale_function': 'pub fn total(' not in books['src/lib.rs']
                             and 'use crate::total;' not in books['src/billing.rs']
                             and 'use crate::total;' not in books['src/shipping.rs']
                             and 'total(&[1, 2, 3])' not in books['tests/test_scope.rs'],
    }
    return (build.returncode == 0 and tests.returncode == 0 and all(checks.values()),
            {**checks, 'build_exit': build.returncode, 'test_exit': tests.returncode,
             'build_stderr': build.stderr[-2000:], 'test_stderr': tests.stderr[-2000:]})


def rename_field_rust_fixture(root):
    files = {
        'Cargo.toml': '[package]\nname = "field-demo"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n',
        'Cargo.lock': '# This file is automatically @generated by Cargo.\n# It is not intended for manual editing.\nversion = 4\n\n[[package]]\nname = "field-demo"\nversion = "0.1.0"\n',
        '.gitignore': '/target\n',
        'src/lib.rs': 'pub mod legacy;\n\n#[derive(Clone, Copy)]\npub struct Account {\n    pub balance: u32,\n    pub name: &\'static str,\n}\n\nimpl Account {\n    pub fn new(balance: u32, name: &\'static str) -> Self {\n        Self { balance, name }\n    }\n\n    pub fn balance(&self) -> u32 {\n        self.balance\n    }\n}\n\npub fn total(accounts: &[Account]) -> u32 {\n    accounts.iter().map(Account::balance).sum()\n}\n\npub fn describe(account: &Account) -> String {\n    let Account { balance, name } = *account;\n    format!("{name}:{balance}")\n}\n\npub fn unrelated(balance: u32) -> u32 {\n    balance + 1\n}\n',
        'src/legacy.rs': 'use crate::Account;\n\npub fn opening() -> Account {\n    Account { balance: 10, name: "legacy" }\n}\n\npub fn read(account: &Account) -> u32 {\n    account.balance\n}\n',
        'src/main.rs': 'use field_demo::{legacy, total, Account};\n\nfn main() {\n    let account = Account::new(7, "main");\n    println!("{} {} {}", total(&[account]), account.balance(), legacy::read(&legacy::opening()));\n}\n',
        'tests/test_accounts.rs': 'use field_demo::{describe, total, Account};\n\n#[test]\nfn totals() {\n    let account = Account { balance: 5, name: "a" };\n    assert_eq!(total(&[account]), 5);\n    assert_eq!(describe(&account), "a:5");\n    assert_eq!(account.balance(), 5);\n}\n',
        'README.md': '# Field demo\nRun cargo test. The field, a method and unrelated parameters share a name.\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['src/lib.rs'] = ('pub mod legacy;\n\n#[derive(Clone, Copy)]\npub struct Account {\n    pub cents: u32,\n    pub name: &\'static str,\n}\n\nimpl Account {\n    pub fn new(cents: u32, name: &\'static str) -> Self {\n        Self { cents, name }\n    }\n\n    pub fn balance(&self) -> u32 {\n        self.cents\n    }\n}\n\npub fn total(accounts: &[Account]) -> u32 {\n    accounts.iter().map(Account::balance).sum()\n}\n\npub fn describe(account: &Account) -> String {\n    let Account { cents: balance, name } = *account;\n    format!("{name}:{balance}")\n}\n\npub fn unrelated(balance: u32) -> u32 {\n    balance + 1\n}\n')
    reference['src/legacy.rs'] = reference['src/legacy.rs'].replace('balance: 10', 'cents: 10').replace('account.balance', 'account.cents')
    reference['tests/test_accounts.rs'] = reference['tests/test_accounts.rs'].replace('balance: 5', 'cents: 5')
    return {'reference': reference}


def rename_field_rust_verify(root, state):
    build = command(root, 'cargo', 'build', '--quiet')
    tests = command(root, 'cargo', 'test', '--quiet')
    lib = (root / 'src/lib.rs').read_text()
    legacy = (root / 'src/legacy.rs').read_text()
    test = (root / 'tests/test_accounts.rs').read_text()
    checks = {
        'field_declaration': 'pub cents: u32' in lib and 'pub balance: u32' not in lib,
        'field_access': 'self.cents' in lib and 'account.cents' in legacy,
        'method_preserved': 'pub fn balance(&self)' in lib and 'Account::balance' in lib,
        'pattern_binding': 'Account { cents: balance, name }' in lib,
        'unrelated_parameter': 'pub fn unrelated(balance: u32)' in lib and 'balance + 1' in lib,
        'legacy_literal': 'cents: 10' in legacy,
        'test_literal': 'cents: 5' in test,
        'test_method': 'account.balance()' in test,
        'constructor': ('pub fn new(balance: u32' in lib and 'Self { cents: balance' in lib)
                       or ('pub fn new(cents: u32' in lib and 'Self { cents' in lib),
    }
    return (build.returncode == 0 and tests.returncode == 0 and all(checks.values()),
            {**checks, 'build_exit': build.returncode, 'test_exit': tests.returncode,
             'build_stderr': build.stderr[-2000:], 'test_stderr': tests.stderr[-2000:]})


def references_rust_fixture(root):
    files = {
        'Cargo.toml': '[package]\nname = "refs-demo"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n',
        'Cargo.lock': '# This file is automatically @generated by Cargo.\n# It is not intended for manual editing.\nversion = 4\n\n[[package]]\nname = "refs-demo"\nversion = "0.1.0"\n',
        '.gitignore': '/target\n',
        'src/lib.rs': 'pub mod jobs;\npub mod state;\n\n#[derive(Clone, Copy, PartialEq, Debug)]\npub enum Status {\n    Pending,\n    Active,\n    Done,\n}\n\npub fn is_active(status: Status) -> bool {\n    status == Status::Active\n}\n\npub fn label(status: Status) -> &\'static str {\n    match status {\n        Status::Active => "active",\n        _ => "other",\n    }\n}\n',
        'src/jobs.rs': 'use crate::Status;\n\npub fn open() -> Status {\n    Status::Active\n}\n\npub fn after(status: Status) -> Status {\n    if status == Status::Active {\n        Status::Done\n    } else {\n        status\n    }\n}\n\npub fn active_count(statuses: &[Status]) -> usize {\n    statuses.iter().filter(|status| **status == Status::Active).count()\n}\n',
        'src/state.rs': 'use crate::Status;\n\npub const INITIAL: Status = Status::Active;\n\npub fn revive() -> Status {\n    let active = Status::Pending;\n    let _ = active;\n    Status::Active\n}\n',
        'src/main.rs': 'use refs_demo::{is_active, label, Status};\n\nfn main() {\n    let status = Status::Active;\n    let active = is_active(status);\n    println!("{} {} {}", active, label(status), "Active");\n}\n',
        'README.md': '# References demo\nList references to the Status::Active variant.\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['references.txt'] = ''.join(f'{path}:{line}\n' for path, line in (
        ('src/jobs.rs', 4), ('src/jobs.rs', 8), ('src/jobs.rs', 16),
        ('src/lib.rs', 7), ('src/lib.rs', 12), ('src/lib.rs', 17),
        ('src/main.rs', 4), ('src/state.rs', 3), ('src/state.rs', 8)))
    return {'reference': reference}


def references_rust_verify(root, state):
    expected = {'src/jobs.rs:4', 'src/jobs.rs:8', 'src/jobs.rs:16',
                'src/lib.rs:7', 'src/lib.rs:12', 'src/lib.rs:17',
                'src/main.rs:4', 'src/state.rs:3', 'src/state.rs:8'}
    path = root / 'references.txt'
    if not path.is_file():
        return False, {'missing': 'references.txt'}
    lines = {line.strip() for line in path.read_text().splitlines() if line.strip()}
    return lines == expected, {'unexpected': sorted(lines - expected),
                               'missing': sorted(expected - lines),
                               'line_count': len(lines)}



def rename_scale_rust_fixture(root):
    modules = [f'm{index:02d}' for index in range(1, 13)]
    files = {
        'Cargo.toml': '[package]\nname = "scale-demo"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n',
        'Cargo.lock': '# This file is automatically @generated by Cargo.\n# It is not intended for manual editing.\nversion = 4\n\n[[package]]\nname = "scale-demo"\nversion = "0.1.0"\n',
        '.gitignore': '/target\n',
        'src/lib.rs': ''.join(f'pub mod {name};\n' for name in modules) + '\n/// total sums the values.\npub fn total(values: &[u32]) -> u32 {\n    values.iter().copied().sum()\n}\n',
        'README.md': '# Scale demo\nEvery module has a local named total and calls the crate helper.\n',
    }
    for index, name in enumerate(modules, 1):
        files[f'src/{name}.rs'] = (
            'use crate::total;\n\n'
            f'pub fn local_{index:02d}(values: &[u32]) -> u32 {{\n'
            '    let total = values.first().copied().unwrap_or(0);\n'
            '    total + 1\n}\n\n'
            f'pub fn use_{index:02d}(values: &[u32]) -> u32 {{\n'
            '    total(values)\n}\n')
    files['src/main.rs'] = (
        'use scale_demo::total;\n\nfn main() {\n'
        '    let values = [1u32, 2, 3];\n'
        '    let total = total(&values);\n'
        '    println!("{total}");\n}\n')
    files['tests/test_scale.rs'] = (
        'use scale_demo::total;\n\n#[test]\nfn totals() {\n'
        '    assert_eq!(total(&[1, 2, 3]), 6);\n}\n')
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['src/lib.rs'] = reference['src/lib.rs'].replace('pub fn total(', 'pub fn sum_all(')
    reference['src/main.rs'] = (reference['src/main.rs']
                                .replace('use scale_demo::total;', 'use scale_demo::sum_all;')
                                .replace('let total = total(', 'let total = sum_all('))
    reference['tests/test_scale.rs'] = reference['tests/test_scale.rs'].replace('total(', 'sum_all(').replace('scale_demo::total', 'scale_demo::sum_all')
    for name in modules:
        reference[f'src/{name}.rs'] = (reference[f'src/{name}.rs']
                                       .replace('use crate::total;', 'use crate::sum_all;')
                                       .replace('total(values)', 'sum_all(values)'))
    return {'reference': reference}


def rename_scale_rust_verify(root, state):
    build = command(root, 'cargo', 'build', '--quiet')
    tests = command(root, 'cargo', 'test', '--quiet')
    modules = [f'm{index:02d}' for index in range(1, 13)]
    texts = {name: (root / 'src' / f'{name}.rs').read_text() for name in modules}
    lib = (root / 'src/lib.rs').read_text()
    main = (root / 'src/main.rs').read_text()
    test = (root / 'tests/test_scale.rs').read_text()
    checks = {
        'declaration': 'pub fn sum_all(values: &[u32]) -> u32' in lib,
        'modules': all('use crate::sum_all;' in text and 'sum_all(values)' in text
                       and 'let total = ' in text for text in texts.values()),
        'main': 'let total = sum_all(&values)' in main,
        'test': 'scale_demo::sum_all' in test and 'sum_all(&[1, 2, 3])' in test,
        'no_stale_function': 'pub fn total(' not in lib and 'use crate::total;' not in ''.join(texts.values()),
    }
    return (build.returncode == 0 and tests.returncode == 0 and all(checks.values()),
            {**checks, 'build_exit': build.returncode, 'test_exit': tests.returncode,
             'build_stderr': build.stderr[-2000:], 'test_stderr': tests.stderr[-2000:]})


def edit_multi_fixture(root):
    files = {
        'billing/__init__.py': '',
        'billing/validation.py': 'def valid_quantity(quantity):\n    return quantity > 0 or quantity < 100\n',
        'billing/serialization.py': 'def serialize(order):\n    return {"sku": order["sku"]}\n',
        'billing/totals.py': 'def total(order, price):\n    return round(price * order["quantity"], 2)\n',
        'billing/handlers.py': 'from .totals import total\n\ndef handle(order, prices):\n    return total(order, prices[order["sku"]])\n',
        'main.py': 'from billing.handlers import handle\n\nactual = handle({"sku": "widget", "quantity": 2}, {"widget": 10})\nexpected = 20\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/__init__.py': '',
        'tests/test_billing.py': 'import unittest\nfrom billing.validation import valid_quantity\nfrom billing.serialization import serialize\nfrom billing.handlers import handle\n\nclass Billing(unittest.TestCase):\n    def test_upper_bound(self):\n        self.assertFalse(valid_quantity(100))\n    def test_serialization(self):\n        self.assertEqual(serialize({"sku": "x", "quantity": 2}), {"sku": "x", "quantity": 2})\n    def test_handler_rejects(self):\n        with self.assertRaises(ValueError):\n            handle({"sku": "x", "quantity": 0}, {"x": 10})\n    def test_handler_total(self):\n        self.assertEqual(handle({"sku": "x", "quantity": 2}, {"x": 10}), 20)\n',
        'README.md': '# Billing\nRun main.py and the test suite.\n',
        'sentinel.txt': 'unrelated billing notes\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['billing/validation.py'] = 'def valid_quantity(quantity):\n    return quantity > 0 and quantity < 100\n'
    reference['billing/serialization.py'] = 'def serialize(order):\n    return {"sku": order["sku"], "quantity": order["quantity"]}\n'
    reference['billing/handlers.py'] = 'from .totals import total\nfrom .validation import valid_quantity\n\ndef handle(order, prices):\n    if not valid_quantity(order["quantity"]):\n        raise ValueError("invalid quantity")\n    return total(order, prices[order["sku"]])\n'
    return {'reference': reference}


def edit_multi_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def debug_expand_fixture(root):
    files = {
        'tiers.py': 'TIERS = [\n    {"name": "basic", "limit": 10, "rate": 1.0},\n    {"name": "pro", "limit": 20, "rate": 0.9},\n    {"name": "enterprise", "limit": 100, "rate": 0.8},\n]\n\ndef rate_for(quantity):\n    for index, tier in enumerate(TIERS):\n        if quantity < tier["limit"]:\n            return tier["rate"]\n    return 1.0\n',
        'main.py': 'from tiers import rate_for\n\nactual = rate_for(20)\nexpected = 0.9\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/test_tiers.py': 'import unittest\nfrom tiers import rate_for\n\nclass Tiers(unittest.TestCase):\n    def test_boundaries(self):\n        self.assertEqual(rate_for(10), 1.0)\n        self.assertEqual(rate_for(20), 0.9)\n        self.assertEqual(rate_for(100), 0.8)\n',
        'README.md': '# Tiers\nRun main.py to reproduce the tier boundary failure.\n',
        'notes.txt': 'Tier limits are inclusive at the upper bound.\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['tiers.py'] = 'TIERS = [\n    {"name": "basic", "limit": 10, "rate": 1.0},\n    {"name": "pro", "limit": 20, "rate": 0.9},\n    {"name": "enterprise", "limit": 100, "rate": 0.8},\n]\n\ndef rate_for(quantity):\n    for index, tier in enumerate(TIERS):\n        if quantity <= tier["limit"]:\n            return tier["rate"]\n    return 1.0\n'
    return {'reference': reference}


def debug_expand_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=0.9' in program.stdout
            and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def edit_units_fixture(root):
    files = {
        'units/__init__.py': '',
        'units/length.py': 'def meters_to_feet(meters):\n    return round(meters * 3.0, 2)\n',
        'units/mass.py': 'def kg_to_pounds(kilograms):\n    return round(kilograms * 2.0, 2)\n',
        'units/temperature.py': 'def celsius_to_fahrenheit(celsius):\n    return round(celsius * 9 / 5, 2)\n',
        'main.py': 'from units.length import meters_to_feet\nfrom units.mass import kg_to_pounds\nfrom units.temperature import celsius_to_fahrenheit\n\nactual = [meters_to_feet(1), kg_to_pounds(1), celsius_to_fahrenheit(0)]\nexpected = [3.28, 2.2, 32.0]\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/__init__.py': '',
        'tests/test_units.py': 'import unittest\nfrom units.length import meters_to_feet\nfrom units.mass import kg_to_pounds\nfrom units.temperature import celsius_to_fahrenheit\n\nclass Units(unittest.TestCase):\n    def test_length(self):\n        self.assertEqual(meters_to_feet(1), 3.28)\n        self.assertEqual(meters_to_feet(2), 6.56)\n    def test_mass(self):\n        self.assertEqual(kg_to_pounds(1), 2.2)\n        self.assertEqual(kg_to_pounds(5), 11.02)\n    def test_temperature(self):\n        self.assertEqual(celsius_to_fahrenheit(0), 32.0)\n        self.assertEqual(celsius_to_fahrenheit(100), 212.0)\n',
        'README.md': '# Units\nRun main.py and the test suite.\n',
        'sentinel.txt': 'unrelated unit notes\n',
        '.gitignore': '.ffsake/\n__pycache__/\n*.pyc\n.pytest_cache/\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['units/length.py'] = 'def meters_to_feet(meters):\n    return round(meters * 3.28084, 2)\n'
    reference['units/mass.py'] = 'def kg_to_pounds(kilograms):\n    return round(kilograms * 2.20462, 2)\n'
    reference['units/temperature.py'] = 'def celsius_to_fahrenheit(celsius):\n    return round(celsius * 9 / 5 + 32, 2)\n'
    return {'reference': reference}


def edit_units_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def debug_cursor_fixture(root):
    files = {
        'cursor.py': 'def page(items, cursor, size):\n    start = cursor + 1\n    end = start + size\n    return items[start:end], end\n\ndef collect(items, size):\n    cursor = 0\n    result = []\n    while True:\n        batch, nxt = page(items, cursor, size)\n        if not batch:\n            break\n        result.extend(batch)\n        if nxt >= len(items):\n            break\n        cursor = nxt\n    return result\n',
        'main.py': 'from cursor import collect\n\nactual = collect([1, 2, 3, 4, 5], 2)\nexpected = [1, 2, 3, 4, 5]\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/__init__.py': '',
        'tests/test_cursor.py': 'import unittest\nfrom cursor import collect\n\nclass Cursor(unittest.TestCase):\n    def test_pages(self):\n        self.assertEqual(collect([1, 2, 3, 4, 5], 2), [1, 2, 3, 4, 5])\n        self.assertEqual(collect(list(range(10)), 3), list(range(10)))\n',
        'README.md': '# Cursor\nRun main.py to reproduce the pagination failure.\n',
        'notes.txt': 'Each item must appear exactly once.\n',
        '.gitignore': '.ffsake/\n__pycache__/\n*.pyc\n.pytest_cache/\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['cursor.py'] = reference['cursor.py'].replace('start = cursor + 1', 'start = cursor')
    return {'reference': reference}


def debug_cursor_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=[1, 2, 3, 4, 5]' in program.stdout
            and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def edit_settings_fixture(root):
    files = {
        'settings/__init__.py': '',
        'settings/parse.py': 'def parse_bool(value):\n    return value == "true"\n',
        'settings/merge.py': 'def merge(base, override):\n    result = dict(base)\n    result.update(override)\n    return result\n',
        'settings/format.py': 'def format_value(value):\n    return str(value)\n',
        'main.py': 'from settings.parse import parse_bool\nfrom settings.merge import merge\nfrom settings.format import format_value\n\nactual = [\n    parse_bool("TRUE"), parse_bool("no"), parse_bool("1"),\n    merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}),\n    format_value(True), format_value(False),\n]\nexpected = [True, False, True, {"a": {"x": 1, "y": 3}}, "true", "false"]\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/__init__.py': '',
        'tests/test_settings.py': 'import unittest\nfrom settings.parse import parse_bool\nfrom settings.merge import merge\nfrom settings.format import format_value\n\nclass Settings(unittest.TestCase):\n    def test_parse(self):\n        for value in ("true", "TRUE", "yes", "1", "on"):\n            self.assertTrue(parse_bool(value))\n        for value in ("false", "FALSE", "no", "0", "off"):\n            self.assertFalse(parse_bool(value))\n    def test_merge(self):\n        self.assertEqual(merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}),\n                         {"a": {"x": 1, "y": 3}})\n    def test_format(self):\n        self.assertEqual(format_value(True), "true")\n        self.assertEqual(format_value(False), "false")\n        self.assertEqual(format_value(3), "3")\n',
        'README.md': '# Settings\nBoolean parsing is case-insensitive and accepts 1/0, yes/no and on/off.\nNested overrides merge recursively.\n',
        'sentinel.txt': 'unrelated settings notes\n',
        '.gitignore': '.ffsake/\n__pycache__/\n*.pyc\n.pytest_cache/\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['settings/parse.py'] = 'def parse_bool(value):\n    return str(value).strip().lower() in ("true", "yes", "1", "on")\n'
    reference['settings/merge.py'] = 'def merge(base, override):\n    result = dict(base)\n    for key, value in override.items():\n        if isinstance(value, dict) and isinstance(result.get(key), dict):\n            result[key] = merge(result[key], value)\n        else:\n            result[key] = value\n    return result\n'
    reference['settings/format.py'] = 'def format_value(value):\n    if isinstance(value, bool):\n        return "true" if value else "false"\n    return str(value)\n'
    return {'reference': reference}


def edit_settings_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def debug_retry_fixture(root):
    files = {
        'retry.py': 'def delay_for(attempt):\n    return 2 ** attempt\n\ndef schedule(attempts):\n    delays = []\n    for attempt in range(attempts):\n        delay = delay_for(attempt)\n        delays.append(delay)\n    return delays\n',
        'main.py': 'from retry import schedule\n\nactual = schedule(4)\nexpected = [1, 2, 4, 4]\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/__init__.py': '',
        'tests/test_retry.py': 'import unittest\nfrom retry import schedule\n\nclass Retry(unittest.TestCase):\n    def test_cap(self):\n        self.assertEqual(schedule(4), [1, 2, 4, 4])\n        self.assertEqual(schedule(6), [1, 2, 4, 4, 4, 4])\n',
        'README.md': '# Retry\nBackoff doubles but must be capped at four seconds.\n',
        'notes.txt': 'Never wait more than four seconds between attempts.\n',
        '.gitignore': '.ffsake/\n__pycache__/\n*.pyc\n.pytest_cache/\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['retry.py'] = reference['retry.py'].replace('return 2 ** attempt', 'return min(2 ** attempt, 4)')
    return {'reference': reference}


def debug_retry_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=[1, 2, 4, 4]' in program.stdout
            and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def debug_recursive_fixture(root):
    files = {
        'tree.py': 'def total(node, depth=0):\n    subtotal = 0\n    for child in node.get("children", []):\n        subtotal += total(child, depth + 1)\n    return subtotal\n',
        'main.py': 'from tree import total\n\nTREE = {"value": 3, "children": [\n    {"value": 4, "children": []},\n    {"value": 5, "children": [{"value": 6, "children": []}]},\n]}\nactual = total(TREE)\nexpected = 18\nprint(f"actual={actual}")\nprint(f"expected={expected}")\nraise SystemExit(0 if actual == expected else 1)\n',
        'tests/__init__.py': '',
        'tests/test_tree.py': 'import unittest\nfrom tree import total\n\nTREE = {"value": 3, "children": [\n    {"value": 4, "children": []},\n    {"value": 5, "children": [{"value": 6, "children": []}]},\n]}\n\nclass Tree(unittest.TestCase):\n    def test_total(self):\n        self.assertEqual(total(TREE), 18)\n    def test_leaf(self):\n        self.assertEqual(total({"value": 7, "children": []}), 7)\n',
        'README.md': '# Tree\nRun main.py to reproduce the nested total failure.\n',
        'notes.txt': 'Every node contributes its own value, including inner nodes.\n',
        '.gitignore': '.ffsake/\n__pycache__/\n*.pyc\n.pytest_cache/\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['tree.py'] = reference['tree.py'].replace('subtotal = 0', 'subtotal = node["value"]')
    return {'reference': reference}


def debug_recursive_verify(root, state):
    program = command(root, sys.executable, '-B', 'main.py')
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    return (program.returncode == 0 and 'actual=18' in program.stdout
            and 'expected=18' in program.stdout and tests.returncode == 0,
            {'program_exit': program.returncode, 'stdout': program.stdout,
             'test_exit': tests.returncode, 'test_stderr': tests.stderr})


def edit_refactor_fixture(root):
    files = {
        'pricing.py': 'def apply_discount(price, percent):\n    return round(price * (1 - percent / 100), 2)\n',
        'checkout.py': 'from pricing import apply_discount\n\ndef total(items, percent):\n    return round(sum(apply_discount(price, percent) for price in items), 2)\n',
        'reports.py': 'from pricing import apply_discount\n\ndef summary(prices, percent):\n    return {"discounted": [apply_discount(price, percent) for price in prices]}\n',
        'tests/__init__.py': '',
        'tests/test_pricing.py': 'import unittest\nfrom pricing import apply_discount\nfrom checkout import total\nfrom reports import summary\n\nclass Pricing(unittest.TestCase):\n    def test_discount(self):\n        self.assertEqual(apply_discount(80, 25), 60.0)\n        self.assertEqual(total([80, 20], 25), 75.0)\n        self.assertEqual(summary([80], 25), {"discounted": [60.0]})\n',
        'README.md': '# Pricing\nThe public helper is apply_discount.\n',
        'sentinel.txt': 'unrelated refactor notes\n',
        '.gitignore': '.ffsake/\n__pycache__/\n*.pyc\n.pytest_cache/\n',
    }
    suite.write_files(root, files)
    suite.git_init(root)
    reference = dict(files)
    reference['pricing.py'] = 'def apply_rate(price, percent):\n    return round(price * (1 - percent / 100), 2)\n'
    reference['checkout.py'] = 'from pricing import apply_rate\n\ndef total(items, percent):\n    return round(sum(apply_rate(price, percent) for price in items), 2)\n'
    reference['reports.py'] = 'from pricing import apply_rate\n\ndef summary(prices, percent):\n    return {"discounted": [apply_rate(price, percent) for price in prices]}\n'
    reference['tests/test_pricing.py'] = 'import unittest\nfrom pricing import apply_rate\nfrom checkout import total\nfrom reports import summary\n\nclass Pricing(unittest.TestCase):\n    def test_discount(self):\n        self.assertEqual(apply_rate(80, 25), 60.0)\n        self.assertEqual(total([80, 20], 25), 75.0)\n        self.assertEqual(summary([80], 25), {"discounted": [60.0]})\n'
    reference['README.md'] = '# Pricing\nThe public helper is apply_rate.\n'
    return {'reference': reference}


def edit_refactor_verify(root, state):
    tests = command(root, sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests')
    sources = list(root.rglob('*.py'))
    stale = [str(path.relative_to(root)) for path in sources if 'apply_discount' in path.read_text()]
    renamed = any('apply_rate' in path.read_text() for path in sources)
    return (tests.returncode == 0 and not stale and renamed,
            {'test_exit': tests.returncode, 'test_stderr': tests.stderr,
             'stale_references': stale, 'renamed': renamed})


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
              'allowed_changes': {'payroll.py'}, 'debug_program': 'payroll.py',
              'workflow_markers': ('adjustments', '50')},
    'debug_shipping': {'fixture': debug_shipping_fixture, 'verify': debug_shipping_verify,
                       'prompt': 'Use a debugger to diagnose the shipping threshold failure. Observe the subtotal and fee at runtime, make the smallest fix, and run the program to verify it. Preserve unrelated files.',
                       'allowed_changes': {'shipping.py'}, 'debug_program': 'shipping.py',
                       'workflow_markers': ('amount', '50')},
    'debug_inventory': {'fixture': debug_inventory_fixture, 'verify': debug_inventory_verify,
                        'prompt': 'Run the failing inventory order in main.py. Use a debugger to observe the runtime values that determine the order total, identify the smallest cause, fix it, and verify the order and tests. Preserve unrelated files.',
                        'allowed_changes': {'inventory/discounts.py'}, 'debug_program': 'main.py',
                        'workflow_markers': ('quantity', '10')},
    'debug_intervals': {'fixture': debug_intervals_fixture, 'verify': debug_intervals_verify,
                        'prompt': 'The interval merge program reports an incorrect merged schedule. Use a debugger to observe the boundary values, make the smallest fix, and verify the program and tests. Preserve unrelated files.',
                        'allowed_changes': {'intervals.py'}, 'debug_program': 'main.py',
                        'workflow_markers': ('end', '3')},
    'debug_cache': {'fixture': debug_cache_fixture, 'verify': debug_cache_verify,
                    'prompt': 'The cache program fails when reading a missing key. Use a debugger to observe the failing key and call path, fix the fallback behavior, and verify the program and tests. Preserve unrelated files.',
                    'allowed_changes': {'cache.py'}, 'debug_program': 'main.py',
                    'workflow_markers': ('key', 'beta')},
    'edit_batch': {'fixture': edit_batch_fixture, 'verify': edit_batch_verify,
                   'prompt': 'This checkout package applies percentage discounts incorrectly in three customer modules. Fix all affected modules, keep tests unchanged, and run the test suite. Preserve unrelated files.',
                   'allowed_changes': {'retail.py', 'wholesale.py', 'member.py'}},
    'debug_ledger': {'fixture': debug_ledger_fixture, 'verify': debug_ledger_verify,
                     'prompt': 'Run main.py to reproduce the ledger failure. Use a debugger to observe at least two loop iterations and the runtime values, make the smallest fix, and verify the program and tests. Preserve unrelated files.',
                     'allowed_changes': {'ledger.py'}, 'debug_program': 'main.py',
                     'workflow_markers': ('index', '0')},
    'debug_nested': {'fixture': debug_nested_fixture, 'verify': debug_nested_verify,
                     'prompt': 'Run main.py to reproduce the nested settings failure. Use a debugger to inspect the nested runtime values, make the smallest fix, and verify the program and tests. Preserve unrelated files.',
                     'allowed_changes': {'settings.py'}, 'debug_program': 'main.py',
                     'workflow_markers': ('key', 'limits')},
    'edit_multi': {'fixture': edit_multi_fixture, 'verify': edit_multi_verify,
                   'prompt': 'The billing package fails its validation, serialization and handler tests. Fix all affected modules, keep tests unchanged, and run the test suite. Preserve unrelated files.',
                   'allowed_changes': {'billing/validation.py', 'billing/serialization.py', 'billing/handlers.py'}},
    'edit_feature': {'fixture': edit_feature_fixture, 'verify': edit_feature_verify,
                     'prompt': 'The tracker package is missing its pending-list and summary behavior. Implement them so the existing tests and main.py pass, keep existing behavior unchanged, and preserve unrelated files.',
                     'allowed_changes': {'tracker/store.py'}},
    'rename_scope_rust': {'fixture': rename_scope_rust_fixture, 'verify': rename_scope_rust_verify,
                          'prompt': 'Rename the crate function `total` to `sum_all` everywhere it is used as a function, including the integration test. Same-named local variables, parameters and comments must keep their names. Use the installed language server where useful, then verify with cargo build and cargo test. Preserve unrelated files.',
                          'allowed_changes': {'src/lib.rs', 'src/billing.rs', 'src/shipping.rs', 'src/main.rs', 'tests/test_scope.rs'},
                          'semantic': 'rename'},
    'rename_field_rust': {'fixture': rename_field_rust_fixture, 'verify': rename_field_rust_verify,
                          'prompt': 'Rename the `Account` field `balance` to `cents`, updating every use including struct literals, shorthand initialization, pattern destructuring and the integration test. The method named `balance`, the pattern binding in `describe`, and unrelated parameters named `balance` must keep their names. Use the installed language server where useful, then verify with cargo build and cargo test. Preserve unrelated files.',
                          'allowed_changes': {'src/lib.rs', 'src/legacy.rs', 'tests/test_accounts.rs'},
                          'semantic': 'rename'},
    'references_rust': {'fixture': references_rust_fixture, 'verify': references_rust_verify,
                        'prompt': 'Write `references.txt` listing every source reference to the `Status::Active` enum variant, one `path:line` per line, sorted. Include the declaration. Exclude unrelated same-spelling names such as the `active` locals and functions and the string "Active". You may use the installed language server.',
                        'allowed_changes': {'references.txt'},
                        'semantic': 'references'},
    'rename_scale_rust': {'fixture': rename_scale_rust_fixture, 'verify': rename_scale_rust_verify,
                          'prompt': 'Rename the crate function `total` to `sum_all` everywhere it is used as a function, including main and the integration test. Every local or parameter named `total` must keep its name. Use the installed language server where useful, then verify with cargo build and cargo test. Preserve unrelated files.',
                          'allowed_changes': {f'src/m{index:02d}.rs' for index in range(1, 13)} | {'src/lib.rs', 'src/main.rs', 'tests/test_scale.rs'},
                          'semantic': 'rename'},
    'debug_expand': {'fixture': debug_expand_fixture, 'verify': debug_expand_verify,
                     'prompt': 'Run main.py to reproduce the tier boundary failure. Use a debugger to observe at least two loop iterations and the runtime tier values, make the smallest fix, and verify the program and tests. Preserve unrelated files.',
                     'allowed_changes': {'tiers.py'}, 'debug_program': 'main.py',
                     'workflow_markers': ('index', '1')},
    'edit_units': {'fixture': edit_units_fixture, 'verify': edit_units_verify,
                   'prompt': 'The unit conversion package returns wrong values. Fix all affected modules, keep tests unchanged, and verify the program and test suite. Preserve unrelated files.',
                   'allowed_changes': {'units/length.py', 'units/mass.py', 'units/temperature.py'}},
    'debug_cursor': {'fixture': debug_cursor_fixture, 'verify': debug_cursor_verify,
                     'prompt': 'Run main.py to reproduce the pagination failure. Use a debugger to observe at least two page iterations and the cursor values, make the smallest fix, and verify the program and tests. Preserve unrelated files.',
                     'allowed_changes': {'cursor.py'}, 'debug_program': 'main.py',
                     'workflow_markers': ('cursor', '0')},
    'edit_refactor': {'fixture': edit_refactor_fixture, 'verify': edit_refactor_verify,
                      'prompt': 'Rename the public pricing helper apply_discount to apply_rate and update every callsite, including tests. Behavior must stay identical. Preserve unrelated files.',
                      'allowed_changes': {'pricing.py', 'checkout.py', 'reports.py', 'tests/test_pricing.py', 'README.md'}},
    'edit_settings': {'fixture': edit_settings_fixture, 'verify': edit_settings_verify,
                      'prompt': 'The settings package mishandles boolean parsing, nested overrides and value formatting. Fix all affected modules, keep tests unchanged, and verify the program and test suite. Preserve unrelated files.',
                      'allowed_changes': {'settings/parse.py', 'settings/merge.py', 'settings/format.py'}},
    'debug_retry': {'fixture': debug_retry_fixture, 'verify': debug_retry_verify,
                    'prompt': 'Run main.py to reproduce the retry schedule failure. Use a debugger to observe at least two loop iterations and the delay values, make the smallest fix, and verify the program and tests. Preserve unrelated files.',
                    'allowed_changes': {'retry.py'}, 'debug_program': 'main.py',
                    'workflow_markers': ('attempt', '1')},
    'debug_recursive': {'fixture': debug_recursive_fixture, 'verify': debug_recursive_verify,
                        'prompt': 'Run main.py to reproduce the nested tree total failure. Use a debugger to observe recursive calls at more than one depth and the runtime values, make the smallest fix, and verify the program and tests. Preserve unrelated files.',
                        'allowed_changes': {'tree.py'}, 'debug_program': 'main.py',
                        'workflow_markers': ('depth', '1')},
}
