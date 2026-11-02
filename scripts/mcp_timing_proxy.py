#!/usr/bin/env python3
"""Transparent line-delimited MCP relay recording content-free wire timings.

Durations include relay/transport overhead, server queueing and execution. Gaps
between requests include host/model work and any non-MCP tools. Neither is a
measurement of model inference or internal tool execution by itself.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def compact_read_response(data):
    """Remove only redundant equal nested paths; retain the outer path and all results."""
    try:
        message = json.loads(data)
        content = message['result']['content']
        if len(content) != 1 or content[0]['type'] != 'text':
            return data
        body = json.loads(content[0]['text'])
        if body.get('ok') is not True:
            return data
        changed = False
        for item in body['result']['files']:
            read = item.get('read')
            if (isinstance(read, dict) and isinstance(item.get('path'), str)
                    and read.get('path') == item['path']):
                del read['path']
                changed = True
        if not changed:
            return data
        content[0]['text'] = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        return (json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    except (ValueError, TypeError, KeyError, AttributeError):
        return data


def relay(binary, log, arguments, compact=False):
    child = subprocess.Popen([binary, *arguments], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    start = time.monotonic_ns()
    pending = {}
    sequence = 0
    lock = threading.Lock()
    # Only numeric timing and protocol method/tool names are recorded. No args,
    # output, IDs, paths or session metadata go into the event log.
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        def record(event):
            with lock:
                output.write(json.dumps(event, separators=(',', ':')) + '\n')
                output.flush()
        def incoming():
            nonlocal sequence
            try:
                for data in sys.stdin.buffer:
                    now = time.monotonic_ns()
                    try:
                        value = json.loads(data)
                        method = value.get('method')
                        ident = value.get('id')
                        tool = value.get('params', {}).get('name') if method == 'tools/call' else None
                        with lock:
                            sequence += 1
                            serial = sequence
                            if ident is not None: pending[ident] = (now, serial, tool)
                        event = {'direction':'request','ms':(now-start)/1e6,'bytes':len(data),
                                 'sequence':serial, 'method':method,'tool':tool}
                        record(event)
                    except (ValueError, TypeError, AttributeError):
                        record({'direction':'request_invalid','ms':(now-start)/1e6,'bytes':len(data)})
                    child.stdin.write(data); child.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try: child.stdin.close()
                except OSError: pass
        reader = threading.Thread(target=incoming, daemon=True)
        reader.start()
        def server_stderr():
            for data in child.stderr:
                if data.startswith(b'FFSAKE_BENCH_TIMING '):
                    try:
                        item = json.loads(data.removeprefix(b'FFSAKE_BENCH_TIMING '))
                        record({'direction': 'server_execution', 'ms': (time.monotonic_ns()-start)/1e6,
                                'tool': item['tool'], 'execution_ms': item['execution_ms']})
                        continue
                    except (ValueError, TypeError, KeyError):
                        pass
                sys.stderr.buffer.write(data)
                sys.stderr.buffer.flush()
        stderr_reader = threading.Thread(target=server_stderr, daemon=True)
        stderr_reader.start()
        try:
            for data in child.stdout:
                now = time.monotonic_ns()
                try:
                    value = json.loads(data)
                    ident = value.get('id')
                    with lock: before = pending.pop(ident, None)
                    original_bytes = len(data)
                    if compact and before and before[2] == 'ffsake_read_files':
                        data = compact_read_response(data)
                    event = {'direction':'response','ms':(now-start)/1e6,'bytes':len(data),
                             'original_bytes':original_bytes,
                             'sequence':before[1] if before is not None else None,
                             'roundtrip_ms':(now-before[0])/1e6 if before is not None else None,
                             'protocol_error':'error' in value}
                    record(event)
                except (ValueError, TypeError, AttributeError):
                    record({'direction':'response_invalid','ms':(now-start)/1e6,'bytes':len(data)})
                sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
        except BrokenPipeError:
            pass
        finally:
            if child.poll() is None:
                child.terminate()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
            stderr_reader.join(timeout=5)
    return child.returncode


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', required=True)
    p.add_argument('--log', required=True, type=Path)
    p.add_argument('--compact', action='store_true', help='diagnostic: omit duplicate nested read paths')
    p.add_argument('arguments', nargs=argparse.REMAINDER)
    args = p.parse_args()
    return relay(args.binary, args.log, args.arguments, args.compact)

if __name__ == '__main__':
    raise SystemExit(main())
