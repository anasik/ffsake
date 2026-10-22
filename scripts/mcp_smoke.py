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
        print(f"MCP boundary guards: {denied_checks} rejected operations; sentinel and source checks PASS")
    finally:
        proc.stdin.close()
        assert proc.wait(timeout=30) == 0, proc.stderr.read()

print("MCP smoke: initialize/tools/search/preview/apply/undo PASS")


