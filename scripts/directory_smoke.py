#!/usr/bin/env python3
"""Exercise empty-directory previews, stale guards and persisted MCP history."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    with tempfile.TemporaryDirectory(prefix="ffsake-directory-") as temporary:
        root = Path(temporary)
        proc = subprocess.Popen([str(binary), str(root), "--profile", "core"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        request_id = 0

        def call(name, arguments, ok=True):
            nonlocal request_id
            request_id += 1
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 10)[0], name
            response = json.loads(proc.stdout.readline())
            value = json.loads(response["result"]["content"][0]["text"])
            assert response["id"] == request_id and value["ok"] == ok, value
            return value.get("result", value)

        def preview(operation, path, ok=True):
            return call("ffsake_preview_files", {"operations": [{"op": operation, "path": path}]}, ok)

        try:
            pending = preview("create_directory", "empty")
            assert pending["preview"]["changed_directories"] == 1 and not (root / "empty").exists()
            created = call("ffsake_apply", {"handle": pending["handle"]})["transaction"]
            assert (root / "empty").is_dir()
            call("ffsake_apply", {"handle": pending["handle"]}, False)
            pending = preview("remove_directory", "empty")
            (root / "empty/keep").write_bytes(b"keep\x00\xff")
            call("ffsake_apply", {"handle": pending["handle"]}, False)
            call("ffsake_undo", {"transaction": created}, False)
            assert (root / "empty/keep").read_bytes() == b"keep\x00\xff"
            (root / "empty/keep").unlink()
            call("ffsake_undo", {"transaction": created})
            assert not (root / "empty").exists()
            call("ffsake_redo", {"transaction": created})
            if os.name == "posix":
                (root / "empty").chmod(0o750)
            pending = preview("remove_directory", "empty")
            removed = call("ffsake_apply", {"handle": pending["handle"]})["transaction"]
            assert not (root / "empty").exists()
            call("ffsake_undo", {"transaction": removed})
            if os.name == "posix":
                assert (root / "empty").stat().st_mode & 0o777 == 0o750
            call("ffsake_redo", {"transaction": removed})
            assert not (root / "empty").exists()
            # Directory and file transactions retain independent history and
            # require safe dependency ordering when building a nested package.
            parent = preview("create_directory", "package")
            parent_id = call("ffsake_apply", {"handle": parent["handle"]})["transaction"]
            child = preview("create_directory", "package/src")
            child_id = call("ffsake_apply", {"handle": child["handle"]})["transaction"]
            file = call("ffsake_preview_files", {"operations": [
                {"op": "create", "path": "package/src/main.rs", "content": "fn main() {}\n"}]})
            file_id = call("ffsake_apply", {"handle": file["handle"]})["transaction"]
            call("ffsake_undo", {"transaction": parent_id}, False)
            call("ffsake_undo", {"transaction": child_id}, False)
            call("ffsake_undo", {"transaction": file_id})
            call("ffsake_undo", {"transaction": child_id})
            call("ffsake_undo", {"transaction": parent_id})
            assert not (root / "package").exists()
            call("ffsake_redo", {"transaction": child_id}, False)
            call("ffsake_redo", {"transaction": parent_id})
            call("ffsake_redo", {"transaction": child_id})
            call("ffsake_redo", {"transaction": file_id})
            assert (root / "package/src/main.rs").read_text() == "fn main() {}\n"
            for path in (".", "../outside", ".ffsake/hidden", "missing/child"):
                preview("create_directory", path, False)
            pending = preview("create_directory", "collision")
            (root / "collision").write_text("preserve")
            call("ffsake_apply", {"handle": pending["handle"]}, False)
            assert (root / "collision").read_text() == "preserve"
            call("ffsake_preview_files", {"operations": [
                {"op": "create_directory", "path": "mixed"},
                {"op": "create", "path": "other", "content": "no"}]}, False)
            assert not (root / "mixed").exists() and not (root / "other").exists()
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)
            assert proc.returncode == 0, proc.stderr.read()
        print(json.dumps({"ok": True, "directory_history": True, "stale_content_preserved": True,
                          "recursive_removal": False, "core_profile": True}))


if __name__ == "__main__":
    main()
