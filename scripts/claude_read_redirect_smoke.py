#!/usr/bin/env python3
"""Model-free checks for the Read->ffsake redirect hook."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HOOK = Path(__file__).resolve().with_name("claude_read_redirect_hook.py")
BINARY = Path(__file__).resolve().parents[1] / "target/debug/ffsake"


def run(event, cwd):
    env = {**os.environ, "FFSAKE_BIN": str(BINARY)}
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                            capture_output=True, text=True, timeout=30, cwd=cwd, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["hookSpecificOutput"]


def main():
    assert BINARY.is_file(), BINARY
    with tempfile.TemporaryDirectory(prefix="ffsake-redirect-") as td:
        root = Path(td)
        (root / "a.txt").write_text("one\ntwo\nthree\n")
        allowed = run({"tool_name": "Read", "tool_input": {"file_path": "a.txt"}, "cwd": str(root)}, root)
        assert allowed["permissionDecision"] == "allow", allowed
        rewritten = Path(allowed["updatedInput"]["file_path"])
        assert rewritten.is_file() and rewritten.parent.name == "hook-reads"
        assert rewritten.read_text() == "one\ntwo\nthree\n", rewritten.read_text()
        denied = run({"tool_name": "Bash", "tool_input": {"command": "cat a.txt"}, "cwd": str(root)}, root)
        assert denied["permissionDecision"] == "deny", denied
        cached = run({"tool_name": "Read", "tool_input": {"file_path": str(rewritten)}, "cwd": str(root)}, root)
        assert cached["permissionDecision"] == "allow" and "updatedInput" not in cached, cached
        outside = root.parent / "outside.txt"
        outside.write_text("secret")
        blocked = run({"tool_name": "Read", "tool_input": {"file_path": str(outside)}, "cwd": str(root)}, root)
        assert blocked["permissionDecision"] == "deny", blocked
        print(json.dumps({
            "read_rewritten_to_ffsake": True,
            "cached_read_allowed": True,
            "shell_file_reads_denied": True,
            "outside_read_denied": True,
            "model_calls": 0,
        }, indent=2))


if __name__ == "__main__":
    main()
