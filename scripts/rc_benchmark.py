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
import re
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

import agent_benchmark_suite as suite
import rc_benchmark_fixtures as fixtures

suite.TASKS.update(fixtures.TASKS)
TIMING_FILE = None
EDITING_CATALOG = tuple(name for name in suite.FFSAKE_CORE_TOOLS if not (
    name.startswith('ffsake_debug') or name in ('ffsake_task_register', 'ffsake_run_task',
    'ffsake_task_status', 'ffsake_task_output', 'ffsake_task_cancel', 'ffsake_git_review',
    'ffsake_git_mutate', 'ffsake_git_branches', 'ffsake_git_operation_review',
    'ffsake_git_operation')))
CATALOG = 'core'
COMPACT = False
CONTEXT = False
CONTEXT_MS = None
SERVER_EDITING = False

GUIDANCE = ('Use FFSAKE MCP for scoped repository reading, search, previews, edits, '
            'Git review and mutation, project tasks, and debugging. Native tools remain '
            'available for commands FFSAKE cannot perform; do not use them to evade access denial.')

CLI_GUIDANCE = ''
FORCE_GUARDED = False
FORCE_READS_CLI = False
PERMISSION_PROFILES = False
CLI_HOOK_POLICY = False
CLI_TOOLS_ONLY = False
CLI_REDIRECT_READS = False
CLAUDE_MODEL = 'sonnet'
CLAUDE_EFFORT = 'low'


def cli_guidance(binary, split=False):
    if split:
        return (
            f"The guarded FFSAKE CLI is at {binary}. Use it for repository reads and debugging, "
            "and use native Edit/Write for code changes.\n"
            f"{binary} read . main.py:1-40 tests/test_main.py:1-80\n"
            f"{binary} probe . --debug .ffsake/rc-debug.json --config main --breakpoint main.py:12 --stops 2\n"
            "Replace the example filenames and line numbers with the real ones. Use a literal dot for the workspace and no shell variables or quotes; the host read "
            "block rejects commands it cannot parse. Run one command per Bash call. Read only the "
            "lines you need. Probe returns compact stop values; do not repeat the same stops with "
            "pdb. Never bypass an access denial."
        )
    return (
        f"The guarded FFSAKE CLI is at {binary}. Use it for repository reads and debugging, and "
        "use native Edit/Write for code changes.\n"
        f"{binary} read . main.py:1-40 tests/test_main.py:1-80\n"
        f"{binary} probe . --debug .ffsake/rc-debug.json --config main --breakpoint main.py:12 --stops 2\n"
        "Replace the example filenames and line numbers with the real ones. Use a literal dot for the workspace and no shell variables or quotes; the host read "
        "block rejects commands it cannot parse. Run one command per Bash call. Read only the "
        "lines you need. Probe returns compact stop values. Never bypass an access denial."
    )

