#!/usr/bin/env python3
"""Release-candidate benchmark driver, built on the existing suite fixtures.

Results are saved after each run. This is opt-in: it sends synthetic fixtures to
the configured model provider and permits code execution in disposable repos.
"""
import argparse
import copy
from contextlib import ExitStack
import hashlib
import json
import os
import platform
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

import agent_benchmark_suite as suite
import rc_benchmark_fixtures as fixtures

suite.TASKS.update(fixtures.TASKS)
GUIDANCE = ('Use FFSAKE MCP for scoped repository reading, search, previews, edits, '
            'Git review and mutation, project tasks, and debugging. Native tools remain '
            'available for commands FFSAKE cannot perform; do not use them to evade access denial.')


def claude(root, prompt, enabled, timeout, profile):
    # The task prompt and native tools are identical in both arms. MCP itself
    # supplies FFSAKE's server guidance; the baseline has no MCP servers.
    cfg = root / '.ffsake-claude-mcp.json'
    servers = {'ffsake': {'command': str(suite.FFSAKE_MCP),
                         'args': suite.mcp_arguments(root, profile)}} if enabled else {}
    cfg.write_text(json.dumps({'mcpServers': servers}))
    cmd = ['claude', '-p', prompt, '--output-format', 'stream-json', '--verbose',
           '--max-turns', '40', '--model', 'sonnet', '--effort', 'low',
           '--dangerously-skip-permissions', '--strict-mcp-config', '--mcp-config', str(cfg)]
    if enabled:
        cmd += ['--append-system-prompt', GUIDANCE]
    return suite.run_process(cmd, root, timeout)


def codex(root, prompt, enabled, timeout, profile):
    cmd = ['codex', 'exec', '--json', '--ignore-user-config', '--skip-git-repo-check',
           '--sandbox', 'workspace-write', '--model', 'gpt-6-astra',
           '-c', 'model_reasoning_effort="low"']
    if enabled:
        cmd += ['-c', 'mcp_servers.ffsake.command=' + json.dumps(str(suite.FFSAKE_MCP)),
                '-c', 'mcp_servers.ffsake.args=' + json.dumps(suite.mcp_arguments(root, profile)),
                '-c', 'developer_instructions=' + json.dumps(GUIDANCE)]
        for name in suite.profile_tools(profile):
            # Intentional noninteractive auto approval for this disposable test,
            # equally available to native repository commands in both arms.
            cmd += ['-c', f'mcp_servers.ffsake.tools.{name}.approval_mode="approve"']
    cmd.append(prompt)
    return suite.run_process(cmd, root, timeout)


def summarize(items):
    rows = []
    for agent, task, mode in sorted({(x['agent'], x['task'], x['mode']) for x in items}):
        group = [x for x in items if (x['agent'], x['task'], x['mode']) == (agent, task, mode)]
        valid = [x for x in group if x['comparison_valid']]
        def stats(values):
            values = [v for v in values if isinstance(v, (float, int)) and not isinstance(v, bool)]
            return {'n': len(values), 'median': statistics.median(values) if values else None,
                    'min': min(values) if values else None, 'max': max(values) if values else None}
        row = {'agent': agent, 'task': task, 'mode': mode, 'runs': len(group),
               'valid': len(valid), 'invalid': len(group) - len(valid),
               'correct': sum(x['correct'] for x in group),
               'correct_valid': sum(x['correct'] for x in valid),
               'correct_rate': sum(x['correct'] for x in group) / len(group),
               'valid_rate': len(valid) / len(group),
               'timeouts': sum(bool(x.get('timed_out')) for x in group),
               'timeout_rate': sum(bool(x.get('timed_out')) for x in group) / len(group),
               'ffsake_used': sum(x['trace'].get('ffsake_tool_calls', 0) > 0 for x in valid),
               'workflow_evidence_passed': sum(x.get('workflow_evidence', {}).get('passed', False) for x in valid),
               'elapsed_ms': stats([x['elapsed_ms'] for x in valid]),
               'tool_calls': stats([x['trace']['tool_calls'] for x in valid])}
        for field in ('input_tokens', 'total_input_tokens', 'uncached_input_tokens',
                      'output_tokens', 'cache_read_input_tokens',
                      'cache_creation_input_tokens', 'total_cost_usd'):
            row[field] = stats([x['trace'].get(field) for x in valid])
        rows.append(row)
    return rows


