#!/usr/bin/env python3
"""Changed-payroll-only check for continue stopped-thread inference.

The exact debug adapter policy used for the paid batch is launched and exercised
without a model before any paid attempt starts.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import agent_benchmark_suite as suite
import debug_smoke
import rc_benchmark as rc


def _json_payload(text):
    return json.loads(text[5:]) if text.startswith('text\n') else json.loads(text)


def preflight(binary, debug_python, timeout=300):
    """Launch the generated policy, inspect one stop and clean up."""
    debug_python = rc.adapter_python(debug_python)
    binary = Path(binary).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='ffsake-continue-thread-preflight-') as td:
        base = Path(td).resolve()
        template = base / 'template'
        template.mkdir()
        root = base / 'root'
        root.mkdir()
        state = suite.TASKS['debug']['fixture'](template)
        bare = rc.bare_template(template)
        fixture = rc.make_worktree_fixture(bare, state, debug_python, 'payroll.py')
        fixture(root)
        policy_path = root / '.ffsake/rc-debug.json'
        policy = json.loads(policy_path.read_text())
        configured = policy['configurations']['payroll']
        assert configured['argv'][0] == str(debug_python), configured
        assert configured['launch']['python'][0] == str(debug_python), configured

        client = debug_smoke.Client(binary, root, policy_path)
        adapter_pid = None
        try:
            info = client.call('workspace_info')
            assert any(item.get('configuration') == 'payroll'
                       and 'adjustments' in item.get('read', {}).get('text', '')
                       for item in info.get('debug_source', [])), info
            launched = client.call('debug_launch', configuration='payroll',
                                   breakpoints=[{'path': 'payroll.py', 'lines': [18]}],
                                   resume=True)
            client.session = launched['session']
            adapter_pid = launched.get('adapter_pid')
            stopped = client.wait(lambda state: state['status'] == 'stopped'
                                  or state['cleaned'], allow_error=True)
            assert not stopped['cleaned'], stopped
            assert stopped['stop']['reason'] == 'breakpoint', stopped
            thread = stopped['stop']['threadId']
            frames = client.action('stack', thread=thread)['frames']
            frame = next((item for item in frames
                          if item.get('handle') and item['source_path'].endswith('payroll.py')), None)
            assert frame is not None, frames
            scopes = client.action('scopes', handle=frame['handle'])['scopes']
            variables = []
            for scope in scopes:
                if scope.get('handle'):
                    variables += client.action('variables', handle=scope['handle'])['variables']
            assert any(item['name'] == 'adjustments' and item['value'] == '[50.0]'
                       for item in variables), variables
            result = {'ok': True, 'adapter_path': str(debug_python),
                      'adapter_pid': adapter_pid, 'variables': variables,
                      'stop': stopped['stop']}
        finally:
            if client.p.poll() is None:
                if client.session:
                    try:
                        client.stop()
                    except Exception:
                        pass
                client.close()
        assert adapter_pid is not None and not debug_smoke.alive(adapter_pid)
        return result


def launch_failure(result):
    for call in rc.tool_calls(result['agent'], suite.parse_jsonl(result['stdout'])):
        if not call['name'].endswith('debug_launch'):
            continue
        try:
            payload = _json_payload(call.get('output', ''))
        except (TypeError, ValueError):
            return 'unreadable debug_launch response'
        body = payload.get('result') or {}
        if body.get('status') == 'failed' or body.get('error'):
            return str(body.get('error') or 'debug_launch failed')
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--debug-python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()

    binary = args.binary.resolve(strict=True)
    debug_python = rc.adapter_python(args.debug_python)
    if not debug_python.is_file():
        raise SystemExit('debug Python is not a file')
    preflight_result = preflight(binary, debug_python, args.timeout)
    print(json.dumps({'preflight': preflight_result}, default=str), flush=True)
    if args.preflight_only:
        return

    if args.output.exists():
        raise SystemExit('output already exists')
    suite.FFSAKE_MCP = binary
    suite.TASKS['debug']['prompt'] += ('\nWork only in this repository; do not write scratch files elsewhere. '
                                       'Do not use process-name-based termination or signal processes outside this task.')
    old_provenance = suite.task_provenance

    def task_provenance(root, files, prompt):
        result = old_provenance(root, files, prompt)
        import hashlib
        import subprocess
        for staged in (False, True):
            command = ['git', '-C', str(root), 'diff', '--no-ext-diff', '--binary']
            if staged:
                command.append('--cached')
            diff = subprocess.run(command, capture_output=True, check=True).stdout
            result['initial_' + ('staged' if staged else 'unstaged') + '_diff_sha256'] = hashlib.sha256(diff).hexdigest()
        return result

    suite.task_provenance = task_provenance
    old_args = suite.mcp_arguments

    def mcp_args(root, profile):
        result = old_args(root, profile)
        policy = root / '.ffsake/rc-debug.json'
        return result + ['--debug', str(policy)] if policy.exists() else result

    suite.mcp_arguments = mcp_args
    suite.claude_run, suite.codex_run = rc.claude, rc.codex
    suite.ffsake_prompt = lambda prompt, tool_profile='all': prompt
    model_preflight = suite.preflight(['claude'], 'core')
    if not model_preflight['ok']:
        raise SystemExit('model preflight failed; no model calls made')

    with tempfile.TemporaryDirectory(prefix='ffsake-continue-thread-template-') as td:
        template = Path(td).resolve()
        state = suite.TASKS['debug']['fixture'](template)
        bare = rc.bare_template(template)
        suite.TASKS['debug']['fixture'] = rc.make_worktree_fixture(
            bare, state, debug_python, 'payroll.py')
        report = {
            'kind': 'measured',
            'revision': {**suite.benchmark_provenance(), 'binary_matches_source_verified': True},
            'driver_sha256': suite.file_sha256(Path(__file__).resolve().parent / 'rc_benchmark.py'),
            'fixture_sha256': suite.file_sha256(Path(__file__).resolve().parent / 'rc_benchmark_fixtures.py'),
            'preflight': {'model': model_preflight, 'debugger': preflight_result},
            'agents': ['claude'], 'tasks': ['debug'], 'repetitions': 5,
            'baseline_binary_sha256': None,
            'settings': {
                'claude_model': 'sonnet (host alias)', 'claude_effort': 'low',
                'approval': 'intentional noninteractive auto allowance for the fixture',
                'native_tools_available_in_both_arms': True,
                'task_prompt_identical': True, 'ffsake_profile': 'core',
                'host_catalog': 'core', 'mcp_proxy_instrumented': False,
                'diagnostic_compact': False, 'diagnostic_matrix': False,
                'build_comparison': False, 'changed_profile': None,
                'ffsake_host_guidance': rc.GUIDANCE,
                'debug_python': str(debug_python),
                'fixture_isolation': 'linked worktree per run from one frozen bare fixture',
                'token_accounting': {
                    'claude': 'total_input_tokens = input_tokens + cache_read + cache_creation'},
                'platform': rc.platform.platform(), 'machine': rc.platform.machine(),
                'python': rc.platform.python_version(), 'hardware': rc.hardware_info(),
            },
            'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'results': [], 'aggregate': [], 'complete': False,
            'notes': ('Changed-payroll-only check for continue stopped-thread inference. '
                      'Five primary paid attempts; the prior unused attempt is reserved for one '
                      'provider/tool-invalid retry. Setup failure aborts the batch.'),
        }
        suite.save_report(args.output, report)
        attempts = 0
        valid = 0
        while valid < 5 and attempts < 6:
            attempts += 1
            repetition = valid + 1
            print(f'claude debug changed attempt={attempts} repetition={repetition}', flush=True)
            with tempfile.TemporaryDirectory(prefix='ffsake-rc-run-') as run_base:
                previous_tempdir = tempfile.tempdir
                previous_tmpdir = os.environ.get('TMPDIR')
                try:
                    tempfile.tempdir = run_base
                    os.environ['TMPDIR'] = run_base
                    result = suite.one('claude', 'debug', 'ffsake', args.timeout, None, 'core')
                finally:
                    tempfile.tempdir = previous_tempdir
                    if previous_tmpdir is None:
                        os.environ.pop('TMPDIR', None)
                    else:
                        os.environ['TMPDIR'] = previous_tmpdir
            result['mode'] = 'changed'
            result['repetition'] = repetition
            result['attempt'] = attempts
            events = suite.parse_jsonl(result['stdout'])
            calls = rc.tool_calls('claude', events)
            evidence = rc.workflow_evidence('claude', 'debug', calls,
                                            rc.tool_output_text('claude', events))
            result['workflow_evidence'] = evidence
            result['workflow_evidence_reviewed'] = evidence['passed']
            result['artifact_correct'] = result['correct']
            result['correct'] = bool(result['correct'] and evidence['passed'])
            result['trace']['observed_tool_errors'] = rc.error_count('claude', events)
            result['trace']['retries'] = None
            rc.normalize_usage('claude', result['trace'])
            provider_errors = result['provider_errors'] + rc.agent_provider_errors('claude', events)
            result['provider_errors'] = provider_errors
            if provider_errors and 'provider_api_error' not in result['comparison_invalid_reasons']:
                result['comparison_invalid_reasons'].append('provider_api_error')
                result['comparison_valid'] = False
            result['reported_model'] = next((event.get('model') for event in events
                if event.get('type') == 'system' and event.get('subtype') == 'init'
                and event.get('model')), None)
            result['human_interventions'] = 0
            result['trace']['mcp_failed_tool_calls'] = None
            result['trace']['mcp_approval_blocked_calls'] = None
            result['comparison_invalid_reasons'] = [item for item in result['comparison_invalid_reasons']
                                                    if item != 'tool_policy_violation']
            failure = launch_failure(result)
            if failure:
                if 'benchmark_setup_error' not in result['comparison_invalid_reasons']:
                    result['comparison_invalid_reasons'].append('benchmark_setup_error')
                result['comparison_valid'] = False
                result['setup_failure'] = failure
            else:
                result['comparison_valid'] = not result['comparison_invalid_reasons']
            if result['comparison_valid']:
                valid += 1
            report['results'].append(result)
            report['aggregate'] = rc.summarize(report['results'])
            suite.save_report(args.output, report)
            print(json.dumps({'attempt': attempts, 'repetition': repetition,
                              'correct': result['correct'], 'artifact': result['artifact_correct'],
                              'valid': result['comparison_valid'], 'evidence': evidence['passed'],
                              'elapsed_ms': result['elapsed_ms'],
                              'calls': result['trace']['tool_calls'],
                              'errors': result['trace']['observed_tool_errors'],
                              'setup_failure': failure,
                              'invalid': result['comparison_invalid_reasons']}), flush=True)
            if failure:
                raise SystemExit('setup failure; batch aborted')
        report['complete'] = True
        suite.save_report(args.output, report)
        print(json.dumps({'attempts': attempts, 'valid': valid, 'complete': True}))


if __name__ == '__main__':
    main()