def claude(root, prompt, enabled, timeout, profile, mode=None):
    # The task prompt and native tools are identical in both arms. MCP itself
    # supplies FFSAKE's server guidance; the baseline has no MCP servers.
    cfg = root.parent / 'ffsake-claude-mcp.json'
    if mode == 'cli':
        cfg.write_text(json.dumps({'mcpServers': {}}))
        guidance = CLI_GUIDANCE
        if FORCE_GUARDED:
            guidance += (' Native Read, Edit, Write, Glob and Grep are disabled for this run. '
                         'Use the CLI for all repository reads and edits, and use Bash only to run '
                         'programs and tests, not to read or modify repository files.')
        elif FORCE_READS_CLI:
            guidance += (' Native Read, Glob and Grep are disabled for this run. Use the CLI for '
                         'repository reads and native Edit/Write for changes.')
        cmd = ['claude', '-p', prompt, '--output-format', 'stream-json', '--verbose',
               '--max-turns', '40', '--model', CLAUDE_MODEL, '--effort', CLAUDE_EFFORT,
               '--strict-mcp-config', '--mcp-config', str(cfg)]
        if CLI_REDIRECT_READS:
            hook = Path(__file__).with_name('claude_read_redirect_hook.py').resolve()
            settings = root.parent / 'ffsake-read-policy.json'
            settings.write_text(json.dumps({'hooks': {'PreToolUse': [{
                'matcher': 'Read|Bash',
                'hooks': [{'type': 'command',
                           'command': f'FFSAKE_BIN={suite.CLI_BINARY} {sys.executable} {hook}'}],
            }]}}))
            cmd += ['--settings', str(settings), '--dangerously-skip-permissions']
        elif CLI_TOOLS_ONLY:
            cmd += ['--tools', 'Bash', 'Edit', 'Write', '--dangerously-skip-permissions']
        elif CLI_HOOK_POLICY:
            hook = Path(__file__).with_name('claude_pre_tool_policy.py').resolve()
            settings = root.parent / 'ffsake-claude-policy.json'
            settings.write_text(json.dumps({'hooks': {'PreToolUse': [{
                'matcher': 'Read|Glob|Grep|Bash',
                'hooks': [{'type': 'command', 'command': f'{sys.executable} {hook}'}],
            }]}}))
            cmd += ['--settings', str(settings),
                    '--allowedTools', 'Bash', 'Edit', 'Write']
        elif PERMISSION_PROFILES:
            cmd += ['--tools', 'Bash', 'Edit', 'Write', '--allowedTools', 'Bash', 'Edit', 'Write',
                    '--disallowedTools', 'Read', 'Glob', 'Grep']
        else:
            cmd += ['--dangerously-skip-permissions']
        if guidance:
            cmd += ['--append-system-prompt', guidance]
        if FORCE_GUARDED:
            cmd += ['--disallowedTools', 'Read,Edit,Write,Glob,Grep,NotebookEdit']
        elif FORCE_READS_CLI:
            cmd += ['--disallowedTools', 'Read,Glob,Grep']
        return suite.run_process(cmd, root, timeout)
    servers = {'ffsake': {'command': str(suite.FFSAKE_MCP),
                         'args': suite.mcp_arguments(root, profile)}} if enabled else {}
    cfg.write_text(json.dumps({'mcpServers': servers}))
    cmd = ['claude', '-p', prompt, '--output-format', 'stream-json', '--verbose',
           '--max-turns', '40', '--model', CLAUDE_MODEL, '--effort', CLAUDE_EFFORT,
           '--strict-mcp-config', '--mcp-config', str(cfg)]
    if PERMISSION_PROFILES:
        allowed = ('Bash,Edit,Write' if mode == 'cli'
                   else 'Bash,Read,Edit,Write,Glob,Grep')
        tools = allowed.split(',')
        cmd += ['--tools', *tools, '--allowedTools', *tools]
        if mode == 'cli':
            cmd += ['--disallowedTools', 'Read', 'Glob', 'Grep']
    else:
        cmd += ['--dangerously-skip-permissions']
    if enabled:
        cmd += ['--append-system-prompt',
                GUIDANCE + (' Native Read, Edit, Write, Glob and Grep are disabled for this run; '
                            'use FFSAKE MCP for all repository reads and edits. Use Bash only to run '
                            'programs and tests, not to read or modify repository files.'
                            if FORCE_GUARDED else '')]
        if FORCE_GUARDED:
            cmd += ['--disallowedTools', 'Read,Edit,Write,Glob,Grep,NotebookEdit']
    return suite.run_process(cmd, root, timeout)


def initial_context(root, profile):
    """Collect a labelled startup snapshot with the same read-only MCP tools."""
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'ffsake_workspace_info', 'arguments': {}}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'ffsake_tasks', 'arguments': {}}},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'ffsake_git', 'arguments': {'query': 'status'}}},
    ]
    started = time.perf_counter()
    run = subprocess.run([str(suite.FFSAKE_MCP), *suite.mcp_arguments(root, profile)],
        input=''.join(json.dumps(r) + '\n' for r in requests), text=True, capture_output=True, timeout=20, check=True)
    responses = [json.loads(line) for line in run.stdout.splitlines()]
    if len(responses) != 3:
        raise RuntimeError('startup discovery returned incomplete responses')
    bodies = [json.loads(x['result']['content'][0]['text']) for x in responses]
    if any(x.get('ok') is not True for x in bodies):
        raise RuntimeError('startup discovery failed')
    return json.dumps(bodies, separators=(',', ':')), (time.perf_counter() - started) * 1000


def codex(root, prompt, enabled, timeout, profile):
    global CONTEXT_MS
    cmd = ['codex', 'exec', '--json', '--ignore-user-config', '--skip-git-repo-check',
           '--sandbox', 'workspace-write', '--model', 'gpt-6-astra',
           '-c', 'model_reasoning_effort="low"']
    context_ms = 0
    guidance = GUIDANCE
    if enabled and CONTEXT:
        context, context_ms = initial_context(root, profile)
        CONTEXT_MS = context_ms
        guidance += (' Startup snapshot from FFSAKE workspace_info, tasks and Git status follows. '
                     'It may become stale; recheck when needed. In a child or fork still verify its root: ' + context)
    if enabled:
        command = str(suite.FFSAKE_MCP)
        arguments = suite.mcp_arguments(root, profile)
        if SERVER_EDITING:
            arguments[arguments.index('--profile') + 1] = 'editing'
        if TIMING_FILE is not None:
            command = str(Path(__file__).with_name('mcp_timing_proxy.py'))
            arguments = ['--binary', str(suite.FFSAKE_MCP), '--log', str(TIMING_FILE), *(['--compact'] if COMPACT else []), *arguments]
        cmd += ['-c', 'mcp_servers.ffsake.command=' + json.dumps(command),
                '-c', 'mcp_servers.ffsake.args=' + json.dumps(arguments),
                '-c', 'developer_instructions=' + json.dumps(guidance)]
        if CATALOG == 'editing':
            cmd += ['-c', 'mcp_servers.ffsake.enabled_tools=' + json.dumps(list(EDITING_CATALOG))]
        for name in suite.profile_tools(profile):
            # Intentional noninteractive auto approval for this disposable test,
            # equally available to native repository commands in both arms.
            cmd += ['-c', f'mcp_servers.ffsake.tools.{name}.approval_mode="approve"']
    cmd.append(prompt)
    result = suite.run_process(cmd, root, timeout)
    result['elapsed_ms'] += context_ms
    return result


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
               'artifact_correct': sum(x.get('artifact_correct', x['correct']) for x in group),
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


