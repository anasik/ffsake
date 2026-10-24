#!/usr/bin/env python3
"""Prove the opt-in layout benchmark is achievable through real core MCP calls."""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile

import agent_benchmark_suite as suite


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else suite.FFSAKE_MCP).resolve()
    with tempfile.TemporaryDirectory(prefix="ffsake-layout-smoke-") as temporary:
        root = Path(temporary)
        state = suite.layout_fixture(root)
        proc = subprocess.Popen([str(binary), str(root), "--profile", "core"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        calls = []

        def call(name, arguments):
            calls.append(name)
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": len(calls), "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 30)[0], name
            response = json.loads(proc.stdout.readline())
            value = json.loads(response["result"]["content"][0]["text"])
            assert response["id"] == len(calls) and value["ok"], value
            return value["result"]

        try:
            preview = call("ffsake_preview_files", {"operations": [
                {"op": "move_directory", "from": "service_old", "to": "service_next"},
                {"op": "copy_directory", "from": "templates", "to": "templates_snapshot"},
                {"op": "delete_directory", "path": "retired"},
                {"op": "create_directory", "path": "reports"},
                {"op": "edit", "path": "README.md", "old_text": "python3 -B -m service_old",
                 "new_text": "python3 -B -m service_next", "expected_matches": 1}]})
            assert suite.fixture_files(root) == state["original"]
            transaction = call("ffsake_apply", {"handle": preview["handle"]})["transaction"]
            before = suite.fixture_files(root)
            ok, details = suite.layout_verify(root, state)
            assert ok, details
            assert suite.fixture_files(root) == before
            call("ffsake_undo", {"transaction": transaction})
            assert suite.fixture_files(root) == state["original"]
            directories = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_dir()
                           and not {".git", ".ffsake"}.intersection(path.relative_to(root).parts)}
            assert directories == state["directories"]
            call("ffsake_redo", {"transaction": transaction})
            ok, details = suite.layout_verify(root, state)
            assert ok, details
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            assert proc.returncode == 0, proc.stderr.read()
        print(json.dumps({"ok": True, "model_calls": 0, "task": "layout", "profile": "core",
                          "verification": details, "byte_and_directory_history": True, "calls": calls}))


if __name__ == "__main__":
    main()
