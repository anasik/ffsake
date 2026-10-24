#!/usr/bin/env python3
"""Calibrated source-loading checks, not an OS sandbox or read-access audit."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    marker = "HiddenTypeMarker"
    payload = f"pub struct {marker}; pub fn secret() -> {marker} {{ {marker} }}"
    with tempfile.TemporaryDirectory(prefix="ffsake-source-scope-") as temporary:
        base = Path(temporary).resolve()
        root = base / "workspace"
        root.mkdir()
        external = base / "outside.rs"
        model = root / "model.rs"
        for path in (external, model):
            path.write_bytes(payload.encode())
        # Put analyzer snapshots beside the external sentinel so ../outside.rs
        # really names an existing file from the disposable snapshot as well.
        proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env={**os.environ, "TMPDIR": str(base)})
        request_id = 0

        def call(name, files, source, selection):
            nonlocal request_id
            request_id += 1
            arguments = {"files": files, "crate_root": "main.rs", "path": "main.rs",
                         "edition": "2024", "position": {"line": 1, "column": source.index(selection)}}
            arguments["max_bytes" if name == "hover" else "limit"] = 4096 if name == "hover" else 100
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": "ffsake_rust_" + name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 35)[0], f"MCP timeout: {name}"
            response = json.loads(proc.stdout.readline())
            assert response["id"] == request_id, response
            value = json.loads(response["result"]["content"][0]["text"])
            assert value["ok"], value
            report = value["result"]
            assert report["complete"] is False and set(report["file_hashes"]) == set(files), report
            return report

        checked = []
        try:
            for kind in ("module", "include", "include_str"):
                for case, target, supplied in (
                    ("explicit_relative", "model.rs", True),
                    ("omitted_relative", "model.rs", False),
                    ("external_absolute", str(external), False),
                    ("external_escape", "../outside.rs", False),
                    ("original_absolute", str(model), True),
                ):
                    literal = json.dumps(target)
                    if kind == "module":
                        source = f"#[path = {literal}] mod external; fn main() {{ let result = external::secret(); let _ = result; }}"
                        selection = "result"
                    else:
                        # Declare analyzer-recognized builtins without a sysroot.
                        # The explicit-file positive controls prove expansion works.
                        source = f"#[rustc_builtin_macro] macro_rules! {kind} {{ ($file:expr) => {{}} }} "
                        if kind == "include":
                            source += f"include!({literal}); fn main() {{ let result = secret(); let _ = result; }}"
                            selection = "result"
                        else:
                            source += f"const VALUE: &str = include_str!({literal}); fn main() {{}}"
                            selection = "VALUE"
                    (root / "main.rs").write_bytes(source.encode())
                    files = ["main.rs", "model.rs"] if supplied else ["main.rs"]
                    report = call("hover", files, source, selection)
                    visible = json.dumps(report["hover"])
                    expected = case == "explicit_relative"
                    assert (marker in visible) == expected, (kind, case, report)
                    if kind != "include_str":
                        definition = call("definition", files, source, "secret()")
                        if expected:
                            assert definition["matches"] == 1, definition
                            location = definition["ranges"][0]
                            assert location["path"] == "model.rs", location
                            assert payload.encode()[location["start_byte"]:location["end_byte"]] == b"secret", location
                        else:
                            assert definition["matches"] == 0, (kind, case, definition)
                    assert (root / "main.rs").read_bytes() == source.encode()
                    for path in (external, model):
                        assert path.read_bytes() == payload.encode()
                    checked.append(f"{kind}/{case}")
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
    print(json.dumps({"cases": checked, "positive_controls": 3,
                      "sources_unchanged": True, "os_isolation_verified": False}, indent=2))


if __name__ == "__main__":
    main()
