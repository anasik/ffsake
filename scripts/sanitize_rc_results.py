#!/usr/bin/env python3
"""Export a content-free event index and numeric results from a private RC run.

This whitelist deliberately omits prompts, paths, shell commands, tool
arguments, tool output, stderr, session identifiers and arbitrary model prose.
The original report must remain private; this is a redacted index, not a full
transcript and cannot independently establish all workflow evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from agent_benchmark_suite import FFSAKE_TOOLS


SAFE_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,120}$')


def name(value):
    return value if isinstance(value, str) and SAFE_NAME.fullmatch(value) else None


def numeric(value):
    return value if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)) else None


def tool_name(value):
    native = {'command_execution', 'file_change', 'web_search', 'Bash', 'Read', 'Write', 'Edit', 'ToolSearch',
              'read', 'shell', 'edit', 'write', 'apply_patch', 'glob', 'grep', 'list', 'task'}
    if not isinstance(value, str):
        return 'other'
    if value in native:
        return value
    if isinstance(value, str) and value.startswith('mcp__ffsake__') and value.removeprefix('mcp__ffsake__') in FFSAKE_TOOLS:
        return value
    return 'other'


def digest(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value) else None


def events(text):
    output = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type')
        if kind not in ('thread.started', 'turn.started', 'turn.completed', 'turn.failed',
                        'error', 'item.started', 'item.updated', 'item.completed'):
            continue
        entry = {'type': kind}
        item = event.get('item')
        if isinstance(item, dict):
            item_types = {'command_execution', 'file_change', 'mcp_tool_call', 'agent_message',
                          'reasoning', 'web_search', 'collab_tool_call'}
            statuses = {'completed', 'failed', 'in_progress', 'declined'}
            entry['item'] = {'type': item.get('type') if item.get('type') in item_types else 'other'}
            if item.get('status') in statuses: entry['item']['status'] = item['status']
            if item.get('server') == 'ffsake': entry['item']['server'] = 'ffsake'
            if item.get('tool') in FFSAKE_TOOLS: entry['item']['tool'] = item['tool']
            if isinstance(item.get('exit_code'), int):
                entry['item']['exit_code'] = item['exit_code']
        output.append(entry)
    return output


def timing_events(path, directory):
    """Export only whitelisted relative timing metadata from private relay logs."""
    root = Path(directory).resolve(strict=True)
    selected = Path(path).resolve(strict=True)
    if not selected.is_relative_to(root) or not selected.is_file() or selected.stat().st_size > 5_000_000:
        raise ValueError('timing file is outside the supplied directory or too large')
    safe_methods = {'initialize', 'tools/list', 'tools/call', 'notifications/initialized',
                    'notifications/roots/list_changed', 'roots/list', 'ping'}
    allowed_directions = {'request', 'response', 'server_execution', 'request_invalid', 'response_invalid'}
    result = []
    for line in selected.read_text().splitlines():
        value = json.loads(line)
        direction = value.get('direction')
        if direction not in allowed_directions:
            continue
        event = {'direction': direction}
        for key in ('ms', 'bytes', 'original_bytes', 'sequence', 'roundtrip_ms', 'execution_ms'):
            if key in value:
                event[key] = numeric(value[key])
        if 'method' in value:
            method = value['method']
            event['method'] = method if isinstance(method, str) and method in safe_methods else 'other'
        if value.get('tool'):
            event['tool'] = value['tool'] if value['tool'] in FFSAKE_TOOLS else 'other'
        if isinstance(value.get('protocol_error'), bool):
            event['protocol_error'] = value['protocol_error']
        result.append(event)
    return result


DEBUG_ACTIONS = {'list', 'poll', 'wait', 'terminate', 'breakpoints', 'threads', 'stack',
                 'scopes', 'variables', 'source', 'inspect', 'continue', 'next', 'step_in',
                 'step_out', 'pause'}


def debug_actions(text):
    """Retain action names only, never debugger arguments or returned values."""
    result = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
            item = event.get('item', {})
            request = item.get('arguments', {}).get('request', {})
            action = request.get('action')
            if (event.get('type') == 'item.completed' and item.get('server') == 'ffsake'
                    and item.get('tool') == 'ffsake_debug' and isinstance(action, str)):
                result.append(action if action in DEBUG_ACTIONS else 'other')
        except (ValueError, TypeError, AttributeError):
            continue
    return result


def claude_debug_actions(text):
    """Retain Claude debug action names without prompts or tool arguments."""
    result = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
            if event.get('type') != 'assistant':
                continue
            for content in event.get('message', {}).get('content', []):
                if content.get('type') != 'tool_use' or content.get('name') != 'mcp__ffsake__ffsake_debug':
                    continue
                action = content.get('input', {}).get('request', {}).get('action')
                if isinstance(action, str):
                    result.append(action if action in DEBUG_ACTIONS else 'other')
        except (ValueError, TypeError, AttributeError):
            continue
    return result


def claude_events(text):
    """Export Claude stream event shapes and tool names, never content."""
    output = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type')
        if kind not in ('system', 'assistant', 'user', 'result'):
            continue
        entry = {'type': kind}
        if kind == 'system' and event.get('subtype') in ('init', 'hook_started', 'hook_response', 'status'):
            entry['subtype'] = event['subtype']
        if kind == 'assistant':
            contents = event.get('message', {}).get('content', [])
            entry['content_types'] = [x.get('type') if x.get('type') in ('text', 'thinking', 'tool_use', 'tool_result') else 'other'
                                      for x in contents if isinstance(x, dict)]
            entry['tools'] = [tool_name(x.get('name')) for x in contents
                              if isinstance(x, dict) and x.get('type') == 'tool_use']
        if kind == 'user':
            contents = event.get('message', {}).get('content', [])
            entry['tool_results'] = [bool(x.get('is_error')) for x in contents
                                     if isinstance(x, dict) and x.get('type') == 'tool_result']
        if kind == 'result':
            entry['is_error'] = event.get('is_error') if isinstance(event.get('is_error'), bool) else None
            entry['num_turns'] = numeric(event.get('num_turns'))
            if event.get('subtype') in ('success', 'error', 'error_max_turns', 'error_during_execution'):
                entry['subtype'] = event['subtype']
        output.append(entry)
    return output


def sanitize(report, timing_dir=None, omit_events=False):
    results = []
    for run in report['results']:
        trace = run.get('trace', {})
        provenance = run.get('provenance', {})
        verification = run.get('verification', {})
        raw_stdout = run.get('stdout', '')
        raw_stderr = run.get('stderr', '')
        row = {key: run.get(key) if isinstance(run.get(key), bool) else None for key in
               ('correct', 'artifact_correct', 'comparison_valid', 'timed_out')}
        row.update({key: numeric(run.get(key)) for key in ('repetition', 'attempt', 'elapsed_ms', 'exit_code')})
        row['task'] = run.get('task') if run.get('task') in {'cross', 'maintenance', 'layout', 'build', 'git', 'debug', 'debug_shipping', 'debug_inventory', 'debug_intervals', 'debug_cache', 'edit_batch', 'debug_ledger', 'debug_nested', 'edit_multi', 'debug_expand', 'edit_units', 'debug_cursor', 'edit_settings', 'edit_refactor', 'debug_retry'} else 'other'
        reasons = run.get('comparison_invalid_reasons')
        row['comparison_invalid_reasons'] = [x for x in reasons if x in {'tool_profile_violation', 'provider_api_error', 'benchmark_setup_error'}] if isinstance(reasons, list) else []
        provider_errors = run.get('provider_errors')
        row['provider_error_count'] = len(provider_errors) if isinstance(provider_errors, list) else None
        if isinstance(run.get('reported_model'), str) and SAFE_NAME.fullmatch(run['reported_model']):
            row['reported_model'] = run['reported_model']
        row['mode'] = run.get('mode') if run.get('mode') in {'baseline', 'ffsake', 'core', 'editing', 'compact', 'context', 'old', 'changed', 'cli'} else 'other'
        if run.get('server_profile') in ('core', 'editing'):
            row['server_profile'] = run['server_profile']
        if isinstance(run.get('startup_context'), dict):
            row['startup_context'] = {key:numeric(run['startup_context'].get(key))
                                      for key in ('tool_calls', 'elapsed_ms')}
        row['provenance_hashes'] = {key: digest(provenance.get(key)) for key in (
            'prompt_sha256', 'fixture_manifest_sha256', 'initial_staged_diff_sha256',
            'initial_unstaged_diff_sha256')}
        row['trace'] = {key: numeric(trace.get(key)) for key in (
            'tool_calls', 'ffsake_tool_calls', 'shell_tool_calls', 'observed_tool_errors',
            'mcp_failed_tool_calls', 'mcp_approval_blocked_calls', 'events', 'num_turns',
            'input_tokens', 'total_input_tokens', 'uncached_input_tokens', 'output_tokens',
            'cache_read_input_tokens', 'cache_creation_input_tokens', 'total_cost_usd', 'retries',
            'toolsearch_calls', 'mcp_calls', 'cli_calls')}
        row['trace']['tool_names'] = [tool_name(x) for x in trace.get('tool_names', [])]
        if row['task'].startswith('debug'):
            row['debug_actions'] = (claude_debug_actions(raw_stdout) if run.get('agent') == 'claude'
                                    else debug_actions(raw_stdout))
        passed = run.get('workflow_evidence', {}).get('passed')
        details = run.get('workflow_evidence', {}).get('details', {})
        allowed_details = ('ffsake_run_task', 'ffsake_output_polled', 'native_task_command',
                           'output_markers', 'live_mcp_observed', 'live_native_observed',
                           'ffsake_launch', 'ffsake_inspected_state', 'ffsake_resumed',
                           'ffsake_state_observed', 'native_debugger', 'native_state_observed',
                           'review_before_commit', 'workflow_evidence_required',
                           'cli_probe', 'cli_cleanup')
        row['workflow_evidence'] = {'passed': passed if isinstance(passed, bool) else None,
                                    'details': {key: details[key] for key in allowed_details
                                                if isinstance(details.get(key), bool)}}
        audit_complete = verification.get('file_audit_complete')
        row['file_audit'] = {'complete': audit_complete if isinstance(audit_complete, bool) else None,
                             'unexpected_count': len(verification['unexpected_file_changes'])
                             if isinstance(verification.get('unexpected_file_changes'), list) else None,
                             'verifier_change_count': len(verification['verification_file_changes'])
                             if isinstance(verification.get('verification_file_changes'), list) else None,
                             'program_exit': numeric(verification.get('exit_code')),
                             'test_exit': numeric(verification.get('test_exit')),
                             'checks': numeric(verification.get('checks')),
                             'checks_passed': numeric(verification.get('passed')),
                             'failed_check_count': len(verification['failed_checks'])
                             if isinstance(verification.get('failed_checks'), list) else None,
                             'task_definition_preserved': verification.get('task_definition_preserved')
                             if isinstance(verification.get('task_definition_preserved'), bool) else None}
        row['private_stdout_sha256'] = hashlib.sha256(raw_stdout.encode()).hexdigest()
        row['private_stderr_sha256'] = hashlib.sha256(raw_stderr.encode()).hexdigest()
        if not omit_events:
            row['events'] = claude_events(raw_stdout) if run.get('agent') == 'claude' else events(raw_stdout)
        if timing_dir and run.get('mcp_timing_file'):
            row['mcp_timing'] = timing_events(run['mcp_timing_file'], timing_dir)
        results.append(row)
    revision = report.get('revision', {})
    settings = report.get('settings', {})
    configuration = {}
    for key in ('cli_redirect_reads', 'opencode_redirect_reads',
                'permission_profiles', 'cli_hook_policy', 'default_permissions'):
        value = settings.get(key, report.get(key))
        if isinstance(value, bool):
            configuration[key] = value
    return {'schema_version': 1, 'kind': 'content-free redacted event index',
            'limitations': 'Commands, arguments, output and private content are omitted; retain the original report privately.',
            'source_revision': digest(revision.get('source_revision')),
            'binary_sha256': digest(revision.get('mcp_binary_sha256')),
            'driver_sha256': digest(report.get('driver_sha256')),
            'fixture_sha256': digest(report.get('fixture_sha256')),
            'harness_sha256': {name: digest(value) for name, value in (revision.get('harness_sha256') or {}).items()
                               if name in ('agent_benchmark_suite.py', 'agent_benchmark.py',
                                           'claude_read_redirect_hook.py', 'opencode_read_redirect_plugin.js')},
            'configuration': configuration,
            'agents': [x if x in ('codex','claude','opencode') else 'other' for x in report.get('agents', [])],
            'tasks': [x if x in ('cross','maintenance','layout','build','git','debug','debug_shipping','debug_inventory','debug_intervals','debug_cache','edit_batch','debug_ledger','debug_nested','edit_multi','debug_expand','edit_units','debug_cursor') else 'other' for x in report.get('tasks', [])],
            'repetitions': numeric(report.get('repetitions')),
            'complete': report.get('complete') if isinstance(report.get('complete'), bool) else None,
            'platform': settings.get('machine') if settings.get('machine') in ('arm64', 'x86_64', 'aarch64') else None,
            'results': results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--task', choices=('cross', 'maintenance', 'layout', 'build', 'git', 'debug', 'debug_shipping', 'debug_inventory', 'debug_intervals', 'debug_cache', 'edit_batch', 'debug_ledger', 'debug_nested', 'edit_multi', 'debug_expand', 'edit_units', 'debug_cursor', 'edit_settings', 'edit_refactor', 'debug_retry'))
    parser.add_argument('--timing-dir', type=Path, help='private directory containing content-free timing logs')
    parser.add_argument('--omit-events', action='store_true', help='omit host event index; retain tool names and timing')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('output already exists')
    result = sanitize(json.loads(args.input.read_text()), args.timing_dir, args.omit_events)
    if args.task:
        result['results'] = [row for row in result['results'] if row['task'] == args.task]
        result['tasks'] = [args.task]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
