"""The measured fixtures start identically and reject incomplete outcomes."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import agent_benchmark_suite as suite
import rc_benchmark as rc
import rc_benchmark_fixtures as fixtures


class FixtureTests(unittest.TestCase):
    def test_paired_worktrees_and_reference_fixes(self):
        for name in ('cross', 'build', 'git', 'debug'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                base = Path(td).resolve()
                template, left, right = base / 'template', base / 'left', base / 'right'
                template.mkdir(); left.mkdir(); right.mkdir()
                task = fixtures.TASKS[name]
                state = task['fixture'](template)
                populate = rc.make_clone_fixture(rc.bare_template(template), state)
                populate(left); populate(right)
                self.assertTrue((left / '.git').is_dir())
                self.assertTrue((right / '.git').is_dir())
                prompt = task['prompt']
                a = suite.task_provenance(left, suite.fixture_files(left), prompt)
                b = suite.task_provenance(right, suite.fixture_files(right), prompt)
                self.assertEqual(a['fixture_manifest_sha256'], b['fixture_manifest_sha256'])
                self.assertFalse(task['verify'](left, state)[0])
                if name == 'cross':
                    (left / 'pricing.py').write_text('def final_price(price, rate):\n    return round(price * (1 - rate / 100), 2)\n')
                    (left / 'checkout.py').write_text('from pricing import final_price\n\ndef total(price, discount_percent):\n    return final_price(price, discount_percent)\n')
                elif name in ('build', 'git'):
                    (left / 'calculator.py').write_text('def total(left, right):\n    return left + right\n')
                    if name == 'git':
                        run = fixtures.command(left, 'git', 'commit', '--only', '-m', 'Fix total calculation', 'calculator.py')
                        self.assertEqual(run.returncode, 0, run.stderr)
                else:
                    path = left / 'payroll.py'
                    path.write_text(path.read_text().replace('def total_pay(hours, rate, adjustments=[]):',
                        'def total_pay(hours, rate, adjustments=None):\n    if adjustments is None:\n        adjustments = []'))
                self.assertTrue(task['verify'](left, state)[0], name)
                self.assertFalse(task['verify'](right, state)[0])

    def test_stats_do_not_replace_missing_usage_with_zero(self):
        item = {'agent':'codex','task':'cross','mode':'baseline','correct':True,'comparison_valid':True,
                'elapsed_ms':1,'trace':{'tool_calls':1,'input_tokens':None}}
        row = rc.summarize([item])[0]
        self.assertEqual(row['input_tokens'], {'n':0,'median':None,'min':None,'max':None})

    def test_aggregate_uses_valid_runs_only(self):
        good = {'agent':'claude','task':'git','mode':'ffsake','correct':True,'comparison_valid':True,
                'elapsed_ms':10,'workflow_evidence':{'passed':True},
                'trace':{'tool_calls':2,'input_tokens':1,'total_input_tokens':3}}
        bad = {'agent':'claude','task':'git','mode':'ffsake','correct':False,'comparison_valid':False,
               'elapsed_ms':999,'workflow_evidence':{'passed':False},
               'trace':{'tool_calls':99,'input_tokens':100,'total_input_tokens':100}}
        row = rc.summarize([good, bad])[0]
        self.assertEqual((row['runs'], row['valid'], row['invalid']), (2, 1, 1))
        self.assertEqual(row['elapsed_ms']['median'], 10)
        self.assertEqual(row['tool_calls']['median'], 2)

    def test_workflow_evidence_requires_debugger_state_and_ordered_review(self):
        build = rc.workflow_evidence('codex', 'build', [
            {'name':'mcp__ffsake__ffsake_run_task','text':'{}','input':{}},
            {'name':'mcp__ffsake__ffsake_task_output','text':'{}','input':{}}],
            'TEST STARTED\nTEST FINISHED')
        self.assertTrue(build['passed'])
        debug = rc.workflow_evidence('codex', 'debug', [
            {'name':'command_execution','text':'python3 -m pdb payroll.py','input':{}}],
            '(Pdb) b 15\nBreakpoint 1\n(Pdb) p adjustments\n[]')
        self.assertTrue(debug['passed'])
        unordered = rc.workflow_evidence('claude', 'git', [
            {'name':'Bash','text':'git commit -m fix','input':{}},
            {'name':'Bash','text':'git diff --cached','input':{}}], '')
        self.assertFalse(unordered['passed'])

    def test_usage_totals_are_provider_normalized(self):
        claude = {'input_tokens':2,'cache_read_input_tokens':10,'cache_creation_input_tokens':3}
        rc.normalize_usage('claude', claude)
        self.assertEqual(claude['total_input_tokens'], 15)
        self.assertEqual(claude['uncached_input_tokens'], 2)
        codex = {'input_tokens':20,'cache_read_input_tokens':8}
        rc.normalize_usage('codex', codex)
        self.assertEqual(codex['total_input_tokens'], 20)
        self.assertEqual(codex['uncached_input_tokens'], 12)


if __name__ == '__main__': unittest.main()
