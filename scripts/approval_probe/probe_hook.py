"""Test-only hooks attempting automatic approval of the harmless probe."""
import json
import os
from pathlib import Path
import sys

out = Path(os.environ['FFSAKE_PROBE_OUTPUT']).resolve()
out.mkdir(parents=True, exist_ok=True)
event = json.load(sys.stdin)
with (out / 'hook-events.jsonl').open('a') as stream:
    stream.write(json.dumps(event) + '\n')
name = event['hook_event_name']
decision = ({'permissionDecision': 'allow'} if name == 'PreToolUse'
            else {'decision': {'behavior': 'allow'}})
print(json.dumps({'hookSpecificOutput': {'hookEventName': name, **decision}}))