def rotate_conditions(names, repetition, task_index):
    shift = (repetition - 1 + task_index) % len(names)
    return names[shift:] + names[:shift]


def validate_pair(first, second):
    """Invalidate both arms if the frozen prompt or starting state differs."""
    keys = ('prompt_sha256', 'fixture_manifest_sha256',
            'initial_staged_diff_sha256', 'initial_unstaged_diff_sha256')
    mismatches = [key for key in keys if not first['provenance'].get(key)
                  or first['provenance'].get(key) != second['provenance'].get(key)]
    if first['agent'] == 'claude' and (not first.get('reported_model') or
                                       first.get('reported_model') != second.get('reported_model')):
        mismatches.append('reported_model')
    if mismatches:
        for result in (first, second):
            result['comparison_invalid_reasons'].append('paired_input_mismatch:' + ','.join(mismatches))
            result['comparison_valid'] = False
    return mismatches


def hardware_info():
    result = {'cpu': None, 'memory_bytes': None, 'model': None}
    if platform.system() == 'Darwin':
        for key, name in (('cpu', 'machdep.cpu.brand_string'), ('memory_bytes', 'hw.memsize'), ('model', 'hw.model')):
            try:
                value = subprocess.check_output(['sysctl', '-n', name], text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
                result[key] = int(value) if key == 'memory_bytes' else value
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    return result


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
        by_id = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            message = event.get('message', {})
            if not isinstance(message, dict):
                continue
            blocks = message.get('content', [])
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if message.get('role') == 'assistant' and block.get('type') == 'tool_use':
                    arguments = block.get('input') or {}
                    call = {'name': str(block.get('name') or ''),
                            'input': arguments if isinstance(arguments, dict) else {},
                            'text': json.dumps(arguments), 'output': ''}
                    calls.append(call)
                    if block.get('id'):
                        by_id[block.get('id')] = call
                elif message.get('role') == 'user' and block.get('type') == 'tool_result':
                    call = by_id.get(block.get('tool_use_id'))
                    if call is not None:
                        call['output'] = '\n'.join(suite.recursive_strings(block.get('content')))
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
                              'text': json.dumps(arguments),
                              'output': '\n'.join(suite.recursive_strings(item.get('result') or {}))})
            elif item.get('type') == 'command_execution':
                command = str(item.get('command') or '')
                calls.append({'name': 'command_execution', 'input': {'command': command}, 'text': command,
                              'output': str(item.get('aggregated_output') or '')})
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


_DEBUGGER_TOKENS = ('pdb', 'debugpy', 'lldb', 'gdb', 'dlv', '--inspect')
_DEBUG_PROMPT = re.compile(r'^\((?:pdb|gdb|lldb)\)\s?(.*)$', re.I)
_DEBUG_COMMAND = re.compile(r'^(?:p|pp|b|break|c|cont|continue|n|next|s|step|q|quit|w|where|up|down|l|list|cl|clear|disable|enable|ignore|run|restart|j|jump|until|u)\b')
_VALUE_PREFIX = re.compile(r'^(?:\[|\(|\{|["\']|[-+]?\d|True\b|False\b|None\b|\$)', re.I)
_ASSIGNED_VALUE = re.compile(r'^(?:\([\w ]+\)\s*)?\$?\d*\s*=\s*.+')


def _embedded_json(text):
    text = (text or '').strip()
    if text.startswith('text\n'):
        text = text[5:].strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        start = text.find('{')
        if start < 0:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
            return value
        except ValueError:
            return None


def _nested_key(value, key):
    if isinstance(value, dict):
        if key in value:
            return True
        return any(_nested_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_nested_key(child, key) for child in value)
    return False


def _debug_variables(payload):
    found = []

    def visit(value):
        if isinstance(value, dict):
            variables = value.get('variables')
            if isinstance(variables, list):
                for item in variables:
                    if (isinstance(item, dict) and isinstance(item.get('name'), str)
                            and item['name'] and isinstance(item.get('value'), str)
                            and item['value']):
                        found.append(item)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return found


def _runtime_value_matches(value, marker):
    text = str(value)
    if re.fullmatch(r'\d+(?:\.\d+)?', marker):
        number = re.escape(marker)
        if '.' not in marker:
            number += r'(?:\.0+)?'
        return re.search(r'(?<![\d.])' + number + r'(?![\d.])', text) is not None
    return re.search(r'(?<!\w)' + re.escape(marker) + r'(?!\w)', text, re.I) is not None


