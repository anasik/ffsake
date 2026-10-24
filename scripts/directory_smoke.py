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

        def search(pattern, paths):
            report = call("ffsake_search", {"pattern": pattern, "details": True})
            assert report["matches"] == len(paths), report
            assert {item["path"] for item in report["ranges"]} == set(paths), report
            call("ffsake_release", {"handle": report["handle"]})

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
            def move_preview():
                return call("ffsake_preview_files", {"operations": [
                    {"op": "move_directory", "from": "package", "to": "renamed"}]})

            (root / "package/empty").mkdir()
            (root / "package/blob").write_bytes(b"\x00\xff\r\n")
            (root / "package/search.txt").write_text("tree_search_sentinel\n")
            search("tree_search_sentinel", ["package/search.txt"])
            movement = move_preview()
            assert movement["preview"]["changed_files"] == 3
            assert not (root / "renamed").exists()
            (root / "package/extra").write_text("keep")
            call("ffsake_apply", {"handle": movement["handle"]}, False)
            assert (root / "package/extra").read_text() == "keep"
            (root / "package/extra").unlink()
            movement = move_preview()
            moved = call("ffsake_apply", {"handle": movement["handle"]})["transaction"]
            assert not (root / "package").exists() and (root / "renamed/empty").is_dir()
            search("tree_search_sentinel", ["renamed/search.txt"])
            assert (root / "renamed/blob").read_bytes() == b"\x00\xff\r\n"
            (root / "renamed/blob").write_bytes(b"changed")
            call("ffsake_undo", {"transaction": moved}, False)
            assert (root / "renamed/blob").read_bytes() == b"changed"
            (root / "renamed/blob").write_bytes(b"\x00\xff\r\n")
            call("ffsake_undo", {"transaction": moved})
            assert (root / "package/empty").is_dir() and not (root / "renamed").exists()
            search("tree_search_sentinel", ["package/search.txt"])
            call("ffsake_redo", {"transaction": moved})
            assert (root / "renamed/src/main.rs").read_text() == "fn main() {}\n"
            search("tree_search_sentinel", ["renamed/search.txt"])
            deletion = preview("delete_directory", "renamed")
            assert deletion["preview"]["retained_in_journal"] is True
            assert (root / "renamed/blob").exists()
            (root / "renamed/extra").write_text("preserve")
            call("ffsake_apply", {"handle": deletion["handle"]}, False)
            assert (root / "renamed/extra").read_text() == "preserve"
            (root / "renamed/extra").unlink()
            deletion = preview("delete_directory", "renamed")
            archived = call("ffsake_apply", {"handle": deletion["handle"]})["transaction"]
            assert not (root / "renamed").exists()
            search("tree_search_sentinel", [])
            call("ffsake_undo", {"transaction": archived})
            search("tree_search_sentinel", ["renamed/search.txt"])
            assert (root / "renamed/empty").is_dir()
            assert (root / "renamed/blob").read_bytes() == b"\x00\xff\r\n"
            call("ffsake_redo", {"transaction": archived})
            search("tree_search_sentinel", [])
            assert not (root / "renamed").exists()
            (root / "renamed").mkdir()
            (root / "renamed/keep").write_text("keep")
            call("ffsake_undo", {"transaction": archived}, False)
            assert (root / "renamed/keep").read_text() == "keep"
            (root / "copy-source/empty").mkdir(parents=True)
            (root / "copy-source/blob").write_bytes(b"original\x00\xff")
            (root / "copy-source/search.txt").write_text("copy_search_sentinel\n")
            if os.name == "posix":
                (root / "copy-source/blob").chmod(0o751)
            def copy_preview():
                return call("ffsake_preview_files", {"operations": [
                    {"op": "copy_directory", "from": "copy-source", "to": "copy-result"}]})

            copying = copy_preview()
            (root / "copy-source/blob").write_bytes(b"changed")
            call("ffsake_apply", {"handle": copying["handle"]}, False)
            assert not (root / "copy-result").exists()
            (root / "copy-source/blob").write_bytes(b"original\x00\xff")
            copying = copy_preview()
            copied = call("ffsake_apply", {"handle": copying["handle"]})["transaction"]
            assert (root / "copy-result/empty").is_dir()
            search("copy_search_sentinel", ["copy-source/search.txt", "copy-result/search.txt"])
            assert (root / "copy-result/blob").read_bytes() == b"original\x00\xff"
            call("ffsake_undo", {"transaction": copied})
            search("copy_search_sentinel", ["copy-source/search.txt"])
            (root / "copy-source/blob").write_bytes(b"independent source")
            call("ffsake_redo", {"transaction": copied})
            search("copy_search_sentinel", ["copy-source/search.txt", "copy-result/search.txt"])
            assert (root / "copy-result/blob").read_bytes() == b"original\x00\xff"
            assert (root / "copy-source/blob").read_bytes() == b"independent source"
            if os.name == "posix":
                assert (root / "copy-result/blob").stat().st_mode & 0o777 == 0o751
            automatic = call("ffsake_preview_create", {"path": "automatic/src/main.rs", "content": "fn main() {}\n"})
            automatic_id = call("ffsake_apply", {"handle": automatic["handle"]})["transaction"]
            (root / "automatic/extra").write_text("keep")
            call("ffsake_undo", {"transaction": automatic_id}, False)
            assert (root / "automatic/src/main.rs").read_text() == "fn main() {}\n"
            assert (root / "automatic/extra").read_text() == "keep"
            (root / "automatic/extra").unlink()
            call("ffsake_undo", {"transaction": automatic_id})
            assert not (root / "automatic").exists()
            call("ffsake_redo", {"transaction": automatic_id})
            assert (root / "automatic/src/main.rs").read_text() == "fn main() {}\n"
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
        print(json.dumps({"ok": True, "directory_history": True, "tree_move_history": True, "tree_archive_history": True, "independent_copy_history": True, "implicit_parent_history": True, "search_scope_and_refresh": True, "stale_content_preserved": True,
                          "recursive_removal": False, "core_profile": True}))


if __name__ == "__main__":
    main()
