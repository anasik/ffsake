"""Controlled standard-DAP peer for lifecycle/failure tests; not a debugger."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

mode, directory = sys.argv[1], Path(sys.argv[2])
seq = 0
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
(directory / 'pids.json').write_text(json.dumps([os.getpid(), child.pid]))


def send(value):
    global seq
    seq += 1
    value['seq'] = seq
    data = json.dumps(value).encode()
    sys.stdout.buffer.write(f'Content-Length: {len(data)}\r\n\r\n'.encode() + data)
    sys.stdout.buffer.flush()


def event(name, **body):
    send({'type': 'event', 'event': name, 'body': body})


if mode == 'malformed':
    sys.stdout.buffer.write(b'Content-Length: 999999999\r\n\r\n'); sys.stdout.buffer.flush()
    time.sleep(120)
if mode == 'no_read':
    time.sleep(120)
while True:
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if line == b'\r\n':
            break
        if line.lower().startswith(b'content-length:'):
            length = int(line.split(b':')[1])
    message = json.loads(sys.stdin.buffer.read(length))
    if message['type'] == 'response':
        assert message['success'] is False and message['command'] == 'runInTerminal'
        (directory / 'reverse-rejected').write_text('yes')
        continue
    command = message['command']
    (directory / 'calls.jsonl').open('a').write(json.dumps(message) + '\n')
    args = message.get('arguments', {})
    if command == 'disconnect':
        # Ignore to prove process-group cleanup independently of adapter cooperation.
        continue
    if command == 'initialize':
        if mode == 'crash':
            sys.exit(7)
        if mode == 'timeout':
            continue
        body = {'supportsConfigurationDoneRequest': mode != 'unsupported', 'supportTerminateDebuggee': mode == 'attach'}
        if mode == 'bad_correlation':
            command = 'WRONG_COMMAND'
    elif command in ('launch', 'attach'):
        if mode == 'reverse':
            send({'type': 'request', 'command': 'runInTerminal', 'arguments': {'args': ['DO_NOT_EXECUTE']}})
        event('initialized')
        body = {}
    elif command == 'configurationDone':
        if mode != 'running':
            event('stopped', reason='breakpoint', threadId=1, allThreadsStopped=True)
        body = {}
    elif command == 'setBreakpoints':
        body = {'breakpoints': [{'verified': True, 'line': l['line']} for l in args['breakpoints']]}
    elif command == 'stackTrace':
        body = {'stackFrames': [{'id': 11, 'name': 'test', 'line': 1, 'column': 1,
                                'source': {'path': str(directory / 'outside.txt'), 'sourceReference': 7}}]}
    elif command == 'scopes':
        body = {'scopes': [{'name': 'Locals', 'variablesReference': 12, 'expensive': False}]}
    elif command == 'variables':
        if mode == 'resume_during_inspect':
            event('continued', threadId=1, allThreadsContinued=True)
        if mode == 'invalidate_during_inspect':
            event('invalidated', areas=['variables'])
        body = {'variables': [{'name': str(i), 'value': 'v' * 3000, 'variablesReference': 12} for i in range(110)]}
    elif command == 'source':
        body = {'content': 'EXTERNAL_SOURCE_SENTINEL'}
    elif command in ('next', 'stepIn', 'stepOut', 'continue'):
        event('continued', threadId=1, allThreadsContinued=True)
        event('stopped', reason='step', threadId=1, allThreadsStopped=True)
        body = {}
    elif command == 'pause':
        send({'type': 'response', 'request_seq': message['seq'], 'command': command,
              'success': False, 'message': 'pause unsupported by fixture'})
        continue
    else:
        body = {}
    if mode == 'output' and command == 'configurationDone':
        for _ in range(30):
            event('output', output='x' * 4096)
    send({'type': 'response', 'request_seq': message['seq'], 'command': command,
          'success': True, 'body': body})
