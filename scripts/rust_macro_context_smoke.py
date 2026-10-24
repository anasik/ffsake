#!/usr/bin/env python3
"""Compare omitted and explicit real macro-crate context with external rustc."""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    helper = ('#[macro_export] macro_rules! identity { ($value:expr) => { $value }; }\r\n'
              '#[macro_export] macro_rules! repeat { ($value:expr) => { $value + $value }; }\r\n'
              '#[macro_export] macro_rules! hygiene { ($value:expr) => { { let account = 99u32; let _ = account; $value } }; }\r\n')
    evidence = []
    with tempfile.TemporaryDirectory(prefix="ffsake-macro-context-") as temporary:
        base = Path(temporary)
        root = base / "analysis"
        (root / "helpers").mkdir(parents=True)
        (root / "helpers/lib.rs").write_bytes(helper.encode())
        library = base / "libhelpers.rlib"
        compiled = subprocess.run(["rustc", "--edition=2024", "--crate-name", "helpers", "--crate-type", "rlib",
            str(root / "helpers/lib.rs"), "-o", str(library)], capture_output=True, text=True, timeout=30)
        assert compiled.returncode == 0, compiled.stderr
        proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        request_id = 0

        def call(tool, arguments):
            nonlocal request_id
            request_id += 1
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 35)[0], tool
            response = json.loads(proc.stdout.readline())
            assert response["id"] == request_id, response
            value = json.loads(response["result"]["content"][0]["text"])
            assert value["ok"], value
            return value["result"]

        def compile_main(expect_missing=False):
            checked = subprocess.run(["rustc", "--edition=2024", "--crate-name", "macro_fixture",
                str(root / "main.rs"), "--extern", f"helpers={library}", "-o", str(base / "fixture")],
                capture_output=True, text=True, timeout=30)
            if expect_missing:
                assert checked.returncode != 0 and "E0425" in checked.stderr and "account" in checked.stderr, checked.stderr
            else:
                assert checked.returncode == 0, checked.stderr
                ran = subprocess.run([str(base / "fixture")], capture_output=True, text=True, timeout=5)
                assert ran.returncode == 0, ran.stderr

        try:
            for macro, expected in (("identity", 7), ("repeat", 14), ("hygiene", 7)):
                source = ('fn main() { let _ = "🚀"; let account = 7u32; '
                          f'let result = helpers::{macro}!(account); if result != {expected} {{ panic!("CHANGED"); }} }}\r\n')
                (root / "main.rs").write_bytes(source.encode())
                compile_main()
                for declared in (False, True):
                    crates = [{"root": "main.rs"}]
                    if declared:
                        crates = [{"root": "main.rs", "dependencies": {"helpers": "helpers/lib.rs"}},
                                  {"root": "helpers/lib.rs"}]
                    report = call("ffsake_rust_rename_proposal", {"files": ["main.rs", "helpers/lib.rs"],
                        "crate_root": "main.rs", "path": "main.rs", "edition": "2024", "crates": crates,
                        "position": {"line": 1, "column": source.index("account")}, "new_name": "customer", "limit": 100})
                    assert report["complete"] is False and report["semantic_validated"] is False
                    assert set(report["file_hashes"]) == {"main.rs", "helpers/lib.rs"}
                    proposal = report["proposal"]
                    count = 2 if declared else 1
                    assert proposal["unsupported_reasons"] == [] and proposal["text_edit_count"] == count, (macro, declared, report)
                    assert len(proposal["edits"]) == count
                    assert all(edit["path"] == "main.rs" and edit["replacement"] == "customer" for edit in proposal["edits"])
                    # Even repeated expansions have one edit for the original argument.
                    expected_source = source.replace("account", "customer", count)
                    preview = call("ffsake_preview_rust_rename", {"rename_set": report["rename_set"],
                        "expected_path": "main.rs", "expected_start_byte": report["prepared"]["start_byte"],
                        "expected_old_text": "account", "expected_new_name": "customer", "expected_edits": count})
                    assert preview["analysis_context"] == report["analysis_context"]
                    transaction = call("ffsake_apply", {"handle": preview["handle"]})["transaction"]
                    assert (root / "main.rs").read_bytes() == expected_source.encode()
                    assert (root / "helpers/lib.rs").read_bytes() == helper.encode()
                    compile_main(expect_missing=not declared)
                    call("ffsake_undo", {"transaction": transaction})
                    assert (root / "main.rs").read_bytes() == source.encode()
                    call("ffsake_redo", {"transaction": transaction})
                    assert (root / "main.rs").read_bytes() == expected_source.encode()
                    call("ffsake_undo", {"transaction": transaction})
                    assert (root / "main.rs").read_bytes() == source.encode()
                    assert (root / "helpers/lib.rs").read_bytes() == helper.encode()
                    call("ffsake_release", {"handle": report["rename_set"]})
                    evidence.append({"macro": macro, "declared_dependency": declared, "edits": count,
                                     "compiler_outcome": "passes" if declared else "expected_E0425",
                                     "byte_history_passed": True})
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
    print(json.dumps({"cases": evidence, "model_calls": 0, "complete_analysis": False}, indent=2))


if __name__ == "__main__":
    main()
