"""Protocol relay must preserve responses and keep sensitive payloads out of logs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from mcp_timing_proxy import compact_read_response

class TimingProxyTests(unittest.TestCase):
    def test_compaction_removes_only_equal_duplicate_paths(self):
        body = {'ok': True, 'result': {'files': [
            {'path': 'a', 'status': 'ok', 'read': {'path': 'a', 'text': 'hello'}},
            {'path': 'b', 'status': 'ok', 'read': {'path': 'different', 'text': 'world'}},
            {'path': 'c', 'status': 'denied'}]}}
        response = {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': json.dumps(body)}]}}
        result = json.loads(compact_read_response(json.dumps(response).encode()))
        files = json.loads(result['result']['content'][0]['text'])['result']['files']
        self.assertEqual(files[0], {'path':'a', 'status':'ok', 'read':{'text':'hello'}})
        self.assertEqual(files[1:], body['result']['files'][1:])
        error = b'{"jsonrpc":"2.0","id":1,"error":{"code":1}}'
        self.assertEqual(compact_read_response(error), error)

    def test_roundtrip_and_redaction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            server = root / 'server'
            server.write_text('#!/usr/bin/env python3\nimport json,sys\nfor line in sys.stdin:\n v=json.loads(line); print(json.dumps({"jsonrpc":"2.0","id":v["id"],"result":{"text":"private-response"}}),flush=True)\n')
            server.chmod(0o755)
            log = root / 'measurements.jsonl'
            request = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'ffsake_read','arguments':{'path':'private-filename'}}}
            run = subprocess.run(['python3', str(ROOT/'scripts/mcp_timing_proxy.py'), '--binary', str(server), '--log', str(log)], input=json.dumps(request)+'\n', capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)['result']['text'], 'private-response')
            data = log.read_text()
            self.assertNotIn('private-', data)
            events = [json.loads(line) for line in data.splitlines()]
            self.assertEqual([x['direction'] for x in events], ['request','response'])
            self.assertEqual(events[0]['sequence'], events[1]['sequence'])
            self.assertEqual(events[0]['tool'], 'ffsake_read')
            self.assertGreaterEqual(events[1]['roundtrip_ms'], 0)
            self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)

if __name__ == '__main__': unittest.main()
