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
    import select
    ready, _, _ = select.select([proc.stdout], [], [], 30)
    if not ready:
        proc.kill()
        raise AssertionError("MCP response timed out after 30 seconds")
    line = proc.stdout.readline()
    if not line:
        raise AssertionError(proc.stderr.read())
    return json.loads(line)

with tempfile.TemporaryDirectory(prefix="ffsake-mcp-") as temp:
    root = Path(temp) / "workspace"
    root.mkdir()
    outside = Path(temp) / "outside.txt"
    outside.write_text("outside-only-secret\n")
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
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
        })
        assert init["result"]["serverInfo"]["name"] == "ffsake"
        assert init["result"]["protocolVersion"] == "2025-11-25"

        listed = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert {"ffsake_search", "ffsake_preview_matches", "ffsake_preview_edit", "ffsake_apply", "ffsake_undo"} <= names

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

        # Verify the normal search -> preview -> apply -> verification workflow
        # without a redundant explicit refresh or large range payloads.
        for pattern, expected in [("needle", 0), ("world", 2)]:
            verified = send(proc, {
                "jsonrpc": "2.0", "id": "verify-after-apply-" + pattern,
                "method": "tools/call",
                "params": {"name": "ffsake_search", "arguments": {"pattern": pattern}},
            })
            verified_payload = json.loads(verified["result"]["content"][0]["text"])
            assert verified_payload["ok"], verified
            assert verified_payload["result"]["matches"] == expected, verified
            assert "ranges" not in verified_payload["result"], verified

        undone = send(proc, {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "ffsake_undo", "arguments": {"transaction": transaction}},
        })
        undone_payload = json.loads(undone["result"]["content"][0]["text"])
        assert undone_payload["ok"]
        assert (root / "a.txt").read_text() == "needle needle\n"
        # Exercise the public MCP boundary, not just Resident's Rust API.
        # A failed guard must return an MCP error and leave source/sentinel intact.
        request_id = 7
        denied_checks = 0

        def call(name, arguments, expect_ok=True):
            global request_id, denied_checks
            request_id += 1
            response = send(proc, {
                "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            })
            assert response["id"] == request_id, response
            result = response["result"]
            payload = json.loads(result["content"][0]["text"])
            assert payload["ok"] is expect_ok, (name, arguments, response)
            assert result["isError"] is (not expect_ok), response
            if not expect_ok:
                denied_checks += 1
                assert payload.get("error"), response
            assert outside.read_text() == "outside-only-secret\n"
            return payload.get("result")

        assert "ffsake_read_files" in names
        (root / "unicode.txt").write_text("α\nβ\n")
        batch = call("ffsake_read_files", {"files": [
            {"path": "unicode.txt", "start": 2, "end": 2},
            {"path": "missing/AGENTS.md"}, {"path": "a.txt"},
        ]})["files"]
        assert [r["status"] for r in batch] == ["ok", "missing", "ok"]
        assert batch[0]["read"]["text"] == "β"
        assert batch[0]["read"]["total_lines"] == 2
        assert batch[2]["read"] == call("ffsake_read", {"path": "a.txt"})
        (root / "unicode.txt").unlink()
        for files in [[], [{"path": "a.txt"}] * 33,
                      [{"path": "a.txt", "unexpected": True}],
                      [{"path": "a.txt", "start": 0}],
                      [{"path": "a.txt", "start": 2, "end": 1}]]:
            call("ffsake_read_files", {"files": files}, expect_ok=False)
        for path in ["../outside.txt", str(outside), ".ffsake", ".ffsake/missing", "."]:
            call("ffsake_read_files", {"files": [{"path": "a.txt"}, {"path": path}]}, expect_ok=False)
        (root / "binary.dat").write_bytes(b"\xff")
        call("ffsake_read_files", {"files": [{"path": "binary.dat"}]}, expect_ok=False)
        (root / "binary.dat").unlink()
        (root / "large.txt").write_text("x" * (256 * 1024))
        assert len(call("ffsake_read_files", {"files": [{"path": "large.txt"}]})["files"][0]["read"]["text"]) == 256 * 1024
        call("ffsake_read_files", {"files": [{"path": "large.txt"}] * 4})
        call("ffsake_read_files", {"files": [{"path": "large.txt"}] * 5}, expect_ok=False)
        (root / "large.txt").write_text("x" * (256 * 1024 + 1))
        call("ffsake_read_files", {"files": [{"path": "large.txt", "end": 1}]}, expect_ok=False)
        (root / "large.txt").unlink()
        for target in [outside, root / "a.txt", root / "missing", root / ".ffsake"]:
            (root / "batch-link").symlink_to(target)
            call("ffsake_read_files", {"files": [{"path": "batch-link"}]}, expect_ok=False)
            call("ffsake_read_files", {"files": [{"path": "batch-link/missing"}]}, expect_ok=False)
            (root / "batch-link").unlink()

        search_tool = next(tool for tool in listed["result"]["tools"] if tool["name"] == "ffsake_search")
        selector = search_tool["inputSchema"]["properties"]["target"]
        variants = selector["properties"]["include"]["items"]["anyOf"]
        assert variants[0]["enum"] == ["All"], variants
        assert {v["required"][0] for v in variants[1:]} == {"Exact", "Glob", "Regex"}
        for include, expected in [(["All"], 2), ([{"Exact": "a.txt"}], 2), ([], 0)]:
            scoped = call("ffsake_search", {"pattern": "needle", "target": {"include": include, "exclude": []}})
            assert scoped["matches"] == expected, scoped
            assert call("ffsake_release", {"handle": scoped["handle"]})["released"]

        for name in ["ffsake_read", "ffsake_list"]:
            for path in ["../outside.txt", str(outside), ".ffsake"]:
                call(name, {"path": path}, expect_ok=False)
        assert (root / "a.txt").read_text() == "needle needle\n"

        (root / "escape.txt").symlink_to(outside)
        call("ffsake_read", {"path": "escape.txt"}, expect_ok=False)
        call("ffsake_refresh", {})
        found_outside = call("ffsake_search", {"pattern": "outside-only-secret"})
        assert found_outside["matches"] == 0, found_outside
        call("ffsake_release", {"handle": found_outside["handle"]})

        for name in ["bash", "ffsake_exec", "ffsake_write"]:
            call(name, {"command": "touch forbidden.txt"}, expect_ok=False)
        assert not (root / "forbidden.txt").exists()
        call("ffsake_read", {"path": "a.txt", "unexpected": True}, expect_ok=False)
        call("ffsake_apply", {"handle": 999999}, expect_ok=False)
        # A successful preview is consumed by apply; replay must be rejected.
        call("ffsake_apply", {"handle": preview_handle}, expect_ok=False)
        # The original MatchSet is not an apply capability.
        call("ffsake_apply", {"handle": handle}, expect_ok=False)
        for name in ["ffsake_undo", "ffsake_redo"]:
            call(name, {"transaction": "../outside.txt"}, expect_ok=False)
        assert (root / "a.txt").read_text() == "needle needle\n"

        found_again = call("ffsake_search", {"pattern": "needle"})
        assert found_again["matches"] == 2, found_again
        fresh_handle = found_again["handle"]
        call("ffsake_preview_matches", {"handle": fresh_handle, "replacement": "world",
                                        "expected_matches": 1}, expect_ok=False)
        assert (root / "a.txt").read_text() == "needle needle\n"
        fresh_preview = call("ffsake_preview_matches", {
            "handle": fresh_handle, "replacement": "world", "expected_matches": 2})
        assert (root / "a.txt").read_text() == "needle needle\n"
        (root / "a.txt").write_text("external edit must survive\n")
        call("ffsake_apply", {"handle": fresh_preview["handle"]}, expect_ok=False)
        call("ffsake_preview_matches", {"handle": fresh_handle, "replacement": "world",
                                        "expected_matches": 2}, expect_ok=False)
        assert (root / "a.txt").read_text() == "external edit must survive\n"

        # Use valid, current handles so release is the only reason for rejection.
        call("ffsake_refresh", {})
        release_match = call("ffsake_search", {"pattern": "external"})
        assert release_match["matches"] == 1, release_match
        release_preview = call("ffsake_preview_matches", {
            "handle": release_match["handle"], "replacement": "world", "expected_matches": 1})
        assert call("ffsake_release", {"handle": release_preview["handle"]})["released"]
        call("ffsake_apply", {"handle": release_preview["handle"]}, expect_ok=False)
        assert call("ffsake_release", {"handle": release_match["handle"]})["released"]
        call("ffsake_preview_matches", {"handle": release_match["handle"], "replacement": "world",
                                        "expected_matches": 1}, expect_ok=False)
        assert (root / "a.txt").read_text() == "external edit must survive\n"
        # Multiline code editing uses one guarded preview, with literal Unicode,
        # whitespace and dollar signs preserved rather than regex-expanded.
        before = 'def first():\n    return "café"\n\ndef second():\n    return "$1"\n'
        after = 'def first():\n    return "你好"\n\ndef second():\n    return "$2"\n'
        (root / "code.py").write_text(before)
        edit = {"path": "code.py", "old_text": before, "new_text": after, "expected_matches": 1}
        plan = call("ffsake_preview_edit", edit)
        assert plan["preview"]["changed_files"] == 1
        assert (root / "code.py").read_text() == before
        changed = call("ffsake_apply", {"handle": plan["handle"]})
        assert changed["changed_files"] == 1
        assert (root / "code.py").read_text() == after
        call("ffsake_apply", {"handle": plan["handle"]}, expect_ok=False)
        call("ffsake_undo", {"transaction": changed["transaction"]})
        assert (root / "code.py").read_text() == before
        call("ffsake_redo", {"transaction": changed["transaction"]})
        assert (root / "code.py").read_text() == after
        verified = call("ffsake_search", {"pattern": "你好"})
        assert verified["matches"] == 1
        call("ffsake_release", {"handle": verified["handle"]})

        # Ambiguity, missing content, invalid paths/arguments and stale plans
        # must fail without writing either the workspace or outside sentinel.
        for override in [
            {"path": "../outside.txt"}, {"path": str(outside)},
            {"path": "escape.txt"}, {"path": ".ffsake"},
            {"path": "."}, {"path": "missing.py"},
            {"old_text": ""}, {"old_text": "absent"},
            {"old_text": "return", "expected_matches": 1},
            {"old_text": after, "expected_matches": 0},
            {"old_text": after, "expected_matches": -1},
            {"old_text": after, "unexpected": True},
        ]:
            call("ffsake_preview_edit", {**edit, **override}, expect_ok=False)
            assert (root / "code.py").read_text() == after
        stale = call("ffsake_preview_edit", {**edit, "old_text": after, "new_text": before})
        (root / "code.py").write_text("external code edit\n")
        call("ffsake_apply", {"handle": stale["handle"]}, expect_ok=False)
        assert (root / "code.py").read_text() == "external code edit\n"
        assert (root / "a.txt").read_text() == "external edit must survive\n"
        print("MCP multiline preview/edit/apply/undo/redo and stale-content guards PASS")
        # File operations share the preview/apply/history flow. Destinations
        # must be absent; source and destination state are frozen at preview.
        names_expected = {"ffsake_preview_create", "ffsake_preview_copy",
                          "ffsake_preview_move", "ffsake_preview_delete"}
        assert names_expected <= names
        created_path = root / "nested" / "created.txt"
        create = call("ffsake_preview_create", {"path": "nested/created.txt", "content": "new café\n"})
        assert not created_path.exists()
        tx = call("ffsake_apply", {"handle": create["handle"]})["transaction"]
        assert created_path.read_text() == "new café\n"
        call("ffsake_undo", {"transaction": tx})
        assert not created_path.exists()
        call("ffsake_redo", {"transaction": tx})
        assert created_path.read_text() == "new café\n"
        call("ffsake_preview_create", {"path": "nested/created.txt", "content": "overwrite"}, expect_ok=False)

        binary_bytes = bytes([0, 255, 10, 128])
        (root / "binary.dat").write_bytes(binary_bytes)
        copied = call("ffsake_preview_copy", {"from": "binary.dat", "to": "nested/copied.dat"})
        assert not (root / "nested/copied.dat").exists()
        copy_tx = call("ffsake_apply", {"handle": copied["handle"]})["transaction"]
        assert (root / "nested/copied.dat").read_bytes() == binary_bytes
        call("ffsake_undo", {"transaction": copy_tx})
        assert not (root / "nested/copied.dat").exists()
        call("ffsake_redo", {"transaction": copy_tx})
        moved = call("ffsake_preview_move", {"from": "nested/copied.dat", "to": "renamed.dat"})
        assert (root / "nested/copied.dat").exists()
        assert not (root / "renamed.dat").exists()
        move_tx = call("ffsake_apply", {"handle": moved["handle"]})["transaction"]
        assert not (root / "nested/copied.dat").exists()
        assert (root / "renamed.dat").read_bytes() == binary_bytes
        call("ffsake_undo", {"transaction": move_tx})
        assert (root / "nested/copied.dat").read_bytes() == binary_bytes
        assert not (root / "renamed.dat").exists()
        call("ffsake_redo", {"transaction": move_tx})
        deleted = call("ffsake_preview_delete", {"path": "renamed.dat"})
        assert (root / "renamed.dat").exists()
        delete_tx = call("ffsake_apply", {"handle": deleted["handle"]})["transaction"]
        assert not (root / "renamed.dat").exists()
        call("ffsake_undo", {"transaction": delete_tx})
        assert (root / "renamed.dat").read_bytes() == binary_bytes
        call("ffsake_redo", {"transaction": delete_tx})
        assert not (root / "renamed.dat").exists()

        # A copy plan must notice source changes even though it only writes
        # the destination. A create plan must not overwrite a raced-in file.
        stale_copy = call("ffsake_preview_copy", {"from": "binary.dat", "to": "stale.dat"})
        (root / "binary.dat").write_bytes(b"external")
        call("ffsake_apply", {"handle": stale_copy["handle"]}, expect_ok=False)
        assert not (root / "stale.dat").exists()
        raced = call("ffsake_preview_create", {"path": "raced.txt", "content": ""})
        (root / "raced.txt").write_text("external")
        call("ffsake_apply", {"handle": raced["handle"]}, expect_ok=False)
        assert (root / "raced.txt").read_text() == "external"
        stale_delete = call("ffsake_preview_delete", {"path": "raced.txt"})
        (root / "raced.txt").write_text("later external")
        call("ffsake_apply", {"handle": stale_delete["handle"]}, expect_ok=False)
        assert (root / "raced.txt").read_text() == "later external"
        stale_move = call("ffsake_preview_move", {"from": "raced.txt", "to": "move-dest.txt"})
        (root / "move-dest.txt").write_text("occupied")
        call("ffsake_apply", {"handle": stale_move["handle"]}, expect_ok=False)
        assert (root / "raced.txt").read_text() == "later external"
        assert (root / "move-dest.txt").read_text() == "occupied"

        # Reject traversal, directories, internal paths and symlinks, including
        # links inserted between preview and apply or between apply and undo.
        for bad_path in ["../outside.txt", str(outside), ".ffsake/private", ".", "nested", "escape.txt"]:
            call("ffsake_preview_create", {"path": bad_path, "content": "bad"}, expect_ok=False)
            call("ffsake_preview_delete", {"path": bad_path}, expect_ok=False)
            for tool_name in ["ffsake_preview_copy", "ffsake_preview_move"]:
                call(tool_name, {"from": bad_path, "to": "unused.dat"}, expect_ok=False)
                call(tool_name, {"from": "binary.dat", "to": bad_path}, expect_ok=False)
        (root / "dangling").symlink_to(Path(temp) / "missing")
        call("ffsake_preview_create", {"path": "dangling", "content": "bad"}, expect_ok=False)
        safe_dir = root / "safe-dir"
        safe_dir.mkdir()
        swapped = call("ffsake_preview_create", {"path": "safe-dir/new.txt", "content": "bad"})
        safe_dir.rmdir()
        safe_dir.symlink_to(Path(temp), target_is_directory=True)
        call("ffsake_apply", {"handle": swapped["handle"]}, expect_ok=False)
        assert not (Path(temp) / "new.txt").exists()
        safe_dir.unlink()
        history_dir = root / "history-dir"
        history_dir.mkdir()
        undo_guard = call("ffsake_preview_create", {"path": "history-dir/outside.txt", "content": "inside"})
        undo_guard_tx = call("ffsake_apply", {"handle": undo_guard["handle"]})["transaction"]
        (history_dir / "outside.txt").unlink()
        history_dir.rmdir()
        history_dir.symlink_to(Path(temp), target_is_directory=True)
        call("ffsake_undo", {"transaction": undo_guard_tx}, expect_ok=False)
        assert outside.read_text() == "outside-only-secret\n"
        empty_plan = call("ffsake_preview_create", {"path": "empty.txt", "content": ""})
        empty_tx = call("ffsake_apply", {"handle": empty_plan["handle"]})["transaction"]
        assert (root / "empty.txt").read_bytes() == b""
        call("ffsake_undo", {"transaction": empty_tx})
        assert not (root / "empty.txt").exists()
        released = call("ffsake_preview_create", {"path": "released.txt", "content": "x"})
        call("ffsake_release", {"handle": released["handle"]})
        call("ffsake_apply", {"handle": released["handle"]}, expect_ok=False)
        assert not (root / "released.txt").exists()
        print("MCP create/copy/move/delete, history, collision and symlink guards PASS")
        # Several independent files are one preview, one apply and one
        # transaction. Any stale member rejects the entire batch before writes.
        assert "ffsake_preview_files" in names
        batch_ops = [
            {"op": "create", "path": "batch/a.txt", "content": "batch needle α\n"},
            {"op": "create", "path": "batch/b.txt", "content": "batch needle β\n"},
            {"op": "create", "path": "batch/empty.txt", "content": ""},
        ]
        batch = call("ffsake_preview_files", {"operations": batch_ops})
        assert batch["preview"]["changed_files"] == 3
        assert not (root / "batch").exists()
        batch_tx = call("ffsake_apply", {"handle": batch["handle"]})["transaction"]
        assert (root / "batch/a.txt").read_text() == "batch needle α\n"
        assert (root / "batch/b.txt").read_text() == "batch needle β\n"
        assert (root / "batch/empty.txt").read_bytes() == b""
        found_batch = call("ffsake_search", {"pattern": "batch needle"})
        assert found_batch["matches"] == 2
        call("ffsake_release", {"handle": found_batch["handle"]})
        call("ffsake_undo", {"transaction": batch_tx})
        assert all(not (root / op["path"]).exists() for op in batch_ops)
        call("ffsake_redo", {"transaction": batch_tx})
        assert (root / "batch/a.txt").read_text() == "batch needle α\n"
        call("ffsake_apply", {"handle": batch["handle"]}, expect_ok=False)

        mixed_ops = [
            {"op": "copy", "from": "batch/a.txt", "to": "batch/copy.txt"},
            {"op": "move", "from": "batch/b.txt", "to": "batch/moved.txt"},
            {"op": "delete", "path": "batch/empty.txt"},
            {"op": "create", "path": "batch/new.txt", "content": "new"},
        ]
        mixed = call("ffsake_preview_files", {"operations": mixed_ops})
        mixed_tx = call("ffsake_apply", {"handle": mixed["handle"]})["transaction"]
        assert (root / "batch/copy.txt").read_bytes() == (root / "batch/a.txt").read_bytes()
        assert (root / "batch/moved.txt").read_text() == "batch needle β\n"
        assert not (root / "batch/b.txt").exists()
        assert not (root / "batch/empty.txt").exists()
        call("ffsake_undo", {"transaction": mixed_tx})
        assert not (root / "batch/copy.txt").exists()
        assert not (root / "batch/moved.txt").exists()
        assert not (root / "batch/new.txt").exists()
        assert (root / "batch/b.txt").read_text() == "batch needle β\n"
        assert (root / "batch/empty.txt").read_bytes() == b""
        call("ffsake_redo", {"transaction": mixed_tx})
        assert (root / "batch/new.txt").read_text() == "new"

        for bad_ops in [
            [],
            [{"op": "create", "path": f"too-many/{n}", "content": ""} for n in range(101)],
            [{"op": "create", "path": "dupe", "content": "a"},
             {"op": "create", "path": "./dupe", "content": "b"}],
            [{"op": "create", "path": "parent", "content": "a"},
             {"op": "create", "path": "parent/child", "content": "b"}],
            [{"op": "copy", "from": "batch/a.txt", "to": "copy-again"},
             {"op": "delete", "path": "batch/a.txt"}],
            [{"op": "delete", "path": "batch/a.txt"},
             {"op": "copy", "from": "batch/a.txt", "to": "copy-again"}],
            [{"op": "create", "path": "first-invalid", "content": "x"},
             {"op": "create", "path": "../outside.txt", "content": "bad"}],
            [{"op": "create", "path": "first-invalid", "content": "x"},
             {"op": "create", "path": "history-dir/outside.txt", "content": "bad"}],
            [{"op": "create", "path": "dupe", "content": "x", "unknown": True}],
            [{"op": "shell", "command": "touch forbidden.txt"}],
        ]:
            call("ffsake_preview_files", {"operations": bad_ops}, expect_ok=False)
            assert not (root / "dupe").exists()
            assert not (root / "parent").exists()
            assert not (root / "copy-again").exists()
            assert not (root / "first-invalid").exists()
            assert not (root / "too-many").exists()

        shared_source = call("ffsake_preview_files", {"operations": [
            {"op": "copy", "from": "batch/a.txt", "to": "batch/read-copy-1.txt"},
            {"op": "copy", "from": "batch/a.txt", "to": "batch/read-copy-2.txt"},
        ]})
        shared_tx = call("ffsake_apply", {"handle": shared_source["handle"]})["transaction"]
        assert (root / "batch/read-copy-1.txt").read_bytes() == (root / "batch/a.txt").read_bytes()
        assert (root / "batch/read-copy-2.txt").read_bytes() == (root / "batch/a.txt").read_bytes()
        call("ffsake_undo", {"transaction": shared_tx})
        assert not (root / "batch/read-copy-1.txt").exists()
        assert not (root / "batch/read-copy-2.txt").exists()

        raced_batch = call("ffsake_preview_files", {"operations": [
            {"op": "create", "path": "batch/no-partial.txt", "content": "must not appear"},
            {"op": "create", "path": "batch/raced.txt", "content": "planned"},
        ]})
        (root / "batch/raced.txt").write_text("external")
        call("ffsake_apply", {"handle": raced_batch["handle"]}, expect_ok=False)
        assert not (root / "batch/no-partial.txt").exists()
        assert (root / "batch/raced.txt").read_text() == "external"
        copy_batch = call("ffsake_preview_files", {"operations": [
            {"op": "create", "path": "batch/no-copy-partial.txt", "content": "must not appear"},
            {"op": "copy", "from": "batch/a.txt", "to": "batch/stale-copy.txt"},
        ]})
        (root / "batch/a.txt").write_text("external source")
        call("ffsake_apply", {"handle": copy_batch["handle"]}, expect_ok=False)
        assert not (root / "batch/no-copy-partial.txt").exists()
        assert not (root / "batch/stale-copy.txt").exists()
        release_batch = call("ffsake_preview_files", {"operations": [
            {"op": "create", "path": "batch/released.txt", "content": ""}
        ]})
        call("ffsake_release", {"handle": release_batch["handle"]})
        call("ffsake_apply", {"handle": release_batch["handle"]}, expect_ok=False)
        assert not (root / "batch/released.txt").exists()
        # Existing files and structural operations share one transaction.
        edit_before = "café\r\nold\r\n"
        edit_after = "你好\r\nnew$1\r\n"
        (root / "batch/edit-a.txt").write_bytes(edit_before.encode())
        (root / "batch/edit-b.txt").write_text("old old")
        (root / "batch/edit-move.txt").write_text("move me")
        (root / "batch/edit-delete.txt").write_text("delete me")
        edit_batch = call("ffsake_preview_files", {"operations": [
            {"op": "edit", "path": "batch/edit-a.txt", "old_text": edit_before,
             "new_text": edit_after, "expected_matches": 1},
            {"op": "edit", "path": "batch/edit-b.txt", "old_text": "old",
             "new_text": "$2", "expected_matches": 2},
            {"op": "create", "path": "batch/edit-new.txt", "content": "created"},
            {"op": "move", "from": "batch/edit-move.txt", "to": "batch/edit-moved.txt"},
            {"op": "delete", "path": "batch/edit-delete.txt"},
        ]})
        assert edit_batch["preview"]["changed_files"] == 6
        assert edit_batch["preview"]["files"][0]["operation"] == "edit"
        assert (root / "batch/edit-a.txt").read_bytes().decode() == edit_before
        assert not (root / "batch/edit-new.txt").exists()
        edit_tx = call("ffsake_apply", {"handle": edit_batch["handle"]})["transaction"]
        assert (root / "batch/edit-a.txt").read_bytes().decode() == edit_after
        assert (root / "batch/edit-b.txt").read_text() == "$2 $2"
        assert (root / "batch/edit-moved.txt").read_text() == "move me"
        assert not (root / "batch/edit-move.txt").exists()
        assert not (root / "batch/edit-delete.txt").exists()
        call("ffsake_undo", {"transaction": edit_tx})
        assert (root / "batch/edit-a.txt").read_bytes().decode() == edit_before
        assert (root / "batch/edit-b.txt").read_text() == "old old"
        assert not (root / "batch/edit-new.txt").exists()
        assert (root / "batch/edit-move.txt").read_text() == "move me"
        assert not (root / "batch/edit-moved.txt").exists()
        assert (root / "batch/edit-delete.txt").read_text() == "delete me"
        call("ffsake_redo", {"transaction": edit_tx})
        assert (root / "batch/edit-a.txt").read_bytes().decode() == edit_after
        assert (root / "batch/edit-new.txt").read_text() == "created"

        valid_edit = {"op": "edit", "path": "batch/edit-b.txt", "old_text": "$2",
                      "new_text": "new", "expected_matches": 2}
        (root / "batch/non-utf8.bin").write_bytes(bytes([255]))
        for overrides in [
            {"old_text": ""}, {"expected_matches": 0}, {"expected_matches": -1},
            {"expected_matches": 1}, {"old_text": "missing"}, {"new_text": "$2"},
            {"path": "batch/non-utf8.bin"}, {"path": "history-dir/outside.txt"},
            {"path": ".ffsake/internal"}, {"unexpected": True},
        ]:
            call("ffsake_preview_files", {"operations": [
                {"op": "create", "path": "batch/invalid-edit-new.txt", "content": "no"},
                {**valid_edit, **overrides},
            ]}, expect_ok=False)
            assert not (root / "batch/invalid-edit-new.txt").exists()
            assert (root / "batch/edit-b.txt").read_text() == "$2 $2"
        for conflicting in [
            [valid_edit, valid_edit],
            [valid_edit, {"op": "copy", "from": "batch/edit-b.txt", "to": "batch/alias-copy.txt"}],
            [{"op": "copy", "from": "batch/edit-b.txt", "to": "batch/alias-copy.txt"}, valid_edit],
            [valid_edit, {"op": "move", "from": "batch/edit-b.txt", "to": "batch/alias-move.txt"}],
        ]:
            call("ffsake_preview_files", {"operations": conflicting}, expect_ok=False)
            assert not (root / "batch/alias-copy.txt").exists()
            assert not (root / "batch/alias-move.txt").exists()
        stale_edit = call("ffsake_preview_files", {"operations": [
            {"op": "create", "path": "batch/stale-edit-new.txt", "content": "no"},
            valid_edit,
        ]})
        (root / "batch/edit-b.txt").write_text("external edit")
        call("ffsake_apply", {"handle": stale_edit["handle"]}, expect_ok=False)
        assert not (root / "batch/stale-edit-new.txt").exists()
        assert (root / "batch/edit-b.txt").read_text() == "external edit"
        cleared = call("ffsake_preview_files", {"operations": [{
            "op": "edit", "path": "batch/edit-b.txt", "old_text": "external edit",
            "new_text": "", "expected_matches": 1
        }]})
        clear_tx = call("ffsake_apply", {"handle": cleared["handle"]})["transaction"]
        assert (root / "batch/edit-b.txt").read_bytes() == b""
        call("ffsake_undo", {"transaction": clear_tx})
        assert (root / "batch/edit-b.txt").read_text() == "external edit"
        print("MCP batched block edits, mixed history, UTF-8 and stale-edit guards PASS")
        print("MCP batch operations, one-step history, conflicts and stale-state guards PASS")
        print(f"MCP boundary guards: {denied_checks} rejected operations; sentinel and source checks PASS")
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0, proc.stderr.read()

print("MCP smoke: initialize/tools/search/preview/apply/undo PASS")