def error_count(agent, events):
    if agent == 'claude':
        total = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            message = event.get('message')
            if not isinstance(message, dict):
                continue
            for block in message.get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('is_error'):
                    total += 1
        return total
    return sum(1 for event in events if isinstance(event, dict)
               and event.get('type') == 'item.completed'
               and isinstance(event.get('item'), dict)
               and event['item'].get('status') == 'failed')


def tool_calls(agent, events):
    """Extract model-issued tool calls, not lifecycle events or prose."""
    calls = []
    if agent == 'claude':
        for event in events:
            if not isinstance(event, dict):
                continue
            message = event.get('message', {})
            if not isinstance(message, dict) or message.get('role') != 'assistant':
                continue
            for block in message.get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    arguments = block.get('input') or {}
                    calls.append({'name': str(block.get('name') or ''),
                                  'input': arguments if isinstance(arguments, dict) else {},
                                  'text': json.dumps(arguments)})
    elif agent == 'codex':
        for event in events:
            if not isinstance(event, dict) or event.get('type') != 'item.completed':
                continue
            item = event.get('item', {})
            if not isinstance(item, dict):
                continue
            if item.get('type') == 'mcp_tool_call':
                arguments = item.get('arguments') or {}
                calls.append({'name': 'mcp__' + str(item.get('server', '')) + '__' + str(item.get('tool', '')),
                              'input': arguments if isinstance(arguments, dict) else {},
                              'text': json.dumps(arguments)})
            elif item.get('type') == 'command_execution':
                command = str(item.get('command') or '')
                calls.append({'name': 'command_execution', 'input': {'command': command}, 'text': command})
    return calls


def tool_output_text(agent, events):
    """Collect tool results only, excluding assistant prose that could claim success."""
    chunks = []
    if agent == 'claude':
        for event in events:
            if not isinstance(event, dict):
                continue
            message = event.get('message', {})
            if not isinstance(message, dict) or message.get('role') != 'user':
                continue
            for block in message.get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    chunks.extend(suite.recursive_strings(block.get('content')))
    elif agent == 'codex':
        for event in events:
            if not isinstance(event, dict) or event.get('type') != 'item.completed':
                continue
            item = event.get('item', {})
            if not isinstance(item, dict):
                continue
            if item.get('type') == 'command_execution':
                chunks.append(str(item.get('aggregated_output') or ''))
            elif item.get('type') == 'mcp_tool_call':
                chunks.extend(suite.recursive_strings(item.get('result') or {}))
    return '\n'.join(chunks)


def workflow_evidence(agent, task, calls, output):
    """Check trace-visible workflow behavior that a final file verifier cannot prove."""
    names = [call['name'] for call in calls]
    commands = [call['text'] for call in calls]
    lower = output.lower()
    details = {}
    if task == 'build':
        ffsake_run = any('ffsake_run_task' in name for name in names)
        ffsake_observe = any('ffsake_task_output' in name or 'ffsake_task_status' in name for name in names)
        native_run = any('slow_test.py' in command for command in commands)
        markers = 'test started' in lower and 'test finished' in lower
        details = {'ffsake_run_task': ffsake_run, 'ffsake_output_polled': ffsake_observe,
                   'native_task_command': native_run, 'output_markers': markers}
        passed = markers and ((ffsake_run and ffsake_observe) or native_run)
    elif task == 'debug':
        ffsake_launch = any('ffsake_debug_launch' in name or 'ffsake_debug_attach' in name for name in names)
        actions = set()
        for call in calls:
            if 'ffsake_debug' not in call['name']:
                continue
            request = call['input'].get('request', {}) if isinstance(call['input'], dict) else {}
            action = request.get('action') if isinstance(request, dict) else None
            if isinstance(action, str):
                actions.add(action)
        ffsake_inspect = 'breakpoints' in actions and ('scopes' in actions or 'variables' in actions)
        ffsake_resume = 'continue' in actions
        native_debugger = any(any(marker in command.lower() for marker in
                                  ('pdb', 'debugpy', 'lldb', 'gdb', 'dlv', '--inspect'))
                              for command in commands)
        native_state = '(pdb)' in lower or 'breakpoint' in lower or 'stopped' in lower
        details = {'ffsake_launch': ffsake_launch, 'debug_actions': sorted(actions),
                   'ffsake_inspected_state': ffsake_inspect, 'ffsake_resumed': ffsake_resume,
                   'native_debugger': native_debugger, 'native_state_observed': native_state}
        passed = (ffsake_launch and ffsake_inspect and ffsake_resume) or (native_debugger and native_state)
    elif task == 'git':
        review = next((i for i, call in enumerate(calls)
                       if 'ffsake_git_review' in call['name'] or 'git diff --cached' in call['text']
                       or 'git diff --staged' in call['text']), None)
        commit = next((i for i, call in enumerate(calls)
                       if 'ffsake_git_mutate' in call['name'] or 'git commit' in call['text']), None)
        ordered = review is not None and commit is not None and (
            review < commit or (
                review == commit and 'git diff --cached' in calls[review]['text']
                and calls[review]['text'].find('git diff --cached') < calls[review]['text'].find('git commit')))
        details = {'review_index': review, 'commit_index': commit, 'review_before_commit': ordered}
        passed = ordered
    else:
        details = {'workflow_evidence_required': False}
        passed = True
    return {'required': task in ('build', 'debug', 'git'), 'passed': passed, 'details': details}


