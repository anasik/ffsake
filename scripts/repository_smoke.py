#!/usr/bin/env python3
"""Exercise MCP reads, mixed transactions and history on a disposable HEAD snapshot.

No model calls or performance comparison. The user's checkout is never mutated.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tarfile
import tempfile


def main():
    repo = Path(__file__).resolve().parents[1]
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else repo / "target/debug/ffsake-mcp").resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    archive = subprocess.check_output(["git", "archive", revision], cwd=repo)
    with tempfile.TemporaryDirectory(prefix="ffsake-repository-smoke-") as temp:
        root = Path(temp)
        original = {}
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar:
                if member.isdir():
                    continue
                if not member.isfile() or Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                    raise AssertionError(f"unsupported snapshot entry: {member.name}")
                data = tar.extractfile(member).read()
                original[member.name] = data
                path = root / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(member.mode & 0o777)
        calls = []
        proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def call(name, arguments):
            calls.append(name)
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": len(calls), "method": "tools/call",
                                         "params": {"name": name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            if not select.select([proc.stdout], [], [], 30)[0]:
                raise AssertionError(f"MCP timeout: {name}")
            response = json.loads(proc.stdout.readline())
            assert response["id"] == len(calls), response
            value = json.loads(response["result"]["content"][0]["text"])
            assert value["ok"], value
            return value["result"]

        def contents():
            return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
                    if p.is_file() and ".ffsake" not in p.relative_to(root).parts}

        try:
            paths = list(original)
            for offset in range(0, len(paths), 32):
                reads = call("ffsake_read_files", {"files": [{"path": p} for p in paths[offset:offset + 32]]})["files"]
                assert [r["path"] for r in reads] == paths[offset:offset + 32]
                for result in reads:
                    assert result["status"] == "ok"
                    assert result["read"]["text"] == "\n".join(original[result["path"]].decode().splitlines())
            marker = "\n<!-- repository workflow probe -->\n"
            operations = [{"op": "edit", "path": p, "old_text": original[p].decode(),
                           "new_text": original[p].decode() + marker, "expected_matches": 1}
                          for p in ["README.md", "PLAN.md"]]
            operations += [
                {"op": "move", "from": "docs/CLI.md", "to": "docs/CLI-moved.md"},
                {"op": "copy", "from": "Cargo.toml", "to": "Cargo.snapshot.toml"},
                {"op": "delete", "path": "docs/benchmark-results.json"},
                {"op": "create", "path": "docs/WORKFLOW-PROBE.md", "content": "probe\n"},
            ]
            plan = call("ffsake_preview_files", {"operations": operations})
            assert contents() == original
            transaction = call("ffsake_apply", {"handle": plan["handle"]})["transaction"]
            expected = dict(original)
            for path in ["README.md", "PLAN.md"]:
                expected[path] += marker.encode()
            expected["docs/CLI-moved.md"] = expected.pop("docs/CLI.md")
            expected["Cargo.snapshot.toml"] = expected["Cargo.toml"]
            del expected["docs/benchmark-results.json"]
            expected["docs/WORKFLOW-PROBE.md"] = b"probe\n"
            assert contents() == expected
            call("ffsake_undo", {"transaction": transaction})
            assert contents() == original
            call("ffsake_redo", {"transaction": transaction})
            assert contents() == expected
            call("ffsake_undo", {"transaction": transaction})
            assert contents() == original
            restored_mode = None
            if os.name == "posix":
                script = root / "scripts/ffsake_claude.py"
                script.chmod(0o755)
                plan = call("ffsake_preview_delete", {"path": "scripts/ffsake_claude.py"})
                transaction = call("ffsake_apply", {"handle": plan["handle"]})["transaction"]
                assert not script.exists()
                call("ffsake_undo", {"transaction": transaction})
                assert contents() == original
                restored_mode = oct(script.stat().st_mode & 0o7777)
                assert restored_mode == "0o755", restored_mode
            print(json.dumps({"snapshot_revision": revision, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                              "files": len(original), "bytes": sum(map(len, original.values())),
                              "mixed_operations": len(operations), "byte_history_passed": True,
                              "restored_executable_mode": restored_mode, "calls": calls, "model_calls": 0}, indent=2))
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()
        assert proc.returncode == 0, proc.returncode


if __name__ == "__main__":
    main()
