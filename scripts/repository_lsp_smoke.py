#!/usr/bin/env python3
"""Full committed-source LSP evaluation in a disposable archive; no model calls."""
import hashlib
import io
import os
import json
from pathlib import Path
import select
import statistics
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
    diagnostic_iterations = 5 if "--measure-diagnostics" in sys.argv[2:] else 1
    inspection_iterations = 5 if "--measure-inspection" in sys.argv[2:] else 1
    rename_evidence = None
    relocation_evidence = None
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
        assert 1 <= len(files) <= 64 and all(len(original[path]) <= 256 * 1024 for path in files)
        assert sum(len(original[path]) for path in files) <= 8 * 1024 * 1024
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
            if name.startswith("ffsake_rust_") and name != "ffsake_rust_context_from_manifests":
                assert result["complete"] is False and set(result["file_hashes"]) == set(arguments["files"])
                assert result["analysis_context"]["crate_root"] == arguments["crate_root"]
            calls.append({"tool": name, "seconds": round(time.monotonic() - started, 3)})
            return result

        def contents():
            return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")
                    if path.is_file() and not {".ffsake", "target"}.intersection(path.relative_to(root).parts)}

        try:
            imported = call("ffsake_rust_context_from_manifests", {
                "manifests": ["Cargo.toml", "crates/fff-workspace/Cargo.toml"],
                "files": files, "crate_root": library})
            context = imported["analysis_arguments"]
            assert context["files"] == files and context["crate_root"] == library
            assert context["edition"] == "2024" and imported["complete"] is False
            actual_edges = {c["root"]: c["dependencies"] for c in context["crates"]}
            assert actual_edges == {c["root"]: c.get("dependencies", {}) for c in crates}
            assert set(imported["source_hashes"]) == set(files)
            assert imported["omissions"], "external dependencies must be explicitly omitted"
            common = context
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
            inspection_samples = []
            inspection_arguments = {**common, "path": coordinates, "position": declaration}
            for iteration in range(inspection_iterations):
                def combined_inspection():
                    report = call("ffsake_rust_inspect", {**inspection_arguments, "limit": 1000})
                    assert report["compiler_checked"] is False
                    return report, calls[-1]["seconds"]

                def individual_inspection():
                    reports = {}
                    seconds = 0
                    for name in ("definition", "references", "hover"):
                        bounds = {"max_bytes": 8192} if name == "hover" else {"limit": 1000}
                        report = call("ffsake_rust_" + name, {**inspection_arguments, **bounds})
                        assert report["file_hashes"] == references["file_hashes"]
                        assert report["analysis_context"] == references["analysis_context"]
                        fields = ("path", "hover", "text_bytes") if name == "hover" else ("ranges", "matches")
                        reports[name] = {key: report[key] for key in fields}
                        seconds += calls[-1]["seconds"]
                    return reports, round(seconds, 3)

                if iteration % 2 == 0:
                    inspection, combined_seconds = combined_inspection()
                    separate, separate_seconds = individual_inspection()
                else:
                    separate, separate_seconds = individual_inspection()
                    inspection, combined_seconds = combined_inspection()
                assert {key: inspection[key] for key in separate} == separate
                assert inspection["file_hashes"] == references["file_hashes"]
                assert inspection["analysis_context"] == references["analysis_context"]
                assert inspection["location_count"] == separate["definition"]["matches"] + separate["references"]["matches"]
                inspection_samples.append({"combined_seconds": combined_seconds, "individual_seconds": separate_seconds})
            inspection_evidence = {"context_files": len(files), "reports_identical": True,
                "locations": inspection["location_count"], "hover_text_bytes": inspection["hover"]["text_bytes"],
                "combined_requests": 1, "individual_requests": 3, "samples": inspection_samples,
                "combined_median_seconds": statistics.median(item["combined_seconds"] for item in inspection_samples),
                "individual_median_seconds": statistics.median(item["individual_seconds"] for item in inspection_samples)}
            diagnostic_paths = [coordinates, prefix + "edits.rs"]
            diagnostic_samples = []
            for iteration in range(diagnostic_iterations):
                def batch_reports():
                    report = call("ffsake_rust_workspace_diagnostics", {**common,
                        "paths": diagnostic_paths, "limit": 1000})
                    return report, calls[-1]["seconds"]

                def individual_reports():
                    reports = []
                    seconds = 0
                    for path in diagnostic_paths:
                        report = call("ffsake_rust_diagnostics", {**common, "path": path, "limit": 1000})
                        assert report["compiler_checked"] is False
                        reports.append({key: report[key] for key in ("path", "diagnostics", "diagnostic_count", "text_bytes")})
                        seconds += calls[-1]["seconds"]
                    return reports, round(seconds, 3)

                # Alternate request order in the optional repeated measurement.
                if iteration % 2 == 0:
                    batch, batch_seconds = batch_reports()
                    singles, single_seconds = individual_reports()
                else:
                    singles, single_seconds = individual_reports()
                    batch, batch_seconds = batch_reports()
                assert batch["reports"] == singles, "batch differs from individual native reports"
                assert batch["compiler_checked"] is False and batch["file_count"] == len(diagnostic_paths)
                assert batch["diagnostic_count"] == sum(report["diagnostic_count"] for report in singles)
                assert batch["text_bytes"] == sum(report["text_bytes"] for report in singles)
                diagnostic_samples.append({"batch_seconds": batch_seconds, "individual_seconds": single_seconds})
            diagnostic_evidence = {"selected_files": diagnostic_paths, "context_files": len(files),
                "reports_identical": True, "diagnostics": batch["diagnostic_count"], "text_bytes": batch["text_bytes"],
                "batch_requests": 1, "individual_requests": len(diagnostic_paths), "samples": diagnostic_samples,
                "batch_median_seconds": statistics.median(item["batch_seconds"] for item in diagnostic_samples),
                "individual_median_seconds": statistics.median(item["individual_seconds"] for item in diagnostic_samples)}
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
                assert baseline.returncode == 0 and not errors, (errors, baseline.stdout[-16000:], baseline.stderr[-8192:])
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


                # Exercise a compiler-checked project layout change, not just
                # isolated synthetic directory fixtures. One transaction moves
                # the crate and updates its workspace membership.
                old_directory = "crates/fff-workspace"
                new_directory = "crates/workspace-engine"
                relocation = call("ffsake_preview_files", {"operations": [
                    {"op": "move_directory", "from": old_directory, "to": new_directory},
                    {"op": "edit", "path": "Cargo.toml", "old_text": old_directory,
                     "new_text": new_directory, "expected_matches": 1}]})
                assert contents() == original
                relocation_id = call("ffsake_apply", {"handle": relocation["handle"]})["transaction"]
                relocated = {path.replace(old_directory + "/", new_directory + "/", 1)
                    if path.startswith(old_directory + "/") else path: data for path, data in original.items()}
                relocated["Cargo.toml"] = original["Cargo.toml"].replace(old_directory.encode(), new_directory.encode())
                assert contents() == relocated and not (root / old_directory).exists()
                relocated_files = [path.replace(old_directory, new_directory, 1) for path in files]
                relocated_library = library.replace(old_directory, new_directory, 1)
                relocated_context = call("ffsake_rust_context_from_manifests", {
                    "manifests": ["Cargo.toml", new_directory + "/Cargo.toml"],
                    "files": relocated_files, "crate_root": relocated_library})
                relocated_arguments = relocated_context["analysis_arguments"]
                assert set(relocated_context["source_hashes"]) == set(relocated_files)
                assert len(relocated_arguments["crates"]) == len(crates)
                relocated_references = call("ffsake_rust_references", {**relocated_arguments,
                    "path": coordinates.replace(old_directory, new_directory, 1),
                    "position": declaration, "limit": 1000})
                assert relocated_references["matches"] == references["matches"]
                assert all(item["path"].startswith(new_directory + "/")
                           for item in relocated_references["ranges"])
                relocated_test, relocated_errors = cargo_test()
                assert relocated_test.returncode == 0 and not relocated_errors, (
                    relocated_errors, relocated_test.stderr[-8192:])
                assert contents() == relocated
                call("ffsake_undo", {"transaction": relocation_id})
                assert contents() == original and not (root / new_directory).exists()
                call("ffsake_redo", {"transaction": relocation_id})
                assert contents() == relocated and not (root / old_directory).exists()
                call("ffsake_undo", {"transaction": relocation_id})
                assert contents() == original
                restored_test, restored_errors = cargo_test()
                assert restored_test.returncode == 0 and not restored_errors, (
                    restored_errors, restored_test.stderr[-8192:])
                relocation_evidence = {"mixed_operations": 2, "relocated_cargo_tests": "passed",
                    "restored_cargo_tests": "passed", "reimported_crates": len(relocated_arguments["crates"]),
                    "relocated_references": relocated_references["matches"], "byte_history_passed": True}

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
        "context_import": {"manifests": sorted(imported["manifest_hashes"]),
                           "omissions": imported["omissions"], "matches_manual_graph": True},
        "references": references["matches"], "reference_files": len(paths), "outline_symbols": outline["symbol_count"],
        "byte_history_passed": True, "rename_evidence": rename_evidence, "relocation_evidence": relocation_evidence, "diagnostic_evidence": diagnostic_evidence, "inspection_evidence": inspection_evidence, "calls": calls, "model_calls": 0,
        "timing_is_single_model_free_sample": diagnostic_iterations == 1 and inspection_iterations == 1, "complete_analysis": False}, indent=2))


if __name__ == "__main__":
    main()
