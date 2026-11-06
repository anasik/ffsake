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
        'tests/test_cursor.py': 'import unittest\nfrom cursor import collect\n\nclass Cursor(unittest.TestCase):\n    def test_pages(self):\n        self.assertEqual(collect([1, 2, 3, 4, 5], 2), [1, 2, 3, 4, 5])\n        self.assertEqual(collect(list(range(10)), 3), list(range(10)))\n',
        'README.md': '# Cursor\nRun main.py to reproduce the pagination failure.\n',
        'notes.txt': 'Each item must appear exactly once.\n',
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
}
