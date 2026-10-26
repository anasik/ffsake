"""Replay the harmless probe as a client that does not implement approval UI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

source = Path(sys.argv[1])
events = [json.loads(line) for line in source.read_text().splitlines()]
initialize = next(e['message'] for e in events if e['message'].get('method') == 'initialize')
call = next(e['message'] for e in events if e['message'].get('method') == 'tools/call')
with tempfile.TemporaryDirectory(prefix='ffsake-unapproved-client-') as directory:
    env = dict(os.environ, FFSAKE_PROBE_OUTPUT=directory)
    messages = [initialize, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, call]
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name('probe_server.py'))],
        input=''.join(json.dumps(message) + '\n' for message in messages),
        capture_output=True, text=True, env=env, check=True, timeout=10,
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses[1]['result']['tools'][0]['_meta']['anthropic/requiresUserInteraction'] is True
    assert 'result' in responses[-1]
    assert len((Path(directory) / 'probe-executions.jsonl').read_text().splitlines()) == 1
    print(json.dumps({'unapproved_client_dispatched': True, 'requests': messages,
                      'responses': responses}, indent=2))