def agent_provider_errors(agent, events):
    """Stop on structured provider failures rather than scoring them as task failures."""
    errors = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if agent == 'codex' and event.get('type') in ('error', 'turn.failed'):
            message = event.get('message')
            if not message and isinstance(event.get('error'), dict):
                message = event['error'].get('message')
            if message:
                errors.append({'message': str(message)})
        elif agent == 'claude' and event.get('type') == 'error':
            errors.append({'message': str(event.get('message') or event.get('error') or 'provider error')})
        elif agent == 'claude' and event.get('type') == 'result' and event.get('is_error'):
            errors.append({'message': str(event.get('result') or event.get('error') or 'error result')})
    return errors


def normalize_usage(agent, trace):
    """Expose one comparable total while retaining provider-reported components."""
    if agent == 'claude':
        parts = [trace.get('input_tokens'), trace.get('cache_read_input_tokens'),
                 trace.get('cache_creation_input_tokens')]
        if any(isinstance(value, (int, float)) and not isinstance(value, bool) for value in parts):
            trace['total_input_tokens'] = sum(
                value for value in parts if isinstance(value, (int, float)) and not isinstance(value, bool))
        else:
            trace['total_input_tokens'] = None
        trace['uncached_input_tokens'] = trace.get('input_tokens')
    else:
        total = trace.get('input_tokens')
        cached = trace.get('cache_read_input_tokens')
        trace['total_input_tokens'] = total
        trace['uncached_input_tokens'] = (total - cached if isinstance(total, (int, float))
                                         and isinstance(cached, (int, float)) else None)


def make_clone_fixture(template, state, debug_python=None):
    """Populate each run from the same commit, restoring untracked empty dirs/modes.

    A full clone keeps .git inside the disposable workspace. Linked worktrees put
    their index and refs in the template's common directory, which Codex's
    workspace-write sandbox denies; the baseline then cannot commit while the
    unsandboxed MCP server can.
    """
    def fixture(root):
        root.rmdir()  # suite supplies an empty, uniquely reserved directory
        subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(template), str(root)], check=True)
        for key, value in (('user.name', 'FFSAKE Benchmark'),
                           ('user.email', 'ffsake-benchmark@example.invalid'),
                           ('commit.gpgsign', 'false'), ('gc.auto', '0'),
                           ('gc.autoDetach', 'false'), ('maintenance.auto', 'false'),
                           ('core.fsmonitor', 'false'), ('core.autocrlf', 'false')):
            subprocess.run(['git', '-C', str(root), 'config', key, value], check=True)
        for name in sorted(state.get('directories', ()), key=lambda x: (x.count('/'), x)):
            (root / name).mkdir(parents=True, exist_ok=True)
        if os.name == 'posix':
            for name, mode in state.get('modes', {}).items():
                (root / name).chmod(mode)
        if 'staged' in state:
            (root / 'other.txt').write_text(state['staged'])
            subprocess.run(['git', '-C', str(root), 'add', 'other.txt'], check=True)
            (root / 'other.txt').write_text(state['unstaged'])
        if debug_python:
            directory = root / '.ffsake'
            directory.mkdir()
            policy = {'version': 1, 'configurations': {'payroll': {
                'argv': [str(debug_python), '-m', 'debugpy.adapter'],
                'launch': {'program': '${workspaceFolder}/payroll.py', 'python': [str(debug_python)],
                           'console': 'internalConsole', 'justMyCode': True, 'subProcess': False},
                'timeout_seconds': 20, 'session_seconds': 120}}}
            (directory / 'rc-debug.json').write_text(json.dumps(policy))
        return copy.deepcopy(state)
    return fixture