def _debugger_print_mentions(command, name):
    command = command.replace('\\n', '\n')
    for expression in re.findall(r'\b(?:p|pp)\s+([^;\n\\]+)', command):
        if re.search(r'\b' + re.escape(name) + r'\b', expression):
            return True
    return False


def _looks_like_runtime_value(payload):
    if '=' in payload and not _ASSIGNED_VALUE.match(payload):
        return False
    return bool(_VALUE_PREFIX.match(payload))


def _debugger_response_values(text):
    values = []
    collecting = False
    for line in (text or '').splitlines():
        prompt = _DEBUG_PROMPT.match(line.strip())
        if prompt:
            payload = prompt.group(1).strip()
            if not payload or _DEBUG_COMMAND.match(payload) or payload.startswith('***'):
                collecting = True
            else:
                collecting = False
                if _looks_like_runtime_value(payload):
                    values.append(payload)
            continue
        if collecting:
            payload = line.strip()
            if (payload and not payload.startswith(('>', '->', '***'))
                    and _looks_like_runtime_value(payload)):
                values.append(payload)
    return values


def _native_debugger_state(calls, markers):
    name, marker = markers
    for call in calls:
        command = call.get('text', '') or ''
        if not any(token in command.lower() for token in _DEBUGGER_TOKENS):
            continue
        if not _debugger_print_mentions(command, name):
            continue
        if any(_runtime_value_matches(value, marker)
               for value in _debugger_response_values(call.get('output', ''))):
            return True
    return False


def _contains_true(value, key):
    if isinstance(value, dict):
        return value.get(key) is True or any(_contains_true(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_true(child, key) for child in value)
    return False


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
        live_mcp = any('ffsake_task_output' in c['name'] and 'test started' in c.get('output', '').lower()
                       and re.search(r'"complete"\s*:\s*false', c.get('output', ''), re.I) for c in calls)
        live_native = any(('&' in c['text'] or 'tail -f' in c['text'])
                          and 'test started' in c.get('output', '').lower()
                          and 'test finished' not in c.get('output', '').lower() for c in calls)
        details = {'ffsake_run_task': ffsake_run, 'ffsake_output_polled': ffsake_observe,
                   'native_task_command': native_run, 'output_markers': markers,
                   'live_mcp_observed': bool(live_mcp), 'live_native_observed': bool(live_native)}
        passed = markers and ((ffsake_run and ffsake_observe and live_mcp) or (native_run and live_native))
    elif task.startswith('debug'):
        ffsake_launch = any('ffsake_debug_probe' in name or 'ffsake_debug_launch' in name or 'ffsake_debug_attach' in name for name in names)
        actions = set()
        for call in calls:
            if 'ffsake_debug' not in call['name']:
                continue
            request = call['input'].get('request', {}) if isinstance(call['input'], dict) else {}
            action = request.get('action') if isinstance(request, dict) else None
            if isinstance(action, str):
                actions.add(action)
        markers = suite.TASKS.get(task, {}).get('workflow_markers', ('adjustments', '50'))
        ffsake_inspect = False
        ffsake_state = False
        auto_inspect = False
        combined_start = False
        for call in calls:
            if 'ffsake_debug' not in call['name']:
                continue
            payload = _embedded_json(call.get('output', ''))
            variables = _debug_variables(payload) if payload is not None else []
            if not variables:
                continue
            ffsake_inspect = True
            request = call['input'].get('request', {}) if isinstance(call['input'], dict) else {}
            action = request.get('action') if isinstance(request, dict) else None
            if action == 'continue' and _nested_key(payload, 'inspection'):
                auto_inspect = True
            compact_probe = (call['name'].endswith('ffsake_debug_op')
                             and isinstance(call.get('input'), dict)
                             and call['input'].get('op') == 'probe')
            if (((call['name'].endswith('ffsake_debug_launch') and call['input'].get('resume') is True)
                     or call['name'].endswith('ffsake_debug_probe') or compact_probe)
                    and _nested_key(payload, 'inspection')):
                combined_start = True
                auto_inspect = True
            if any(variable['name'] == markers[0]
                   and _runtime_value_matches(variable['value'], markers[1])
                   for variable in variables):
                ffsake_state = True
        ffsake_resume = 'continue' in actions or combined_start
        cli_payloads = []
        for call in calls:
            text = call.get('text') or ''
            cli_probe_command = ('ffsake' in text and
                                 ('debug_probe' in text or ' probe ' in text))
            if cli_probe_command:
                payload = _embedded_json(call.get('output', ''))
                if payload is not None:
                    cli_payloads.append(payload)
        cli_variables = [variable for payload in cli_payloads
                         for variable in _debug_variables(payload)]
        for payload in cli_payloads:
            for stop in payload.get('stops', []) if isinstance(payload, dict) else []:
                if isinstance(stop, dict) and isinstance(stop.get('values'), dict):
                    cli_variables.extend(
                        {'name': name, 'value': str(value)}
                        for name, value in stop['values'].items())
        cli_probe = bool(cli_payloads)
        cli_inspect = bool(cli_variables)
        cli_state = any(variable['name'] == markers[0]
                        and _runtime_value_matches(variable['value'], markers[1])
                        for variable in cli_variables)
        cli_cleanup = any(_contains_true(payload, 'cleaned') for payload in cli_payloads)
        native_debugger = any(any(token in command.lower() for token in _DEBUGGER_TOKENS)
                              for command in commands)
        native_state = _native_debugger_state(calls, markers)
        details = {'ffsake_launch': ffsake_launch, 'debug_actions': sorted(actions),
                   'ffsake_inspected_state': ffsake_inspect, 'auto_inspection': auto_inspect,
                   'ffsake_resumed': ffsake_resume,
                   'native_debugger': native_debugger, 'native_state_observed': native_state,
                   'ffsake_state_observed': ffsake_state, 'cli_probe': cli_probe,
                   'cli_cleanup': cli_cleanup, 'state_markers': markers}
        passed = ((ffsake_launch and ffsake_inspect and ffsake_resume and ffsake_state)
                  or (cli_probe and cli_inspect and cli_state and cli_cleanup)
                  or (native_debugger and native_state))
    elif task == 'git':
        commit = next((i for i, c in enumerate(calls) if
                       ('ffsake_git_mutate' in c['name'] and c['input'].get('action') == 'commit')
                       or 'git commit' in c['text']), None)
        reviews = [i for i, c in enumerate(calls) if 'ffsake_git_review' in c['name']
                   or 'git diff --cached' in c['text'] or 'git diff --staged' in c['text']]
        stages = [i for i, c in enumerate(calls) if
                  ('ffsake_git_mutate' in c['name'] and c['input'].get('action') == 'stage') or 'git add' in c['text']]
        eligible = [r for r in reviews if commit is not None and any(s <= r <= commit for s in stages)
                    and 'calculator.py' in calls[r].get('output', '')
                    and 'return left + right' in calls[r].get('output', '')]
        review = eligible[-1] if eligible else None
        ordered = review is not None
        if ordered and review == commit:
            text = calls[commit]['text']
            ordered = text.find('git add') < text.find('git diff --cached') < text.find('git commit')
        details = {'review_index': review, 'commit_index': commit, 'review_before_commit': ordered}
        passed = ordered
    else:
        details = {'workflow_evidence_required': False}
        passed = True
    return {'required': task in ('build', 'git') or task.startswith('debug'), 'passed': passed, 'details': details}


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
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in parts):
            trace['total_input_tokens'] = sum(parts)
        else:
            trace['total_input_tokens'] = None
        trace['uncached_input_tokens'] = trace.get('input_tokens')
    else:
        total = trace.get('input_tokens')
        cached = trace.get('cache_read_input_tokens')
        trace['total_input_tokens'] = total
        trace['uncached_input_tokens'] = (total - cached if isinstance(total, (int, float))
                                         and isinstance(cached, (int, float)) else None)


