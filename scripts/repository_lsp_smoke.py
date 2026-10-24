#!/usr/bin/env python3
"""Full committed-source LSP evaluation in a disposable archive; no model calls."""
import hashlib
import io
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
                    if path.is_file() and ".ffsake" not in path.relative_to(root).parts}

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
        "byte_history_passed": True, "calls": calls, "model_calls": 0,
        "timing_is_single_model_free_sample": True, "complete_analysis": False}, indent=2))


if __name__ == "__main__":
    main()
