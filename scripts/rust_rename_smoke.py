#!/usr/bin/env python3
"""Real MCP rename discovery, with independent positive/negative compiler evidence."""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile

from rust_rename_probe import check_program, rename_cases


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    results = []
    for name, query, count, outcome, sources in rename_cases():
        sources = {**sources, "context.rs": "pub const CONTEXT: u32 = 1;\r\n"}
        with tempfile.TemporaryDirectory(prefix="ffsake-rename-mcp-") as temporary:
            base = Path(temporary)
            root = base / "analysis"
            root.mkdir()
            for path, source in sources.items():
                (root / path).write_bytes(source.encode())
            proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            request_id = 0

            def call(arguments, ok=True, tool="ffsake_rust_rename_proposal"):
                nonlocal request_id
                request_id += 1
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments}}) + "\n")
                proc.stdin.flush()
                assert select.select([proc.stdout], [], [], 35)[0], "MCP rename timeout"
                response = json.loads(proc.stdout.readline())
                assert response["id"] == request_id, response
                value = json.loads(response["result"]["content"][0]["text"])
                assert value["ok"] == ok, value
                return value.get("result", value)

            try:
                source = sources[query]
                start = source.index("account")
                arguments = {"files": list(sources), "crate_root": "main.rs", "path": query,
                    "edition": "2024", "new_name": "customer", "limit": 100,
                    "position": {"line": source[:start].count("\n") + 1,
                                 "column": len(source[:start].rsplit("\n", 1)[-1])}}
                if name == "distinct_bindings":
                    arguments["crates"] = [{"root": "main.rs", "features": ["original_context"]}]
                if outcome == "unsupported_alias":
                    rejected = call(arguments, ok=False)
                    assert rejected["error"] == 'rust-analyzer rejected textDocument/prepareRename (-32602): "Renaming aliases is currently unsupported"', rejected
                    assert "rename_set" not in rejected and "handle" not in rejected
                    assert not (root / ".ffsake").exists()
                    assert {p.name: p.read_bytes() for p in root.iterdir()} == {p: s.encode() for p, s in sources.items()}
                    check_program(base / "before", sources, "passes")
                    results.append({"case": name, "native_rejected": True, "source_restored": True, "guarded_history": False})
                    continue
                report = call(arguments)
                assert report["complete"] is False and report["semantic_validated"] is False
                assert report["compiler_checked"] is False and report["new_name"] == "customer"
                assert "handle" not in report and "action_set" not in report
                assert set(report["file_hashes"]) == set(sources)
                assert report["analysis_context"]["crate_root"] == "main.rs"
                selected = report["prepared"]
                assert selected["old_text"] == "account"
                assert source.encode()[selected["start_byte"]:selected["end_byte"]] == b"account"
                proposal = report["proposal"]
                assert report["rename_set"] > 0
                assert not (root / ".ffsake").exists(), "discovery created journal state"
                preview_args = {"rename_set": report["rename_set"], "expected_path": query,
                    "expected_start_byte": selected["start_byte"], "expected_old_text": "account",
                    "expected_new_name": "customer", "expected_edits": count or 1}
                call({"handle": report["rename_set"]}, ok=False, tool="ffsake_apply")
                check_program(base / "before", sources, "passes")
                if outcome == "not_applied":
                    assert proposal["edits"] is None and "resource_operation" in proposal["unsupported_reasons"]
                    assert not (root / "customer.rs").exists()
                    call(preview_args, ok=False, tool="ffsake_preview_rust_rename")
                    call({"handle": report["rename_set"]}, tool="ffsake_release")
                else:
                    assert proposal["unsupported_reasons"] == [] and proposal["text_edit_count"] == count
                    assert len(proposal["edits"]) == count
                    staged = dict(sources)
                    for path, original in sources.items():
                        edits = sorted((e for e in proposal["edits"] if e["path"] == path), key=lambda e: e["start_byte"])
                        assert all(a["end_byte"] <= b["start_byte"] for a, b in zip(edits, edits[1:]))
                        data = original.encode()
                        for edit in reversed(edits):
                            assert data[edit["start_byte"]:edit["end_byte"]] == b"account"
                            assert edit["replacement"] == "customer"
                            data = data[:edit["start_byte"]] + edit["replacement"].encode() + data[edit["end_byte"]:]
                        staged[path] = data.decode()
                    assert all(e["path"] in sources for e in proposal["edits"])
                    if name == "distinct_bindings":
                        assert "let account = 2; account" in staged["main.rs"]
                        call({**arguments, "limit": 1}, ok=False)
                        for invalid in ("", "fn", "r#self", "two names", "a" * 257):
                            call({**arguments, "new_name": invalid}, ok=False)
                        for invalid in (0, 1001):
                            call({**arguments, "limit": invalid}, ok=False)
                        raw = call({**arguments, "new_name": "r#type", "crates": [{"root": "main.rs", "features": ["later_context"]}]})
                        assert raw["analysis_context"] != report["analysis_context"]
                        assert all(e["replacement"] == "r#type" for e in raw["proposal"]["edits"]), raw
                        call({"handle": raw["rename_set"]}, tool="ffsake_release")
                        for changes in ({"expected_path": "context.rs"}, {"expected_start_byte": selected["start_byte"] + 1},
                                        {"expected_old_text": "other"}, {"expected_new_name": "other"},
                                        {"expected_edits": count + 1}, {"expected_edits": 0}):
                            call({**preview_args, **changes}, ok=False, tool="ffsake_preview_rust_rename")
                        call({"action_set": report["rename_set"], "action_index": 0, "expected_title": "Rename proposal",
                              "expected_edits": count}, ok=False, tool="ffsake_preview_rust_action")
                        (root / "context.rs").write_text("pub const CONTEXT: u32 = 2;")
                        call(preview_args, ok=False, tool="ffsake_preview_rust_rename")
                        (root / "context.rs").write_bytes(sources["context.rs"].encode())
                    preview = call(preview_args, tool="ffsake_preview_rust_rename")
                    assert preview["analysis_context"] == report["analysis_context"]
                    assert preview["prepared"] == report["prepared"] and preview["new_name"] == "customer"
                    assert preview["semantic_validated"] is False and preview["compiler_checked"] is False
                    assert all((root / path).read_bytes() == text.encode() for path, text in sources.items())
                    call({"handle": report["rename_set"]}, tool="ffsake_release")
                    call(preview_args, ok=False, tool="ffsake_preview_rust_rename")
                    if name == "distinct_bindings":
                        (root / "context.rs").write_text("pub const CONTEXT: u32 = 2;")
                        call({"handle": preview["handle"]}, ok=False, tool="ffsake_apply")
                        assert (root / "main.rs").read_bytes() == sources["main.rs"].encode()
                        (root / "context.rs").write_bytes(sources["context.rs"].encode())
                    transaction = call({"handle": preview["handle"]}, tool="ffsake_apply")["transaction"]
                    assert all((root / path).read_bytes() == text.encode() for path, text in staged.items())
                    call({"handle": preview["handle"]}, ok=False, tool="ffsake_apply")
                    check_program(base / "after", staged, outcome)
                    call({"transaction": transaction}, tool="ffsake_undo")
                    assert all((root / path).read_bytes() == text.encode() for path, text in sources.items())
                    call({"transaction": transaction}, tool="ffsake_redo")
                    assert all((root / path).read_bytes() == text.encode() for path, text in staged.items())
                    call({"transaction": transaction}, tool="ffsake_undo")
                assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == {p: s.encode() for p, s in sources.items()}
                results.append({"case": name, "external_compiler_outcome": outcome, "source_restored": True, "guarded_history": outcome != "not_applied"})
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
    print(json.dumps({"cases": results, "discovery_directly_applyable": False, "model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