def bare_template(source):
    """Freeze one commit as a local bare origin for every disposable clone."""
    bare = source / 'fixture-git-store'
    subprocess.run(['git', 'clone', '-q', '--bare', str(source), str(bare)], check=True)
    for key, value in (('user.name', 'FFSAKE Benchmark'),
                       ('user.email', 'ffsake-benchmark@example.invalid'),
                       ('commit.gpgsign', 'false'), ('gc.auto', '0'),
                       ('maintenance.auto', 'false'), ('core.fsmonitor', 'false')):
        subprocess.run(['git', '-C', str(bare), 'config', key, value], check=True)
    return bare


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--binary', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--agents', default='claude,codex')
    p.add_argument('--tasks', default='cross,maintenance,build,git,debug')
    p.add_argument('--debug-python', type=Path)
    p.add_argument('--repetitions', type=int, default=5)
    p.add_argument('--timeout', type=int, default=300)
    p.add_argument('--pilot', action='store_true')
    a = p.parse_args()
    agents, tasks = a.agents.split(','), a.tasks.split(',')
    if any(x not in ('claude', 'codex') for x in agents) or any(x not in ('cross', 'maintenance', 'layout', 'build', 'git', 'debug') for x in tasks):
        p.error('unsupported agent or task')
    if 'debug' in tasks and (not a.debug_python or not a.debug_python.is_file()):
        p.error('--debug-python must identify a Python with the validated debugpy adapter')
    if not a.pilot and a.repetitions < 5:
        p.error('measured pairs require at least five repetitions')
    suite.FFSAKE_MCP = a.binary.resolve(strict=True)
    for task in tasks:
        suite.TASKS[task]['prompt'] += '\nWork only in this repository; do not write scratch files elsewhere.'
    old_provenance = suite.task_provenance
    def task_provenance(root, files, prompt):
        result = old_provenance(root, files, prompt)
        for staged in (False, True):
            args = ['git', '-C', str(root), 'diff', '--no-ext-diff', '--binary']
            if staged: args.append('--cached')
            diff = subprocess.run(args, capture_output=True, check=True).stdout
            result['initial_' + ('staged' if staged else 'unstaged') + '_diff_sha256'] = hashlib.sha256(diff).hexdigest()
        return result
    suite.task_provenance = task_provenance
    old_args = suite.mcp_arguments
    def mcp_args(root, profile):
        result = old_args(root, profile)
        policy = root / '.ffsake/rc-debug.json'
        return result + ['--debug', str(policy)] if policy.exists() else result
    suite.mcp_arguments = mcp_args
    suite.claude_run, suite.codex_run = claude, codex
    suite.ffsake_prompt = lambda prompt, tool_profile='all': prompt
    preflight = suite.preflight(agents, 'core')
    if not preflight['ok']:
        raise SystemExit('preflight failed; no model calls made')
    report = {'kind': 'pilot' if a.pilot else 'measured', 'revision': suite.benchmark_provenance(),
              'driver_sha256': suite.file_sha256(Path(__file__)),
              'fixture_sha256': suite.file_sha256(Path(fixtures.__file__)), 'preflight': preflight,
              'agents': agents, 'tasks': tasks, 'repetitions': a.repetitions,
              'settings': {'claude_model': 'sonnet (host alias)', 'claude_effort': 'low',
                           'codex_model': 'gpt-6-astra', 'codex_reasoning_effort': 'low',
                           'approval': 'intentional noninteractive auto allowance for the fixture',
                           'native_tools_available_in_both_arms': True,
                           'task_prompt_identical': True, 'ffsake_profile': 'core',
                           'ffsake_host_guidance': GUIDANCE,
                           'debug_python': str(a.debug_python) if a.debug_python else None,
                           'fixture_isolation': 'full clone per run; .git is inside the disposable workspace',
                           'token_accounting': {
                               'claude': 'total_input_tokens = input_tokens + cache_read + cache_creation',
                               'codex': 'total_input_tokens = input_tokens (includes cached); uncached = input - cached'},
                           'platform': platform.platform(), 'machine': platform.machine(),
                           'python': platform.python_version()},
              'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'results': [], 'aggregate': [], 'complete': False}
    if a.output.exists(): raise SystemExit('output already exists; choose a new path')
    suite.save_report(a.output, report)
    with ExitStack() as stack:
        for task in tasks:
            template = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='ffsake-rc-template-'))).resolve()
            state = suite.TASKS[task]['fixture'](template)
            bare = bare_template(template)
            suite.TASKS[task]['fixture'] = make_clone_fixture(bare, state, a.debug_python if task == 'debug' else None)
        for agent in agents:
            for task in tasks:
                for repetition in range(1, a.repetitions + 1):
                    baseline_first = (repetition + agents.index(agent) + tasks.index(task)) % 2 == 1
                    modes = ('baseline', 'ffsake') if baseline_first else ('ffsake', 'baseline')
                    for mode in modes:
                        print(f'{agent} {task} {mode} {repetition}', flush=True)
                        # Keep a model's parent-directory discovery in an empty,
                        # disposable container rather than the user's shared tmp.
                        with tempfile.TemporaryDirectory(prefix='ffsake-rc-run-') as run_base:
                            previous_tempdir = tempfile.tempdir
                            try:
                                tempfile.tempdir = run_base
                                result = suite.one(agent, task, mode, a.timeout, None, 'core')
                            finally:
                                tempfile.tempdir = previous_tempdir
                        result['repetition'] = repetition
                        events = suite.parse_jsonl(result['stdout'])
                        calls = tool_calls(agent, events)
                        evidence = workflow_evidence(agent, task, calls, tool_output_text(agent, events))
                        result['workflow_evidence'] = evidence
                        result['workflow_evidence_reviewed'] = evidence['passed']
                        result['correct'] = bool(result['correct'] and evidence['passed'])
                        result['trace']['observed_tool_errors'] = error_count(agent, events)
                        result['trace']['retries'] = None  # Repeated calls are not necessarily retries.
                        normalize_usage(agent, result['trace'])
                        provider_errors = result['provider_errors'] + agent_provider_errors(agent, events)
                        result['provider_errors'] = provider_errors
                        if provider_errors and 'provider_api_error' not in result['comparison_invalid_reasons']:
                            result['comparison_invalid_reasons'].append('provider_api_error')
                            result['comparison_valid'] = False
                        result['reported_model'] = next((e.get('model') for e in events
                            if e.get('type') == 'system' and e.get('subtype') == 'init' and e.get('model')), None)
                        result['human_interventions'] = 0  # unattended, auto-approved fixture
                        if agent == 'claude':
                            # The legacy MCP error counter only understands Codex events.
                            result['trace']['mcp_failed_tool_calls'] = None
                            result['trace']['mcp_approval_blocked_calls'] = None
                        # This study explicitly allows native fallback in the FFSAKE arm.
                        result['comparison_invalid_reasons'] = [x for x in result['comparison_invalid_reasons'] if x != 'tool_policy_violation']
                        result['comparison_valid'] = not result['comparison_invalid_reasons']
                        report['results'].append(result)
                        report['aggregate'] = summarize(report['results'])
                        suite.save_report(a.output, report)
                        print(json.dumps({'correct': result['correct'], 'valid': result['comparison_valid'],
                                          'evidence': evidence['passed'], 'elapsed_ms': result['elapsed_ms'],
                                          'tools': result['trace']['tool_names'],
                                          'invalid': result['comparison_invalid_reasons']}), flush=True)
                        if result['provider_errors']:
                            report['stopped_reason'] = 'provider_error'
                            suite.save_report(a.output, report)
                            raise SystemExit('provider error; trace saved')
    report['complete'] = True
    suite.save_report(a.output, report)


if __name__ == '__main__':
    main()
