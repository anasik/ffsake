#!/usr/bin/env python3
"""Run model-free RC safety checks and retain only non-sensitive outcomes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('output already exists')
    binary = args.binary.resolve(strict=True)
    scripts = ('access_smoke.py', 'mcp_smoke.py', 'git_write_smoke.py',
               'project_tasks_smoke.py', 'task_output_smoke.py')
    rows = []
    for script in scripts:
        result = subprocess.run([sys.executable, str(Path(__file__).parent / script), str(binary)],
                                capture_output=True, text=True, timeout=180)
        try:
            parsed = json.loads(result.stdout)
            outcomes = {k:v for k,v in parsed.items() if isinstance(v, bool)} if isinstance(parsed, dict) else {}
        except ValueError:
            outcomes = {}
        rows.append({'script':script, 'exit_code':result.returncode, 'outcomes':outcomes,
                     'stdout_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),
                     'stderr_sha256':hashlib.sha256(result.stderr.encode()).hexdigest()})
        print(script, result.returncode, flush=True)
    evidence = {'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(), 'checks':rows,
                'all_passed':all(row['exit_code'] == 0 for row in rows), 'model_calls':0,
                'scope':'FFSAKE operations and owned process cleanup; not an OS execution sandbox'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + '\n')
    if not evidence['all_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
