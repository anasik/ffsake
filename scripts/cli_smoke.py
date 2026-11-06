#!/usr/bin/env python3
"""Model-free CLI shim checks: guarded reads, preview/apply, history, cleanup."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "tests/fixtures/dap_adapter.py"


def call_full(binary, root, *args, ok=True):
    run = subprocess.run([str(binary), str(root), *args], cwd=root,
                         capture_output=True, text=True, timeout=30)
    assert run.stdout, run.stderr
    value = json.loads(run.stdout)
    assert value["ok"] is ok, (args, value, run.stderr)
    return value


def call(binary, root, *args, ok=True):
    value = call_full(binary, root, *args, ok=ok)
    return value["result"] if ok else value["error"]


def main():
    binary = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="ffsake-cli-") as td:
        base = Path(td).resolve()
        root = base / "root"
        root.mkdir()
        (root / "a.txt").write_text("one\ntwo\n")
        (root / "b.txt").write_text("alpha beta\n")

        read = call(binary, root, "--request",
                    json.dumps({"op": "read_files", "files": [{"path": "a.txt"}, {"path": "b.txt"}]}))
        texts = {item["path"]: item["read"]["text"] for item in read["files"]}
        assert texts == {"a.txt": "one\ntwo", "b.txt": "alpha beta"}, texts

        operations = [
            {"op": "edit", "path": "a.txt", "old_text": "one", "new_text": "ONE", "expected_matches": 1},
            {"op": "edit", "path": "b.txt", "old_text": "alpha", "new_text": "ALPHA", "expected_matches": 1},
        ]
        preview = call_full(binary, root, "--request",
                            json.dumps({"op": "preview_files", "operations": operations}))
        token = preview["token"]
        assert preview["result"]["preview"]["changed_files"] == 2
        assert (root / "a.txt").read_text() == "one\ntwo\n"
        token_path = root / ".ffsake/cli" / token
        assert token_path.is_file()
        if os.name == "posix":
            assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

        applied = call(binary, root, "--apply", token)
        assert (root / "a.txt").read_text() == "ONE\ntwo\n"
        assert (root / "b.txt").read_text() == "ALPHA beta\n"
        assert not token_path.exists()
        transaction = applied["transaction"]
        call(binary, root, "--apply", token, ok=False)

        undone = call(binary, root, "--request", json.dumps({"op": "undo", "transaction": transaction}))
        assert (root / "a.txt").read_text() == "one\ntwo\n"
        call(binary, root, "--request", json.dumps({"op": "redo", "transaction": transaction}))
        assert (root / "a.txt").read_text() == "ONE\ntwo\n"
        call(binary, root, "--request", json.dumps({"op": "undo", "transaction": transaction}))
        assert (root / "a.txt").read_text() == "one\ntwo\n"

        stale = call_full(binary, root, "--request",
                          json.dumps({"op": "preview_files", "operations": operations[:1]}))
        (root / "a.txt").write_text("host change\n")
        error = call(binary, root, "--apply", stale["token"], ok=False)
        assert "expected 1 matches, found 0" in error, error
        assert (root / "a.txt").read_text() == "host change\n"

        outside = base / "outside.txt"
        outside.write_text("PRIVATE_SENTINEL")
        error = call(binary, root, "--request",
                     json.dumps({"op": "read", "path": str(outside)}), ok=False)
        assert "PRIVATE_SENTINEL" not in error and "authorization" in error, error
        error = call(binary, root, "--apply", "../cli/escape", ok=False)
        assert "invalid token name" in error, error

        # One-shot debug probe: no MCP catalog, two stops, owned cleanup.
        policy = base / "debug.json"
        policy.write_text(json.dumps({"version": 1, "configurations": {"fake": {
            "argv": [sys.executable, str(ADAPTER), "normal", str(base)],
            "launch": {}, "timeout_seconds": 1, "session_seconds": 30}}}))
        probe = call(binary, root, "--debug", str(policy), "--request", json.dumps({
            "op": "debug_probe", "configuration": "fake",
            "breakpoints": [{"path": "a.txt", "lines": 1}], "stops": 2}))
        assert probe["status"] == "stopped" and probe["cleaned"] is True, probe
        assert probe.get("breakpoint_stops_collected") == 2, probe
        assert probe.get("next_inspection") and probe.get("stop_inspection_2"), probe
        compact = subprocess.run([str(binary), "probe", str(root), "--debug", str(policy),
                                  "--config", "fake", "--breakpoint", "a.txt:1", "--stops", "2"],
                                 cwd=root, capture_output=True, text=True, timeout=30)
        compact_value = json.loads(compact.stdout)
        assert compact_value["ok"] and compact_value["cleaned"] is True, compact_value
        assert len(compact_value["stops"]) >= 2, compact_value
        calls = [json.loads(line) for line in (base / "calls.jsonl").read_text().splitlines()]
        assert [c["command"] for c in calls].count("continue") >= 1, calls
        pids = json.loads((base / "pids.json").read_text())
        deadline = time.time() + 5
        alive = list(pids)
        while alive and time.time() < deadline:
            alive = [pid for pid in alive if process_alive(pid)]
            if alive:
                time.sleep(0.05)
        assert not alive, alive

        # Native-like subcommands: line-numbered read and reviewed edit/apply.
        (root / "sub.txt").write_text("before\n")
        read_cmd = subprocess.run([str(binary), "read", str(root), "sub.txt"],
                                  cwd=root, capture_output=True, text=True, timeout=30)
        assert "1: before" in read_cmd.stdout, read_cmd.stdout
        ranged = subprocess.run([str(binary), "read", str(root), "sub.txt:1-1"],
                                cwd=root, capture_output=True, text=True, timeout=30)
        assert "1: before" in ranged.stdout and "after" not in ranged.stdout, ranged.stdout
        preview_cmd = subprocess.run([str(binary), "edit", str(root), "sub.txt",
                                      "--old", "before", "--new", "after"],
                                     cwd=root, capture_output=True, text=True, timeout=30)
        preview_value = json.loads(preview_cmd.stdout)
        assert preview_value["ok"] and preview_value["token"], preview_value
        assert (root / "sub.txt").read_text() == "before\n"
        apply_cmd = subprocess.run([str(binary), "apply", str(root), preview_value["token"]],
                                   cwd=root, capture_output=True, text=True, timeout=30)
        assert json.loads(apply_cmd.stdout)["ok"], apply_cmd.stdout
        assert (root / "sub.txt").read_text() == "after\n"

        # Persistent JSONL mode keeps handles in one process, matching the old
        # resident CLI while the one-shot mode above bridges separate Bash turns.
        (root / "serve.txt").write_text("before\n")
        process = subprocess.Popen([str(binary), "serve", str(root)], cwd=root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            lines = [
                json.dumps({"op": "read", "path": "serve.txt"}),
                json.dumps({"op": "preview_files", "operations": [
                    {"op": "edit", "path": "serve.txt", "old_text": "before",
                     "new_text": "after", "expected_matches": 1}]}),
                json.dumps({"op": "apply", "handle": 1}),
                json.dumps({"op": "read", "path": "serve.txt"}),
            ]
            responses = []
            for line in lines:
                process.stdin.write(line + "\n")
                process.stdin.flush()
                responses.append(json.loads(process.stdout.readline()))
            process.stdin.close()
            process.wait(timeout=10)
            assert len(responses) == 4, responses
            assert responses[1]["result"]["handle"] == 1
            assert responses[2]["ok"] and responses[2]["result"]["transaction"]
            assert responses[3]["result"]["text"] == "after"
            assert (root / "serve.txt").read_text() == "after\n"
        finally:
            if process.poll() is None:
                process.kill()
            process.stdout.close()
            process.stderr.close()

        print(json.dumps({
            "guarded_read": True, "preview_without_write": True,
            "token_mode": True, "apply_once": True, "stale_rejection": True,
            "undo_redo": True, "authorization_denied": True,
            "token_traversal_rejected": True, "debug_probe_two_stops": True,
            "owned_cleanup": True, "serve_mode_handles": True,
            "mcp_catalog_bytes": 0, "model_calls": 0,
        }, indent=2))


def process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    main()
