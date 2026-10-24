#!/usr/bin/env python3
"""Verify catalog profiles, disabled dispatch and ordinary guarded editing."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile

from agent_benchmark_suite import FFSAKE_CORE_TOOLS, FFSAKE_TOOLS


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    with tempfile.TemporaryDirectory(prefix="ffsake-profile-") as temporary:
        base = Path(temporary)
        root = base / "workspace"
        root.mkdir()
        source = 'pub struct Account;\r\nfn main() { let _ = "🚀"; }\r\n'
        path = root / "main.rs"
        path.write_bytes(source.encode())
        path.chmod(0o755)
        sentinel = base / "ANALYZER_RAN"
        server = base / "rust-analyzer"
        server.write_text(f"#!/usr/bin/env python3\nfrom pathlib import Path\nPath({str(sentinel)!r}).write_text('ran')\n")
        server.chmod(0o755)
        env = {**os.environ, "PATH": str(base) + os.pathsep + os.environ.get("PATH", "")}

        def catalog(arguments):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            run = subprocess.run([str(binary), str(root), *arguments], input=json.dumps(request) + "\n",
                                 capture_output=True, text=True, env=env, timeout=10)
            assert run.returncode == 0, run.stderr
            return json.loads(run.stdout)["result"]["tools"]

        full = catalog([])
        assert full == catalog(["--profile", "all"])
        core = catalog(["--profile", "core"])
        assert {tool["name"] for tool in full} == set(FFSAKE_TOOLS)
        assert {tool["name"] for tool in core} == set(FFSAKE_CORE_TOOLS)
        assert core == [tool for tool in full if tool["name"] in FFSAKE_CORE_TOOLS]
        assert path.read_bytes() == source.encode() and not sentinel.exists()
        for arguments in (["--profile"], ["--profile", "unknown"], ["--profile", "core", "extra"], ["--other", "core"]):
            rejected = subprocess.run([str(binary), str(base / "missing-workspace"), *arguments],
                                      capture_output=True, text=True, timeout=5)
            assert rejected.returncode == 2 and "usage:" in rejected.stderr, rejected
        proc = subprocess.Popen([str(binary), str(root), "--profile", "core"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        request_id = 0

        def call(name, arguments, ok=True):
            nonlocal request_id
            request_id += 1
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 10)[0], name
            response = json.loads(proc.stdout.readline())
            assert response["id"] == request_id, response
            value = json.loads(response["result"]["content"][0]["text"])
            assert value["ok"] == ok and response["result"]["isError"] != ok, value
            return value.get("result", value)

        try:
            for name in set(FFSAKE_TOOLS) - set(FFSAKE_CORE_TOOLS):
                result = call(name, {}, ok=False)
                assert "disabled by core profile" in result["error"], result
            rejected = call("ffsake_rust_references", {"files": ["main.rs"], "crate_root": "main.rs",
                "path": "main.rs", "position": {"line": 1, "column": 11}, "edition": "2024", "limit": 10}, ok=False)
            assert "disabled by core profile" in rejected["error"] and not sentinel.exists()
            assert "unknown tool" in call("unknown", {}, ok=False)["error"]
            # A supplied op cannot bypass the enabled tool's dispatch mapping.
            call("ffsake_read", {"path": "main.rs", "op": "rust_references"})
            preview = call("ffsake_preview_edit", {"path": "main.rs", "old_text": "Account",
                "new_text": "Customer", "expected_matches": 1})
            assert path.read_bytes() == source.encode()
            transaction = call("ffsake_apply", {"handle": preview["handle"]})["transaction"]
            assert path.read_bytes() == source.replace("Account", "Customer").encode()
            call("ffsake_apply", {"handle": preview["handle"]}, ok=False)
            call("ffsake_undo", {"transaction": transaction})
            assert path.read_bytes() == source.encode()
            call("ffsake_redo", {"transaction": transaction})
            assert path.read_bytes() == source.replace("Account", "Customer").encode()
            call("ffsake_undo", {"transaction": transaction})
            assert path.read_bytes() == source.encode() and path.stat().st_mode & 0o777 == 0o755
            assert not sentinel.exists()
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()
        assert proc.returncode == 0
    size = lambda items: len(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode())
    print(json.dumps({"all_tools": len(full), "all_catalog_bytes": size(full), "core_tools": len(core),
                      "core_catalog_bytes": size(core), "disabled_dispatch_rejected": True,
                      "byte_history_passed": True, "agent_speed_measured": False}, indent=2))


if __name__ == "__main__":
    main()
