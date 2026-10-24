#!/usr/bin/env python3
"""Full committed-source LSP evaluation in a disposable archive; no model calls."""
import hashlib
import io
import os
import json
from pathlib import Path
import select
import subprocess
import sys
import tarfile
import tempfile
import time


def position(source, needle):
    start = source.index(needle)
    return {"line": source[:start].count("\n") + 1,
            "column": len(source[:start].rsplit("\n", 1)[-1])}


def main():
    repo = Path(__file__).resolve().parents[1]
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else repo / "target/debug/ffsake-mcp").resolve()
    verify_rename = "--verify-rename" in sys.argv[2:]
    rename_evidence = None
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    archive = subprocess.check_output(["git", "archive", revision], cwd=repo)
    with tempfile.TemporaryDirectory(prefix="ffsake-repository-lsp-") as temporary:
        root = Path(temporary)
        original = {}
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar:
                if member.isdir():
                    continue
                assert member.isfile() and not Path(member.name).is_absolute() and ".." not in Path(member.name).parts
                data = tar.extractfile(member).read()
                original[member.name] = data
                path = root / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(member.mode & 0o777)
        prefix = "crates/fff-workspace/src/"
        files = sorted(path for path in original if path.startswith(prefix) and path.endswith(".rs"))
        assert 1 <= len(files) <= 32 and all(len(original[path]) <= 256 * 1024 for path in files)
        library = prefix + "lib.rs"
        coordinates = prefix + "coordinates.rs"
        lsp = prefix + "lsp.rs"
        crates = [{"root": library}] + [{"root": prefix + "bin/" + name,
                    "dependencies": {"fff_workspace": library}} for name in ["ffsake.rs", "ffsake-mcp.rs"]]
        common = {"files": files, "crate_root": library, "edition": "2024", "crates": crates}
        proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        calls = []

        def call(name, arguments):
            request_id = len(calls) + 1
            started = time.monotonic()
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 35)[0], f"MCP timeout: {name}"
            response = json.loads(proc.stdout.readline())
            assert response["id"] == request_id, response
            value = json.loads(response["result"]["content"][0]["text"])
            assert value["ok"], value
            result = value["result"]
            if name.startswith("ffsake_rust_"):
                assert result["complete"] is False and set(result["file_hashes"]) == set(files)
                assert result["analysis_context"]["crate_root"] == library
            calls.append({"tool": name, "seconds": round(time.monotonic() - started, 3)})
            return result

        def contents():
            return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")
                    if path.is_file() and not {".ffsake", "target"}.intersection(path.relative_to(root).parts)}

        try:
            source = original[coordinates].decode()
            declaration = position(source, "pub struct Utf16Position")
            declaration["column"] += len("pub struct ")
            references = call("ffsake_rust_references", {**common, "path": coordinates,
                "position": declaration, "limit": 1000})
            paths = {item["path"] for item in references["ranges"]}
            assert references["matches"] >= 10 and len(paths) >= 3, references
            for item in references["ranges"]:
                assert original[item["path"]][item["start_byte"]:item["end_byte"]] == b"Utf16Position"
            definition = call("ffsake_rust_definition", {**common, "path": lsp,
                "position": position(original[lsp].decode(), "Utf16Position"), "limit": 100})
            assert definition["matches"] == 1 and definition["ranges"][0]["path"] == coordinates
            outline = call("ffsake_rust_symbols", {**common, "path": lsp, "limit": 1000})
            assert outline["symbol_count"] >= 10
            resident = prefix + "resident.rs"
            hover = call("ffsake_rust_hover", {**common, "path": resident,
                "position": position(original[resident].decode(), "MAX_HANDLES"), "max_bytes": 4096})
            hover_text = "\n".join(part["text"] for part in hover["hover"]["contents"])
            assert "usize" in hover_text and "128" in hover_text, hover
            at = position(source, "let mut units = 0u32;")
            at["column"] += len("let mut ")
            report = call("ffsake_rust_code_actions", {**common, "path": coordinates,
                "range": {"start": at, "end": {**at, "column": at["column"] + 5}}, "limit": 100})
            selected = [(index, item) for index, item in enumerate(report["actions"])
                        if item["title"] == "Insert explicit type `u32`"]
            assert len(selected) == 1, report
            index, item = selected[0]
            preview = call("ffsake_preview_rust_action", {"action_set": report["action_set"],
                "action_index": index, "expected_title": item["title"], "expected_edits": item["text_edit_count"]})
            assert preview["preview"]["changed_files"] == 1 and preview["semantic_validated"] is False
            assert preview["analysis_context"] == report["analysis_context"]
            assert contents() == original
            transaction = call("ffsake_apply", {"handle": preview["handle"]})["transaction"]
            expected = dict(original)
            expected[coordinates] = source.replace("let mut units = 0u32;", "let mut units: u32 = 0u32;", 1).encode()
            assert contents() == expected
            call("ffsake_undo", {"transaction": transaction})
            assert contents() == original
            call("ffsake_redo", {"transaction": transaction})
            assert contents() == expected
            call("ffsake_undo", {"transaction": transaction})
            assert contents() == original
            call("ffsake_release", {"handle": report["action_set"]})
            if verify_rename:
                def cargo_test():
                    # Compiler execution is an external test harness operation,
                    # never an analysis-server capability or proposed action.
                    checked = subprocess.run(["cargo", "test", "--locked", "--offline", "--workspace",
                        "--message-format=json"], cwd=root, capture_output=True, text=True, timeout=240,
                        env={**os.environ, "CARGO_TARGET_DIR": str(root / "target")})
                    errors = []
                    for line in checked.stdout.splitlines():
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event, dict) and event.get("reason") == "compiler-message":
                            message = event["message"]
                            if message["level"] == "error":
                                errors.append(message)
                    return checked, errors

                baseline, errors = cargo_test()
                assert baseline.returncode == 0 and not errors, (errors, baseline.stderr[-8192:])
                assert contents() == original
                rename = call("ffsake_rust_rename_proposal", {**common, "path": coordinates,
                    "position": declaration, "new_name": "Utf16Coordinate", "limit": 1000})
                proposal = rename["proposal"]
                assert proposal["unsupported_reasons"] == [] and proposal["text_edit_count"] >= 10, rename
                expected = dict(original)
                changed_paths = {edit["path"] for edit in proposal["edits"]}
                assert len(changed_paths) >= 3
                for path in changed_paths:
                    edits = sorted((edit for edit in proposal["edits"] if edit["path"] == path), key=lambda e: e["start_byte"])
                    assert all(a["end_byte"] <= b["start_byte"] for a, b in zip(edits, edits[1:]))
                    data = original[path]
                    for edit in reversed(edits):
                        assert data[edit["start_byte"]:edit["end_byte"]] == b"Utf16Position"
                        assert edit["replacement"] == "Utf16Coordinate"
                        data = data[:edit["start_byte"]] + b"Utf16Coordinate" + data[edit["end_byte"]:]
                    expected[path] = data
                preview = call("ffsake_preview_rust_rename", {"rename_set": rename["rename_set"],
                    "expected_path": coordinates, "expected_start_byte": rename["prepared"]["start_byte"],
                    "expected_old_text": "Utf16Position", "expected_new_name": "Utf16Coordinate",
                    "expected_edits": proposal["text_edit_count"]})
                assert preview["semantic_validated"] is False and preview["compiler_checked"] is False
                assert preview["analysis_context"] == rename["analysis_context"] and contents() == original
                transaction = call("ffsake_apply", {"handle": preview["handle"]})["transaction"]
                assert contents() == expected
                renamed, errors = cargo_test()
                assert renamed.returncode != 0 and errors, (renamed.returncode, renamed.stderr[-8192:])
                # Accept only the measured omitted-macro reference failure. A new
                # unrelated compiler error or runtime failure must fail this check.
                assert all(message["code"] and message["code"]["code"] in ("E0422", "E0425")
                           and "Utf16Position" in message["message"]
                           and any(span["is_primary"] and span["file_name"] == coordinates
                                   for span in message["spans"]) for message in errors), errors
                call("ffsake_undo", {"transaction": transaction})
                assert contents() == original
                call("ffsake_redo", {"transaction": transaction})
                assert contents() == expected
                call("ffsake_undo", {"transaction": transaction})
                assert contents() == original
                call("ffsake_release", {"handle": rename["rename_set"]})
                rename_evidence = {"baseline_cargo_tests": "passed", "renamed_cargo_tests": "expected_missing_macro_references",
                    "compiler_codes": sorted({message["code"]["code"] for message in errors}),
                    "compiler_errors": len(errors), "text_edits": proposal["text_edit_count"],
                    "changed_files": len(changed_paths), "byte_history_passed": True}

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
    print(json.dumps({"snapshot_revision": revision, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "rust_files": len(files), "rust_bytes": sum(len(original[path]) for path in files), "explicit_crates": len(crates),
        "references": references["matches"], "reference_files": len(paths), "outline_symbols": outline["symbol_count"],
        "byte_history_passed": True, "rename_evidence": rename_evidence, "calls": calls, "model_calls": 0,
        "timing_is_single_model_free_sample": True, "complete_analysis": False}, indent=2))


if __name__ == "__main__":
    main()
