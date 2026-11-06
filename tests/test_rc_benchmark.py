"""The measured fixtures start identically and reject incomplete outcomes."""
import os
import sys
import copy
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import agent_benchmark_suite as suite
import rc_benchmark as rc
import rc_benchmark_fixtures as fixtures


class FixtureTests(unittest.TestCase):
    def test_condition_order_is_counterbalanced(self):
        names = ('baseline', 'old', 'changed')
        orders = [rc.rotate_conditions(names, n, 0) for n in range(1, 4)]
        self.assertEqual({order[0] for order in orders}, set(names))
        self.assertTrue(all(set(order) == set(names) for order in orders))

    def test_paired_worktrees_and_reference_fixes(self):
        for name in ('cross', 'build', 'git', 'debug', 'debug_inventory', 'debug_intervals', 'debug_cache', 'edit_batch', 'debug_ledger', 'debug_nested', 'edit_multi', 'debug_expand'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                base = Path(td).resolve()
                template, left, right = base / 'template', base / 'left', base / 'right'
                template.mkdir(); left.mkdir(); right.mkdir()
                task = fixtures.TASKS[name]
                state = task['fixture'](template)
                populate = rc.make_worktree_fixture(rc.bare_template(template), state)
                populate(left); populate(right)
                self.assertTrue((left / '.git').is_file())
                self.assertTrue((right / '.git').is_file())
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
                elif name == 'debug':
                    path = left / 'payroll.py'
                    path.write_text(path.read_text().replace('def total_pay(hours, rate, adjustments=[]):',
                        'def total_pay(hours, rate, adjustments=None):\n    if adjustments is None:\n        adjustments = []'))
                else:
                    for path, content in state['reference'].items():
                        target = left / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content)
                self.assertTrue(task['verify'](left, state)[0], name)
                self.assertFalse(task['verify'](right, state)[0])

    def test_adapter_python_keeps_virtualenv_symlink_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / 'real-python'
            target.write_text('#!/bin/sh\n')
            target.chmod(0o755)
            link = root / 'venv/bin/python'
            link.parent.mkdir(parents=True)
            link.symlink_to(target)
            resolved = rc.adapter_python(link)
            self.assertEqual(str(resolved), os.path.abspath(link))
            self.assertNotEqual(resolved, target.resolve())
            self.assertTrue(resolved.is_symlink())

    def test_stats_do_not_replace_missing_usage_with_zero(self):
        item = {'agent':'codex','task':'cross','mode':'baseline','correct':True,'comparison_valid':True,
                'elapsed_ms':1,'trace':{'tool_calls':1,'input_tokens':None}}
        row = rc.summarize([item])[0]
        self.assertEqual(row['input_tokens'], {'n':0,'median':None,'min':None,'max':None})

    def test_pair_requires_identical_inputs_and_starting_git_state(self):
        provenance = {key: key for key in ('prompt_sha256','fixture_manifest_sha256',
                      'initial_staged_diff_sha256','initial_unstaged_diff_sha256')}
        a = {'agent':'codex','provenance':provenance,'comparison_valid':True,'comparison_invalid_reasons':[]}
        b = copy.deepcopy(a)
        self.assertEqual(rc.validate_pair(a, b), [])
        b['provenance']['initial_staged_diff_sha256'] = 'different'
        self.assertEqual(rc.validate_pair(a, b), ['initial_staged_diff_sha256'])
        self.assertFalse(a['comparison_valid'])
        self.assertFalse(b['comparison_valid'])

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
            {'name':'mcp__ffsake__ffsake_task_output','text':'{}','input':{},
             'output':'{"text":"TEST STARTED", "complete": false}'}],
            'TEST STARTED\nTEST FINISHED')
        self.assertTrue(build['passed'])
        build_no_live = rc.workflow_evidence('codex', 'build', [
            {'name':'mcp__ffsake__ffsake_run_task','text':'{}','input':{}},
            {'name':'mcp__ffsake__ffsake_task_output','text':'{}','input':{},
             'output':'{"text":"TEST STARTED", "complete": true}'}],
            'TEST STARTED\nTEST FINISHED')
        self.assertFalse(build_no_live['passed'])
        debug = rc.workflow_evidence('codex', 'debug', [
            {'name':'command_execution','text':"printf 'b 15\\np adjustments\\nq\\n' | python3 -m pdb payroll.py",'input':{},
             'output':'(Pdb) b 15\nBreakpoint 1\n(Pdb) p adjustments\n[50.0]'}],
            '(Pdb) b 15\nBreakpoint 1\n(Pdb) p adjustments\n[50.0]')
        self.assertTrue(debug['passed'])
        unordered = rc.workflow_evidence('claude', 'git', [
            {'name':'Bash','text':'git commit -m fix','input':{}},
            {'name':'Bash','text':'git diff --cached','input':{}}], '')
        self.assertFalse(unordered['passed'])
        only_stage = rc.workflow_evidence('claude', 'git', [
            {'name':'mcp__ffsake__ffsake_git_review','text':'{}','input':{}},
            {'name':'mcp__ffsake__ffsake_git_mutate','text':'{}','input':{'action':'stage'}}], '')
        self.assertFalse(only_stage['passed'])
        reviewed_commit = rc.workflow_evidence('claude', 'git', [
            {'name':'mcp__ffsake__ffsake_git_mutate','text':'{}','input':{'action':'stage'}},
            {'name':'mcp__ffsake__ffsake_git_review','text':'{}','input':{},
             'output':'calculator.py\n+ return left + right'},
            {'name':'mcp__ffsake__ffsake_git_mutate','text':'{}','input':{'action':'commit'}}], '')
        self.assertTrue(reviewed_commit['passed'])

    def test_debugger_evidence_requires_runtime_values(self):
        launch = {'name':'mcp__ffsake__ffsake_debug_launch','text':'{}','input':{}}
        resume = {'name':'mcp__ffsake__ffsake_debug','text':'{"request":{"action":"continue"}}',
                  'input':{'request':{'action':'continue'}},'output':''}
        source_only = rc.workflow_evidence('claude', 'debug', [
            launch,
            {'name':'mcp__ffsake__ffsake_debug','text':'{"request":{"action":"inspect"}}',
             'input':{'request':{'action':'inspect'}},
             'output':'text\n{"ok":true,"result":{"status":"stopped","output":"adjustments = [50.0]"}}'},
            resume], '')
        self.assertFalse(source_only['passed'])
        self.assertFalse(source_only['details']['ffsake_inspected_state'])
        self.assertFalse(source_only['details']['ffsake_state_observed'])
        observed = rc.workflow_evidence('claude', 'debug', [
            launch,
            {'name':'mcp__ffsake__ffsake_debug','text':'{"request":{"action":"inspect"}}',
             'input':{'request':{'action':'inspect'}},
             'output':'text\n{"ok":true,"result":{"status":"stopped","result":{"ok":true,"result":{"inspection":{"scopes":[{"variables":[{"name":"adjustments","value":"[50.0]"}]}]}}}}}'},
            resume], '')
        self.assertTrue(observed['passed'])
        wrong_value = rc.workflow_evidence('claude', 'debug', [
            launch,
            {'name':'mcp__ffsake__ffsake_debug','text':'{"request":{"action":"inspect"}}',
             'input':{'request':{'action':'inspect'}},
             'output':'text\n{"ok":true,"result":{"status":"stopped","result":{"ok":true,"result":{"inspection":{"scopes":[{"variables":[{"name":"adjustments","value":"[25.0]"}]}]}}}}}'},
            resume], '')
        self.assertFalse(wrong_value['passed'])
        native_source = rc.workflow_evidence('codex', 'debug', [
            {'name':'command_execution','text':'python3 -m pdb payroll.py','input':{},
             'output':'-> adjustments = [50.0]\n(Pdb) p adjustments\n*** NameError: name \'adjustments\' is not defined'}], '')
        self.assertFalse(native_source['passed'])
        native_command_only = rc.workflow_evidence('codex', 'debug', [
            {'name':'command_execution','text':"printf 'p adjustments\\n' | python3 -m pdb payroll.py",'input':{},
             'output':''}], '')
        self.assertFalse(native_command_only['passed'])
        combined = rc.workflow_evidence('claude', 'debug', [
            {'name':'mcp__ffsake__ffsake_debug_launch',
             'text':'{"configuration":"payroll","resume":true}',
             'input':{'configuration':'payroll','breakpoints':[{'path':'payroll.py','lines':[18]}],
                      'resume':True},
             'output':'text\n{"ok":true,"result":{"status":"stopped","result":{"ok":true,"result":{"inspection":{"scopes":[{"variables":[{"name":"adjustments","value":"[50.0]"}]}]}}}}}'}], '')
        self.assertTrue(combined['passed'])
        self.assertTrue(combined['details']['ffsake_resumed'])
        self.assertTrue(combined['details']['auto_inspection'])
        cli = rc.workflow_evidence('claude', 'debug_ledger', [
            {'name':'Bash','text':"ffsake . --request '{\"op\":\"debug_probe\"}'",'input':{},
             'output':'{"ok":true,"result":{"cleaned":true,"result":{"result":{"inspection":{"scopes":[{"variables":[{"name":"index","value":"0"}]}]}}}}}'}], '')
        self.assertTrue(cli['passed'])
        self.assertTrue(cli['details']['cli_probe'])
        self.assertTrue(cli['details']['cli_cleanup'])
        self.assertFalse(cli['details']['ffsake_launch'])
        probe = rc.workflow_evidence('claude', 'debug', [
            {'name':'mcp__ffsake__ffsake_debug_probe','text':'{"configuration":"payroll"}',
             'input':{'configuration':'payroll','breakpoints':[{'path':'payroll.py','lines':17}]},
             'output':'text\n{"ok":true,"result":{"status":"stopped","result":{"ok":true,"result":{"inspection":{"scopes":[{"variables":[]}]}}},"next_inspection":{"scopes":[{"variables":[{"name":"adjustments","value":"[50.0]"}]}]}}}}'}], '')
        self.assertTrue(probe['passed'])
        self.assertTrue(probe['details']['ffsake_state_observed'])
        native_source_list = rc.workflow_evidence('codex', 'debug', [
            {'name':'command_execution','text':"printf 'p adjustments\\nl\\nq\\n' | python3 -m pdb payroll.py",'input':{},
             'output':'(Pdb) l\n  7     adjustments = [50.0]'}], '')
        self.assertFalse(native_source_list['passed'])

    def test_usage_totals_are_provider_normalized(self):
        claude = {'input_tokens':2,'cache_read_input_tokens':10,'cache_creation_input_tokens':3}
        rc.normalize_usage('claude', claude)
        self.assertEqual(claude['total_input_tokens'], 15)
        self.assertEqual(claude['uncached_input_tokens'], 2)
        missing = {'input_tokens':2,'cache_read_input_tokens':10}
        rc.normalize_usage('claude', missing)
        self.assertIsNone(missing['total_input_tokens'])
        codex = {'input_tokens':20,'cache_read_input_tokens':8}
        rc.normalize_usage('codex', codex)
        self.assertEqual(codex['total_input_tokens'], 20)
        self.assertEqual(codex['uncached_input_tokens'], 12)


