#!/usr/bin/env python3
"""Native shorthand renames: exact bytes, independent rustc and guarded history."""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile

from rust_rename_probe import check_program


def cases():
    return [
        ("field", "account", 3, 2,
         'struct Record { account: u32 }\r\n'
         'fn main() { let _ = "🚀"; let account = 7; let record = Record { account }; '
         'let Record { account } = record; if account != 7 { panic!("CHANGED"); } }\r\n',
         'struct Record { customer: u32 }\r\n'
         'fn main() { let _ = "🚀"; let account = 7; let record = Record { customer: account }; '
         'let Record { customer: account } = record; if account != 7 { panic!("CHANGED"); } }\r\n'),
        ("local", "account", 2, 1,
         'fn main() { let _ = "🚀"; let account = 7u32; struct Record { account: u32 } '
         'let record = Record { account }; if record.account != 7 { panic!("CHANGED"); } }\r\n',
         'fn main() { let _ = "🚀"; let customer = 7u32; struct Record { account: u32 } '
         'let record = Record { account: customer }; if record.account != 7 { panic!("CHANGED"); } }\r\n'),
        ("pattern_binding", "account }", 2, 0,
         'struct Record { account: u32 }\r\n'
         'fn main() { let _ = "🚀"; let record = Record { account: 7 }; '
         'let Record { account } = record; if account != 7 { panic!("CHANGED"); } }\r\n',
         'struct Record { account: u32 }\r\n'
         'fn main() { let _ = "🚀"; let record = Record { account: 7 }; '
         'let Record { account: customer } = record; if customer != 7 { panic!("CHANGED"); } }\r\n'),
    ]


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    results = []
    for name, needle, count, insertions, source, expected in cases():
        with tempfile.TemporaryDirectory(prefix="ffsake-shorthand-") as temporary:
            base = Path(temporary)
            root = base / "analysis"
            root.mkdir()
            path = root / "main.rs"
            path.write_bytes(source.encode())
            check_program(base / "before", {"main.rs": source}, "passes")
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

            try:
                start = source.index(needle)
                report = call("ffsake_rust_rename_proposal", {"files": ["main.rs"],
                    "crate_root": "main.rs", "path": "main.rs", "edition": "2024",
                    "position": {"line": source[:start].count("\n") + 1,
                                 "column": len(source[:start].rsplit("\n", 1)[-1])},
                    "new_name": "customer", "limit": count})
                assert report["complete"] is False and report["semantic_validated"] is False
                assert report["compiler_checked"] is False and set(report["file_hashes"]) == {"main.rs"}
                prepared = report["prepared"]
                assert prepared["start_byte"] == len(source[:start].encode())
                assert prepared["old_text"] == "account"
                proposal = report["proposal"]
                assert proposal["unsupported_reasons"] == [] and proposal["text_edit_count"] == count
                edits = sorted(proposal["edits"], key=lambda edit: edit["start_byte"])
                assert len(edits) == count and all(edit["path"] == "main.rs" for edit in edits)
                assert sum(edit["start_byte"] == edit["end_byte"] for edit in edits) == insertions
                assert all(a["end_byte"] <= b["start_byte"] for a, b in zip(edits, edits[1:]))
                staged = source.encode()
                for edit in reversed(edits):
                    assert 0 <= edit["start_byte"] <= edit["end_byte"] <= len(source.encode())
                    staged = staged[:edit["start_byte"]] + edit["replacement"].encode() + staged[edit["end_byte"]:]
                assert staged == expected.encode(), (name, staged)
                assert path.read_bytes() == source.encode()
                preview = call("ffsake_preview_rust_rename", {"rename_set": report["rename_set"],
                    "expected_path": "main.rs", "expected_start_byte": prepared["start_byte"],
                    "expected_old_text": "account", "expected_new_name": "customer", "expected_edits": count})
                assert preview["compiler_checked"] is False and preview["semantic_validated"] is False
                assert path.read_bytes() == source.encode()
                transaction = call("ffsake_apply", {"handle": preview["handle"]})["transaction"]
                assert path.read_bytes() == expected.encode()
                check_program(base / "after", {"main.rs": path.read_bytes().decode()}, "passes")
                call("ffsake_undo", {"transaction": transaction})
                assert path.read_bytes() == source.encode()
                call("ffsake_redo", {"transaction": transaction})
                assert path.read_bytes() == expected.encode()
                call("ffsake_undo", {"transaction": transaction})
                assert path.read_bytes() == source.encode()
                call("ffsake_release", {"handle": report["rename_set"]})
                results.append({"case": name, "edits": count, "insertions": insertions,
                                "exact_bytes_and_history": True, "external_compiler_and_runtime": "passed"})
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
    print(json.dumps({"cases": results, "model_calls": 0, "complete_analysis": False}, indent=2))


if __name__ == "__main__":
    main()
