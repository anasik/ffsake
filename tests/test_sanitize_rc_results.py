import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sanitize_rc_results as sanitizer


class SanitizeTests(unittest.TestCase):
    def test_timing_export_never_includes_payload_or_external_paths(self):
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as other:
            root = Path(temporary)
            path = root/'timing.jsonl'
            path.write_text(json.dumps({'direction':'request','ms':1,'bytes':10,
                'sequence':1,'method':'tools/call','tool':'ffsake_read',
                'path':'/Users/private','payload':'secret'})+'\n')
            report = {'results':[{'task':'cross','mode':'editing','mcp_timing_file':str(path),
                                  'stdout':'','stderr':''}], 'settings':{}}
            result = sanitizer.sanitize(report, root, omit_events=True)
            row = result['results'][0]
            self.assertEqual(row['mode'], 'editing')
            self.assertNotIn('events', row)
            self.assertEqual(row['mcp_timing'][0]['tool'], 'ffsake_read')
            self.assertNotIn('secret', json.dumps(result))
            self.assertNotIn('/Users/private', json.dumps(result))
            with self.assertRaises(ValueError):
                sanitizer.sanitize(report, other, omit_events=True)

    def test_debug_action_export_omits_arguments(self):
        event = {'type':'item.completed','item':{'server':'ffsake','tool':'ffsake_debug',
            'arguments':{'request':{'action':'wait','path':'/Users/private','secret':'SECRET'}}}}
        result = sanitizer.sanitize({'results':[{'task':'debug','mode':'changed',
            'stdout':json.dumps(event),'stderr':''}],'settings':{}}, omit_events=True)
        self.assertEqual(result['results'][0]['debug_actions'], ['wait'])
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('/Users/private', json.dumps(result))

    def test_published_evidence_stays_within_bounded_read_limit(self):
        root = Path(__file__).resolve().parents[1] / 'docs/agent-benchmark-runs'
        oversized = [str(path.relative_to(root)) for path in root.rglob('*.json')
                     if path.stat().st_size > 256 * 1024]
        self.assertEqual(oversized, [])

    def test_known_task_names_cover_all_fixtures(self):
        import rc_benchmark_fixtures as fixtures
        self.assertLessEqual(set(fixtures.TASKS), set(sanitizer.TASK_NAMES))

    def test_known_task_names_survive_redaction(self):
        result = sanitizer.sanitize({'results':[
            {'task':'edit_settings','mode':'baseline','stdout':'','stderr':''},
            {'task':'debug_recursive','mode':'cli','stdout':'','stderr':''},
            {'task':'private_task','mode':'cli','stdout':'','stderr':''}],
            'tasks':['edit_settings','debug_recursive','private_task'], 'settings':{}}, omit_events=True)
        self.assertEqual([row['task'] for row in result['results']],
                         ['edit_settings', 'debug_recursive', 'other'])
        self.assertEqual(result['tasks'], ['edit_settings', 'debug_recursive', 'other'])

    def test_redirect_configuration_is_exported_from_top_level_report(self):
        result = sanitizer.sanitize({'results':[], 'settings':{},
                                     'opencode_redirect_reads':True,
                                     'cli_redirect_reads':False,
                                     'permission_profiles':'private'}, omit_events=True)
        self.assertEqual(result['configuration'],
                         {'cli_redirect_reads':False, 'opencode_redirect_reads':True})

    def test_redirect_configuration_prefers_settings(self):
        result = sanitizer.sanitize({'results':[], 'settings':{'opencode_redirect_reads':True},
                                     'opencode_redirect_reads':False}, omit_events=True)
        self.assertEqual(result['configuration'], {'opencode_redirect_reads':True})

    def test_benchmark_setup_error_is_exported(self):
        run = {'task':'debug','mode':'changed','comparison_valid':False,
               'comparison_invalid_reasons':['benchmark_setup_error','private_reason'],
               'stdout':'','stderr':''}
        result = sanitizer.sanitize({'results':[run],'settings':{}}, omit_events=True)
        self.assertEqual(result['results'][0]['comparison_invalid_reasons'],
                         ['benchmark_setup_error'])

    def test_excludes_arbitrary_private_content_and_keeps_numeric_usage(self):
        secret = 'person@example.com /Users/person/private sk-ant-not-a-real-secret'
        event = {'type':'item.completed','item':{'type':'command_execution','command':secret,
                                                  'aggregated_output':secret,'status':'completed'}}
        run = {'task':'cross','mode':'baseline','repetition':1,'correct':True,
               'artifact_correct':True,'comparison_valid':True,'timed_out':False,
               'elapsed_ms':20,'exit_code':0,'provenance':{'prompt_sha256':secret},
               'trace':{'tool_calls':1,'input_tokens':12,'total_input_tokens':12,
                        'tool_names':['command_execution',secret]},
               'workflow_evidence':{'passed':True},
               'verification':{'file_audit_complete':True,'unexpected_file_changes':[],
                               'verification_file_changes':[],'stdout':secret},
               'stdout':json.dumps(event),'stderr':secret}
        result = sanitizer.sanitize({'results':[run], 'agents':['codex'], 'tasks':['cross'],
                                     'settings':{'machine':'arm64','hardware':{'cpu':secret}},
                                     'complete':True})
        encoded = json.dumps(result)
        self.assertNotIn('person@example.com', encoded)
        self.assertNotIn('/Users/person', encoded)
        self.assertNotIn('sk-ant-not-a-real-secret', encoded)
        self.assertEqual(result['results'][0]['trace']['total_input_tokens'], 12)
        self.assertEqual(result['results'][0]['trace']['tool_names'], ['command_execution','other'])
        self.assertEqual(result['results'][0]['events'][0]['item']['type'], 'command_execution')


if __name__ == '__main__': unittest.main()
