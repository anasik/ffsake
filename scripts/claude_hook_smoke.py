#!/usr/bin/env python3
"""Model-free checks for the Claude PreToolUse read policy."""
import json
from pathlib import Path
import subprocess
import sys

HOOK = Path(__file__).resolve().with_name("claude_pre_tool_policy.py")


def decision(event):
    run = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)["hookSpecificOutput"]["permissionDecision"]


def main():
    assert decision({"tool_name": "Read", "tool_input": {"file_path": "a.py"}}) == "deny"
    assert decision({"tool_name": "Glob", "tool_input": {"pattern": "*"}}) == "deny"
    assert decision({"tool_name": "Grep", "tool_input": {"pattern": "x"}}) == "deny"
    assert decision({"tool_name": "Edit", "tool_input": {}}) == "allow"
    assert decision({"tool_name": "Write", "tool_input": {}}) == "allow"
    assert decision({"tool_name": "Bash", "tool_input": {"command": "cat a.py"}}) == "deny"
    assert decision({"tool_name": "Bash", "tool_input": {"command": "head -5 a.py"}}) == "deny"
    assert decision({"tool_name": "Bash", "tool_input": {"command": 'ffsake read "$PWD" a.py'}}) == "allow"
    assert decision({"tool_name": "Bash", "tool_input": {"command": "python3 -m unittest"}}) == "allow"
    print(json.dumps({
        "native_reads_denied": True,
        "native_edits_allowed": True,
        "file_shell_reads_denied": True,
        "ffsake_read_allowed": True,
        "test_commands_allowed": True,
        "model_calls": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
