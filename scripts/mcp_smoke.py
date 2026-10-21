#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile

binary = str(Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve())

def send(proc, message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise AssertionError(proc.stderr.read())
    return json.loads(line)

with tempfile.TemporaryDirectory(prefix="ffsake-mcp-") as temp:
    root = Path(temp)
    (root / "a.txt").write_text("needle needle\n")

    proc = subprocess.Popen(
        [binary, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        init = send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
        })
        assert init["result"]["serverInfo"]["name"] == "ffsake"

        listed = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert {"ffsake_search", "ffsake_preview_matches", "ffsake_apply", "ffsake_undo"} <= names

        found = send(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "ffsake_search", "arguments": {"pattern": "needle", "details": True}},
        })
        found_payload = json.loads(found["result"]["content"][0]["text"])
        assert found_payload["ok"]
        assert found_payload["result"]["matches"] == 2
        handle = found_payload["result"]["handle"]

        preview = send(proc, {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "ffsake_preview_matches",
                "arguments": {"handle": handle, "replacement": "world", "expected_matches": 2},
            },
        })
        preview_payload = json.loads(preview["result"]["content"][0]["text"])
        assert preview_payload["ok"]
        preview_handle = preview_payload["result"]["handle"]
        assert (root / "a.txt").read_text() == "needle needle\n"

        applied = send(proc, {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "ffsake_apply", "arguments": {"handle": preview_handle}},
        })
        applied_payload = json.loads(applied["result"]["content"][0]["text"])
        assert applied_payload["ok"]
        transaction = applied_payload["result"]["transaction"]
        assert (root / "a.txt").read_text() == "world world\n"

        undone = send(proc, {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "ffsake_undo", "arguments": {"transaction": transaction}},
        })
        undone_payload = json.loads(undone["result"]["content"][0]["text"])
        assert undone_payload["ok"]
        assert (root / "a.txt").read_text() == "needle needle\n"
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0, proc.stderr.read()

print("MCP smoke: initialize/tools/search/preview/apply/undo PASS")
