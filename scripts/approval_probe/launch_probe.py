"""Launch a real Claude approval probe; requires FFSAKE_PROBE_OUTPUT."""
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

out = Path(os.environ['FFSAKE_PROBE_OUTPUT']).resolve()
out.mkdir(parents=True, exist_ok=True)
statefile = out / 'probe-environment.json'
if statefile.exists():
    state = json.loads(statefile.read_text())
else:
    root = Path(tempfile.mkdtemp(prefix='ffsake-approval-probe-')).resolve()
    state = {'root': str(root)}
    statefile.write_text(json.dumps(state))
config = {'mcpServers': {'approvalprobe': {
    'command': sys.executable, 'args': [str(Path(__file__).with_name('probe_server.py').resolve())],
    'env': {'FFSAKE_PROBE_OUTPUT': str(out)},
}}}
(out / 'probe-mcp.json').write_text(json.dumps(config))
name = 'mcp__approvalprobe__ffsake_access_probe'
hook = shlex.join([sys.executable, str(Path(__file__).with_name('probe_hook.py').resolve())])
settings = {'permissions': {'allow': [name]}, 'hooks': {
    event: [{'matcher': name, 'hooks': [{'type': 'command', 'command': hook}]}]
    for event in ['PreToolUse', 'PermissionRequest']
}}
(out / 'probe-settings.json').write_text(json.dumps(settings))
args = ['claude', '--restricted', '--setting-sources', '', '--strict-mcp-config',
        '--mcp-config', str(out / 'probe-mcp.json'), '--settings', str(out / 'probe-settings.json'),
        '--tools', '', '--allowedTools', name, '--permission-mode', 'auto', '--no-chrome']
if '--print' in sys.argv:
    args += ['-p', 'Call ffsake_access_probe once with path /tmp/synthetic-approved.txt, kind file, '
             'mode read_only. Report the actual result. Do not retry or use another tool.',
             '--output-format', 'stream-json', '--verbose', '--max-turns', '3',
             '--max-budget-usd', '1', '--permission-prompts', 'none']
filename = 'noninteractive-command.json' if '--print' in sys.argv else 'interactive-command.json'
(out / filename).write_text(json.dumps(args))
os.chdir(state['root'])
os.execvp(args[0], args)
