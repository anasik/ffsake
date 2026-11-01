import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sanitize_rc_results as sanitizer


class SanitizeTests(unittest.TestCase):
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
