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
        with tempfile.TemporaryDirectory(prefix="ffsake-rename-mcp-") as temporary:
            base = Path(temporary)
            root = base / "analysis"
            root.mkdir()
            for path, source in sources.items():
                (root / path).write_bytes(source.encode())
            proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            request_id = 0

            def call(arguments, ok=True):
                nonlocal request_id
                request_id += 1
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                    "params": {"name": "ffsake_rust_rename_proposal", "arguments": arguments}}) + "\n")
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
                check_program(base / "before", sources, "passes")
                if outcome == "not_applied":
                    assert proposal["edits"] is None and "resource_operation" in proposal["unsupported_reasons"]
                    assert not (root / "customer.rs").exists()
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
                        raw = call({**arguments, "new_name": "r#type"})
                        assert all(e["replacement"] == "r#type" for e in raw["proposal"]["edits"]), raw
                    check_program(base / "after", staged, outcome)
                assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == {p: s.encode() for p, s in sources.items()}
                assert not (root / ".ffsake").exists(), "discovery created journal state"
                results.append({"case": name, "external_compiler_outcome": outcome, "source_unchanged": True})
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
    print(json.dumps({"cases": results, "mutation_capability": False, "model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