class CliArmTests(unittest.TestCase):
    def test_cli_arm_has_no_mcp_server_and_appends_guidance(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = rc.CLI_GUIDANCE
            rc.CLI_GUIDANCE = 'CLI_GUIDANCE_SENTINEL'
            try:
                with mock.patch.object(rc.suite, 'run_process', return_value={
                        'stdout': '', 'stderr': '', 'exit_code': 0, 'elapsed_ms': 0}) as run:
                    rc.claude(root, 'task', False, 5, 'core', 'cli')
            finally:
                rc.CLI_GUIDANCE = old
            command = run.call_args.args[0]
            config = json.loads((root / '.ffsake-claude-mcp.json').read_text())
            self.assertEqual(config['mcpServers'], {})
            self.assertIn('--append-system-prompt', command)
            self.assertEqual(command[command.index('--append-system-prompt') + 1],
                             'CLI_GUIDANCE_SENTINEL')
            self.assertNotIn('--tools', command)

    def test_forced_guarded_arm_disables_native_file_tools(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = rc.FORCE_GUARDED
            rc.FORCE_GUARDED = True
            try:
                with mock.patch.object(rc.suite, 'run_process', return_value={
                        'stdout': '', 'stderr': '', 'exit_code': 0, 'elapsed_ms': 0}) as run:
                    rc.claude(root, 'task', True, 5, 'core')
            finally:
                rc.FORCE_GUARDED = old
            command = run.call_args.args[0]
            self.assertIn('--disallowedTools', command)
            self.assertEqual(command[command.index('--disallowedTools') + 1],
                             'Read,Edit,Write,Glob,Grep,NotebookEdit')
            guidance = command[command.index('--append-system-prompt') + 1]
            self.assertIn('Native Read, Edit, Write, Glob and Grep are disabled', guidance)


if __name__ == '__main__': unittest.main()