def adapter_python(path):
    """Make the adapter path absolute without resolving a virtualenv symlink."""
    return Path(os.path.abspath(os.fspath(path)))


def make_worktree_fixture(template, state, debug_python=None, debug_program='payroll.py'):
    """Create a linked worktree from one frozen commit and restore fixture modes."""
    if debug_python is not None:
        debug_python = adapter_python(debug_python)

    def fixture(root):
        root.rmdir()  # suite supplies an empty, uniquely reserved directory
        subprocess.run(['git', '-C', str(template), 'worktree', 'add', '-q',
                        '-b', 'bench-' + uuid.uuid4().hex, str(root)], check=True)
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
            policy = {'version': 1, 'configurations': {Path(debug_program).stem: {
                'argv': [str(debug_python), '-m', 'debugpy.adapter'],
                'launch': {'program': '${workspaceFolder}/' + debug_program, 'python': [str(debug_python)],
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
    p.add_argument('--instrument-dir', type=Path, help='private directory for per-run content-free MCP timing logs')
    p.add_argument('--catalog', choices=('core', 'editing', 'compact', 'hybrid'), default='core', help='host-side tool selection')
    p.add_argument('--compact', action='store_true', help='diagnostic compact batch-read responses via proxy')
    p.add_argument('--diagnostic-matrix', action='store_true', help='counterbalance native, core, editing catalog, compact and startup snapshot')
    p.add_argument('--baseline-binary', type=Path, help='counterbalance native, this core binary and --binary (editing for editing tasks, core for debug)')
    p.add_argument('--cli-binary', type=Path, help='add a no-MCP CLI arm using this ffsake binary (Claude only)')
    p.add_argument('--dry-run', action='store_true', help='validate binaries, policies and arm commands without model calls')
    p.add_argument('--force-guarded', action='store_true', help='disable native repository read/edit tools in the MCP arm')
    p.add_argument('--claude-model', default='sonnet', help='Claude model alias or ID for paid runs')
    p.add_argument('--claude-effort', default='low', help='Claude effort level')
    p.add_argument('--cli-only', action='store_true', help='with --cli-binary, run only the CLI arm')
    p.add_argument('--cli-native', action='store_true', help='with --cli-binary, run native and CLI arms only')
    p.add_argument('--split-guidance', action='store_true', help='guide the CLI arm to use native edits and CLI reads/probe')
    p.add_argument('--force-reads-cli', action='store_true', help='disable native Read/Glob/Grep in the CLI arm, keeping Edit/Write')
    p.add_argument('--no-cli-guidance', action='store_true', help='do not add CLI guidance text to the CLI arm')
    p.add_argument('--cli-debug-guidance', action='store_true', help='add CLI guidance only for debug tasks')
    p.add_argument('--permission-profiles', action='store_true', help='use host allowlists instead of skip-all permissions')
    p.add_argument('--cli-hook-policy', action='store_true', help='install a PreToolUse hook that denies native repository reads in the CLI arm')
    p.add_argument('--cli-tools-only', action='store_true', help='expose only Bash/Edit/Write in the CLI arm')
    p.add_argument('--cli-redirect-reads', action='store_true', help='rewrite native Read through ffsake with a PreToolUse hook')
    a = p.parse_args()
    global TIMING_FILE, CATALOG, COMPACT, CONTEXT, CONTEXT_MS, SERVER_EDITING, CLI_GUIDANCE, FORCE_GUARDED, FORCE_READS_CLI, PERMISSION_PROFILES, CLI_HOOK_POLICY, CLI_TOOLS_ONLY, CLI_REDIRECT_READS, CLAUDE_MODEL, CLAUDE_EFFORT
    CATALOG = a.catalog
    FORCE_GUARDED = a.force_guarded
    PERMISSION_PROFILES = a.permission_profiles
    CLI_HOOK_POLICY = a.cli_hook_policy
    CLI_TOOLS_ONLY = a.cli_tools_only
    CLI_REDIRECT_READS = a.cli_redirect_reads
    FORCE_READS_CLI = a.force_reads_cli
    CLAUDE_MODEL = a.claude_model
    CLAUDE_EFFORT = a.claude_effort
    COMPACT = a.compact
    if a.diagnostic_matrix and (not a.instrument_dir or a.catalog != 'core' or a.compact or a.agents != 'codex'):
        p.error('--diagnostic-matrix requires Codex, --instrument-dir, and default catalog/compact options')
    if a.baseline_binary and (a.diagnostic_matrix or not a.instrument_dir or a.catalog != 'core' or a.compact):
        p.error('--baseline-binary requires --instrument-dir and default diagnostic options')
    if a.cli_binary and (a.baseline_binary or a.diagnostic_matrix or a.agents != 'claude'):
        p.error('--cli-binary supports Claude only and cannot be combined with baseline or diagnostic modes')
    if (a.cli_only or a.cli_native or a.force_reads_cli) and not a.cli_binary:
        p.error('--cli-only, --cli-native and --force-reads-cli require --cli-binary')
    if COMPACT and not a.instrument_dir:
        p.error('--compact requires --instrument-dir')
    if a.instrument_dir:
        a.instrument_dir = a.instrument_dir.resolve()
        a.instrument_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    agents, tasks = a.agents.split(','), a.tasks.split(',')
    if any(x not in ('claude', 'codex') for x in agents) or any(x not in ('cross', 'maintenance', 'layout', 'build', 'git', 'debug', 'debug_shipping', 'debug_inventory', 'debug_intervals', 'debug_cache', 'edit_batch', 'debug_ledger', 'debug_nested', 'edit_multi', 'debug_expand', 'edit_units', 'debug_cursor') for x in tasks):
        p.error('unsupported agent or task')
    if not a.pilot and 'codex' in agents and 'git' in tasks:
        p.error('Codex Git comparison unavailable: native workspace sandbox denies linked-worktree Git metadata while MCP can write it')
    if any(task.startswith('debug') for task in tasks) and (not a.debug_python or not a.debug_python.is_file()):
        p.error('--debug-python must identify a Python with the validated debugpy adapter')
    if not a.pilot and a.repetitions < 5:
        p.error('measured pairs require at least five repetitions')
    suite.FFSAKE_MCP = a.binary.resolve(strict=True)
    suite.CLI_BINARY = a.cli_binary.resolve(strict=True) if a.cli_binary else None
    CLI_GUIDANCE = ('' if a.no_cli_guidance else
                    cli_guidance(suite.CLI_BINARY, a.split_guidance or a.force_reads_cli)
                    if suite.CLI_BINARY else '')
    suite.CLI_GUIDANCE = CLI_GUIDANCE
    changed_binary = suite.FFSAKE_MCP
    baseline_binary = a.baseline_binary.resolve(strict=True) if a.baseline_binary else None
    for task in tasks:
        suite.TASKS[task]['prompt'] += ('\nWork only in this repository; do not write scratch files elsewhere. '
                                        'Do not use process-name-based termination or signal processes outside this task. '
                                        'Do not ask the user questions; inspect the repository and act autonomously.')
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
    preflight = suite.preflight(agents, a.catalog)
    if not preflight['ok']:
        raise SystemExit('preflight failed; no model calls made')
    if baseline_binary:
        old_preflight = suite.mcp_profile_preflight('core', baseline_binary)
        if not old_preflight['ok']:
            raise SystemExit('baseline MCP preflight failed; no model calls made')
    cli_preflight = None
    if a.cli_binary:
        cli_preflight = suite.cli_preflight(a.cli_binary)
        if not cli_preflight['ok']:
            raise SystemExit('CLI preflight failed; no model calls made')
    if a.dry_run:
        print(json.dumps({
            'ok': True, 'model_calls': 0, 'agents': agents, 'tasks': tasks,
            'repetitions': a.repetitions,
            'arms': (['cli'] if a.cli_only else
                     ['baseline', 'cli'] if a.cli_native else
                     ['baseline', 'ffsake', 'cli'] if a.cli_binary else
                     ['baseline', 'ffsake']),
            'planned_attempts': (1 if a.cli_only else 2 if a.cli_native or not a.cli_binary else 3)
                * len(tasks) * a.repetitions,
            'preflight': preflight, 'cli_preflight': cli_preflight,
            'cli_guidance': CLI_GUIDANCE,
            'mcp_catalog': suite.mcp_profile_preflight('core', a.binary)['catalog_bytes']
                if suite.mcp_profile_preflight('core', a.binary)['ok'] else None,
        }, indent=2))
        return
    report = {'kind': 'pilot' if a.pilot else 'measured', 'revision': suite.benchmark_provenance(),
              'driver_sha256': suite.file_sha256(Path(__file__)),
              'fixture_sha256': suite.file_sha256(Path(fixtures.__file__)), 'preflight': preflight,
              'agents': agents, 'tasks': tasks, 'repetitions': a.repetitions,
              'baseline_binary_sha256': suite.file_sha256(baseline_binary) if baseline_binary else None,
              'cli_binary_sha256': suite.file_sha256(a.cli_binary) if a.cli_binary else None,
              'cli_preflight': cli_preflight,
              'force_guarded': FORCE_GUARDED,
              'permission_profiles': PERMISSION_PROFILES,
              'cli_hook_policy': CLI_HOOK_POLICY,
              'disallowed_native_tools': ['Read', 'Edit', 'Write', 'Glob', 'Grep', 'NotebookEdit'] if FORCE_GUARDED else [],
              'cli_guidance': CLI_GUIDANCE,
              'settings': {'claude_model': CLAUDE_MODEL, 'claude_effort': CLAUDE_EFFORT,
                           'codex_model': 'gpt-6-astra', 'codex_reasoning_effort': 'low',
                           'approval': 'intentional noninteractive auto allowance for the fixture',
                           'native_tools_available_in_both_arms': True,
                           'task_prompt_identical': True, 'ffsake_profile': 'core',
                           'host_catalog': a.catalog, 'mcp_proxy_instrumented': bool(a.instrument_dir), 'diagnostic_compact': a.compact, 'diagnostic_matrix': a.diagnostic_matrix,
                           'build_comparison': bool(baseline_binary), 'changed_profile': 'editing for editing tasks; core for debug' if baseline_binary else None,
                           'ffsake_host_guidance': GUIDANCE,
                           'debug_python': str(a.debug_python) if a.debug_python else None,
                           'fixture_isolation': 'linked worktree per run from one frozen bare fixture',
                           'token_accounting': {
                               'claude': 'total_input_tokens = input_tokens + cache_read + cache_creation',
                               'codex': 'total_input_tokens = input_tokens (includes cached); uncached = input - cached'},
                           'platform': platform.platform(), 'machine': platform.machine(),
                           'python': platform.python_version(), 'hardware': hardware_info()},
              'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'results': [], 'aggregate': [], 'complete': False}
    if a.output.exists(): raise SystemExit('output already exists; choose a new path')
    suite.save_report(a.output, report)
    with ExitStack() as stack:
        for task in tasks:
            template = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='ffsake-rc-template-'))).resolve()
            state = suite.TASKS[task]['fixture'](template)
            bare = bare_template(template)
            suite.TASKS[task]['fixture'] = make_worktree_fixture(
                bare, state, a.debug_python if task.startswith('debug') else None,
                suite.TASKS[task].get('debug_program', 'payroll.py'))
        for agent in agents:
            for task in tasks:
                for repetition in range(1, a.repetitions + 1):
                    baseline_first = (repetition + agents.index(agent) + tasks.index(task)) % 2 == 1
                    if a.diagnostic_matrix:
                        base_modes = ('baseline', 'core', 'editing', 'compact', 'context')
                        modes = rotate_conditions(base_modes, repetition, tasks.index(task))
                    elif baseline_binary:
                        base_modes = ('baseline', 'old', 'changed')
                        modes = rotate_conditions(base_modes, repetition, tasks.index(task))
                    elif a.cli_binary:
                        base_modes = (('cli',) if a.cli_only else
                                      ('baseline', 'cli') if a.cli_native else
                                      ('baseline', 'ffsake', 'cli'))
                        modes = rotate_conditions(base_modes, repetition, tasks.index(task))
                    else:
                        modes = ('baseline', 'ffsake') if baseline_first else ('ffsake', 'baseline')
                    group_start = len(report['results'])
                    for mode in modes:
                        CATALOG = 'editing' if mode == 'editing' else a.catalog
                        COMPACT = (mode == 'compact') if a.diagnostic_matrix else a.compact
                        CONTEXT = mode == 'context'
                        CONTEXT_MS = None
                        suite.FFSAKE_MCP = baseline_binary if mode == 'old' else changed_binary
                        SERVER_EDITING = bool(baseline_binary and mode == 'changed' and task in ('cross', 'maintenance', 'layout'))
                        suite_mode = mode if mode in ('baseline', 'ffsake', 'cli') else 'ffsake'
                        if a.cli_debug_guidance and suite.CLI_BINARY:
                            CLI_GUIDANCE = (cli_guidance(suite.CLI_BINARY, True)
                                            if task.startswith('debug') else '')
                        print(f'{agent} {task} {mode} {repetition}', flush=True)
                        # Keep a model's parent-directory discovery in an empty,
                        # disposable container rather than the user's shared tmp.
                        with tempfile.TemporaryDirectory(prefix='ffsake-rc-run-') as run_base:
                            previous_tempdir = tempfile.tempdir
                            previous_tmpdir = os.environ.get('TMPDIR')
                            try:
                                TIMING_FILE = (a.instrument_dir / (uuid.uuid4().hex + '.jsonl')
                                               if a.instrument_dir and agent == 'codex' and suite_mode == 'ffsake' else None)
                                tempfile.tempdir = run_base
                                os.environ['TMPDIR'] = run_base
                                result = suite.one(agent, task, suite_mode, a.timeout, None, a.catalog)
                                if a.diagnostic_matrix or baseline_binary:
                                    result['mode'] = mode
                            finally:
                                tempfile.tempdir = previous_tempdir
                                if previous_tmpdir is None:
                                    os.environ.pop('TMPDIR', None)
                                else:
                                    os.environ['TMPDIR'] = previous_tmpdir
                        result['repetition'] = repetition
                        if baseline_binary:
                            result['server_profile'] = ('editing' if SERVER_EDITING else 'core') if suite_mode == 'ffsake' else None
                        if CONTEXT:
                            result['startup_context'] = {'tool_calls': 3, 'elapsed_ms': CONTEXT_MS,
                                'included_in_elapsed_ms': True, 'included_in_agent_tool_calls': False}
                        if TIMING_FILE is not None:
                            result['mcp_timing_file'] = str(TIMING_FILE)
                            result['mcp_timing_available'] = TIMING_FILE.exists()
                        TIMING_FILE = None
                        events = suite.parse_jsonl(result['stdout'])
                        calls = tool_calls(agent, events)
                        result['trace']['toolsearch_calls'] = sum(
                            1 for call in calls if call['name'] == 'ToolSearch')
                        result['trace']['mcp_calls'] = sum(
                            1 for call in calls if 'mcp__ffsake__' in call['name'])
                        def is_cli_call(call):
                            text = call.get('text') or ''
                            return (call['name'] == 'Bash' and 'ffsake' in text
                                    and any(token in text for token in (
                                        '--request', '--apply', ' read ', ' edit ',
                                        ' apply ', ' probe ')))
                        result['trace']['cli_calls'] = sum(
                            1 for call in calls if is_cli_call(call))
                        result['trace']['cli_adopted'] = result['trace']['cli_calls'] > 0
                        result['trace']['permission_denials'] = sum(
                            len(event.get('permission_denials', []))
                            for event in events
                            if isinstance(event, dict) and isinstance(event.get('permission_denials'), list))
                        evidence = workflow_evidence(agent, task, calls, tool_output_text(agent, events))
                        result['workflow_evidence'] = evidence
                        result['workflow_evidence_reviewed'] = evidence['passed']
                        result['artifact_correct'] = result['correct']
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
                        if len(report['results']) > group_start + 1:
                            mismatch = validate_pair(report['results'][group_start], result)
                            if mismatch:
                                reason = 'paired_input_mismatch:' + ','.join(mismatch)
                                for member in report['results'][group_start:]:
                                    if reason not in member['comparison_invalid_reasons']:
                                        member['comparison_invalid_reasons'].append(reason)
                                    member['comparison_valid'] = False
                            else:
                                prior = [reason for member in report['results'][group_start:-1]
                                         for reason in member['comparison_invalid_reasons']
                                         if reason.startswith('paired_input_mismatch:')]
                                if prior:
                                    result['comparison_invalid_reasons'].append(prior[0])
                                    result['comparison_valid'] = False
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
