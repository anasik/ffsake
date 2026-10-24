#!/usr/bin/env python3
"""Compiler/runtime evidence for native action limits on disposable fixtures."""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    cases = [
        ("type_annotation", "count", "Insert explicit type `u32`", "passes", "passes", '''fn main() { let count = 1_u32; assert_eq!(count, 1); }
'''),
        ("constant_shadowing", "count", "Promote local to constant", "passes", "global_changed", '''const COUNT: u32 = 10;
fn main() { let count = 1_u32; if COUNT != 10 { panic!("GLOBAL_CHANGED"); } if count != 1 { panic!("LOCAL_CHANGED"); } }
'''),
        ("opaque_macro_reference", "count", "Promote local to constant", "passes", "unresolved_reference", '''fn main() { let count = 1_u32; assert_eq!(count, 1); }
'''),
        ("generated_placeholder", "S {}", "Fill struct fields", "missing_field", "placeholder_panic", '''struct S { x: u32 }
fn main() { let value = S {}; if value.x != 0 { panic!("VALUE_CHANGED"); } }
'''),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix="ffsake-action-evidence-") as temp:
        root = Path(temp) / "workspace"
        root.mkdir()
        path = root / "main.rs"
        executable = Path(temp) / "fixture"
        proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        request_id = 0

        def call(name, arguments):
            nonlocal request_id
            request_id += 1
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                                         "params": {"name": name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 35)[0], f"MCP timeout: {name}"
            response = json.loads(proc.stdout.readline())
            assert response["id"] == request_id, response
            value = json.loads(response["result"]["content"][0]["text"])
            assert value["ok"], value
            return value["result"]

        def compile_and_run(expected):
            compiled = subprocess.run(["rustc", "--edition=2024", "--crate-name", "action_fixture",
                                       str(path), "-o", str(executable)], capture_output=True, text=True, timeout=30)
            error_code = {"missing_field": "E0063", "unresolved_reference": "E0425"}.get(expected)
            if error_code:
                assert compiled.returncode != 0 and error_code in compiled.stderr, compiled.stderr
                return
            assert compiled.returncode == 0, compiled.stderr
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=5)
            marker = {"global_changed": "GLOBAL_CHANGED", "placeholder_panic": "not yet implemented"}.get(expected)
            if marker:
                assert ran.returncode != 0 and marker in ran.stderr, ran.stderr
            else:
                assert expected == "passes" and ran.returncode == 0, ran.stderr

        try:
            for name, selection, title, before, after, source in cases:
                path.write_bytes(source.encode())
                compile_and_run(before)
                start = source.index(selection)
                line = source[:start].count("\n") + 1
                column = len(source[:start].rsplit("\n", 1)[-1])
                report = call("ffsake_rust_code_actions", {"files": ["main.rs"], "crate_root": "main.rs",
                    "path": "main.rs", "edition": "2024", "limit": 100,
                    "range": {"start": {"line": line, "column": column},
                              "end": {"line": line, "column": column + len(selection)}}})
                candidates = [(index, action) for index, action in enumerate(report["actions"])
                              if action["title"] == title]
                assert len(candidates) == 1, (name, report)
                index, action = candidates[0]
                assert action["unsupported_reasons"] == [], action
                preview = call("ffsake_preview_rust_action", {"action_set": report["action_set"],
                    "action_index": index, "expected_title": title, "expected_edits": action["text_edit_count"]})
                assert preview["semantic_validated"] is False and path.read_bytes() == source.encode()
                transaction = call("ffsake_apply", {"handle": preview["handle"]})["transaction"]
                changed = path.read_bytes()
                assert changed != source.encode()
                compile_and_run(after)
                call("ffsake_undo", {"transaction": transaction})
                assert path.read_bytes() == source.encode()
                compile_and_run(before)
                call("ffsake_redo", {"transaction": transaction})
                assert path.read_bytes() == changed
                compile_and_run(after)
                call("ffsake_undo", {"transaction": transaction})
                assert path.read_bytes() == source.encode()
                call("ffsake_release", {"handle": report["action_set"]})
                results.append({"case": name, "action": title, "before": before, "after": after,
                                "parser_checked_preview": True, "byte_history_passed": True})
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
    print(json.dumps({"cases": results, "model_calls": 0, "semantic_equivalence_guaranteed": False}, indent=2))


if __name__ == "__main__":
    main()
