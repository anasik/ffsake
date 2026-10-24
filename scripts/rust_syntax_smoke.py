#!/usr/bin/env python3
"""Compiler-backed syntax-edit evidence; no models, network or user-source edits."""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[1]
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else repo / "target/debug/ffsake-mcp").resolve()
    cases = [
        ("ordinary_binding", 2, "passes", '''fn total() -> i32 { let account = 3; account + 2 }
fn main() { assert_eq!(total(), 5); }
'''),
        ("unrelated_bindings", 4, "passes", '''fn first() -> i32 { let account = 1; account }
fn second() -> i32 { let account = 2; account }
fn main() { assert_eq!(first() + second(), 3); }
'''),
        ("opaque_macro_reference", 1, "compile_error", '''fn main() {
    let account = 7;
    println!("{}", account);
}
'''),
        ("binding_collision", 2, "runtime_error", '''fn main() {
    let customer = 9;
    let account = 2;
    let sum = customer + account;
    assert_eq!(sum, 11);
}
'''),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix="ffsake-rust-syntax-") as temp:
        root = Path(temp) / "workspace"
        root.mkdir()
        executable = Path(temp) / "probe"
        proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        request_id = 0

        def call(name, arguments):
            nonlocal request_id
            request_id += 1
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                                         "params": {"name": name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 30)[0], f"MCP timeout: {name}"
            response = json.loads(proc.stdout.readline())
            assert response["id"] == request_id, response
            value = json.loads(response["result"]["content"][0]["text"])
            assert value["ok"], value
            return value["result"]

        def compile_and_run(expected):
            compiled = subprocess.run(["rustc", "--edition=2024", "--crate-name", "syntax_probe",
                                       str(root / "main.rs"), "-o", str(executable)],
                                      capture_output=True, text=True, timeout=30)
            if expected == "compile_error":
                assert compiled.returncode != 0 and "E0425" in compiled.stderr, compiled.stderr
                return
            assert compiled.returncode == 0, compiled.stderr
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=5)
            if expected == "runtime_error":
                assert ran.returncode != 0 and "assertion" in ran.stderr, ran.stderr
            else:
                assert ran.returncode == 0, ran.stderr

        try:
            for name, count, outcome, source in cases:
                path = root / "main.rs"
                path.write_bytes(source.encode())
                compile_and_run("passes")
                selected = call("ffsake_select_rust_identifiers", {
                    "paths": ["main.rs"], "identifier": "account", "limit": 100})
                assert selected["matches"] == count, (name, selected)
                preview = call("ffsake_preview_matches", {"handle": selected["handle"],
                    "replacement": "customer", "expected_matches": count})
                assert path.read_bytes() == source.encode(), name
                transaction = call("ffsake_apply", {"handle": preview["handle"]})["transaction"]
                changed = path.read_bytes()
                if name == "unrelated_bindings":
                    assert changed.count(b"let customer") == 2
                if name == "opaque_macro_reference":
                    assert b'println!("{}", account)' in changed
                compile_and_run(outcome)
                call("ffsake_undo", {"transaction": transaction})
                assert path.read_bytes() == source.encode(), name
                compile_and_run("passes")
                call("ffsake_redo", {"transaction": transaction})
                assert path.read_bytes() == changed, name
                compile_and_run(outcome)
                call("ffsake_undo", {"transaction": transaction})
                assert path.read_bytes() == source.encode(), name
                call("ffsake_release", {"handle": selected["handle"]})
                results.append({"case": name, "matches": count, "after_apply": outcome,
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
        assert proc.returncode == 0, proc.returncode
    print(json.dumps({"cases": results, "model_calls": 0, "semantic_rename": False}, indent=2))


if __name__ == "__main__":
    main()
