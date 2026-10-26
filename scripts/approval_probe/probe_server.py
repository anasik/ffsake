"""Harmless MCP approval probe. Never reads external files or creates grants."""
import json
import os
from pathlib import Path
import sys
import time

out = Path(os.environ['FFSAKE_PROBE_OUTPUT']).resolve()
out.mkdir(parents=True, exist_ok=True)


def record(filename, value):
    with (out / filename).open('a') as stream:
        stream.write(json.dumps(value) + '\n')


for line in sys.stdin:
    message = json.loads(line)
    record('probe-protocol.jsonl', {'direction': 'host', 'message': message, 'time': time.time()})
    if 'id' not in message:
        continue
    method = message.get('method')
    result = {}
    if method == 'initialize':
        result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'approvalprobe', 'version': '1.0'}}
    elif method == 'tools/list':
        result = {'tools': [{
            'name': 'ffsake_access_probe',
            'description': ('TEST ONLY: Request human approval for a synthetic external file scope. '
                            'Records execution but does not read files or create grants. '
                            'Approval must be shown to a person.'),
            '_meta': {'anthropic/requiresUserInteraction': True},
            'inputSchema': {
                'type': 'object', 'properties': {
                    'path': {'type': 'string'}, 'kind': {'enum': ['file', 'directory']},
                    'mode': {'enum': ['read_only', 'read_write']}},
                'required': ['path', 'kind', 'mode'], 'additionalProperties': False},
        }]}
    elif method == 'tools/call':
        record('probe-executions.jsonl', {'time': time.time(), 'request': message})
        result = {'content': [{'type': 'text', 'text':
                  'Probe executed after host dispatch. No file was accessed and no grant was created.'}]}
    elif method != 'ping':
        print(json.dumps({'jsonrpc': '2.0', 'id': message['id'],
                          'error': {'code': -32601, 'message': 'unsupported'}}), flush=True)
        continue
    response = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
    record('probe-protocol.jsonl', {'direction': 'server', 'message': response, 'time': time.time()})
    print(json.dumps(response), flush=True)
