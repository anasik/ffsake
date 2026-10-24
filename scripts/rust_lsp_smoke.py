#!/usr/bin/env python3
"""Exercise the read-only Rust references MCP endpoint with real rust-analyzer."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile


def failure_checks(binary):
    """A controlled server proves stale-source rejection and bounded shutdown."""
    fake_server = '''#!/usr/bin/env python3
import json, os, pathlib, sys, time
pathlib.Path(os.environ["FFSAKE_LSP_TEST_PID"]).write_text(str(os.getpid()))
mode = os.environ["FFSAKE_LSP_TEST_MODE"]
if mode == "timeout":
    time.sleep(60)
    sys.exit(0)
if mode == "malformed":
    sys.stdout.buffer.write(b"Content-Length: 999999999\\r\\n\\r\\n")
    sys.stdout.buffer.flush()
    time.sleep(60)
    sys.exit(0)
def send(value):
    data = json.dumps({"jsonrpc":"2.0", **value}).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\\r\\n\\r\\n".encode()+data)
    sys.stdout.buffer.flush()
diagnostic_requests = 0
while True:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line: sys.exit(0)
        if line == b"\\r\\n": break
        key, value = line.split(b":", 1)
        headers[key.lower()] = value.strip()
    message = json.loads(sys.stdin.buffer.read(int(headers[b"content-length"])))
    if message.get("method") == "initialize":
        capabilities = {"positionEncoding":"utf-16", "diagnosticProvider":{}}
        if mode != "rename_unsupported": capabilities["renameProvider"] = {"prepareProvider":True}
        send({"id":message["id"],"result":{"capabilities":capabilities}})
    elif message.get("method") == "initialized":
        send({"method":"experimental/serverStatus","params":{"health":"ok","quiescent":True}})
    elif mode.startswith("inspect_") and message.get("method") in ("textDocument/definition", "textDocument/references", "textDocument/hover"):
        diagnostic_requests += 1
        pathlib.Path(os.environ["FFSAKE_LSP_TEST_REQUESTS"]).write_text(str(diagnostic_requests))
        location = {"uri":message["params"]["textDocument"]["uri"], "range":{"start":{"line":0,"character":3},"end":{"line":0,"character":7}}}
        if message["method"] == "textDocument/definition":
            result = [location, location] if mode == "inspect_duplicate_limit" else location
        elif message["method"] == "textDocument/references":
            result = [location]
        else:
            if mode == "inspect_stale":
                pathlib.Path(os.environ["FFSAKE_LSP_TEST_SOURCE"]).write_text("fn external_change() {}")
            result = {"contents":{"kind":"html","value":"bad"}} if mode == "inspect_bad_hover" else {"contents":"x" * 8193} if mode == "inspect_hover_overflow" else None
        send({"id":message["id"],"result":result})
    elif message.get("method") == "textDocument/diagnostic":
        diagnostic_requests += 1
        pathlib.Path(os.environ["FFSAKE_LSP_TEST_REQUESTS"]).write_text(str(diagnostic_requests))
        if mode == "batch_stale":
            pathlib.Path(os.environ["FFSAKE_LSP_TEST_SOURCE"]).write_text("fn external_change() {}")
        result = {"kind":"unchanged"} if mode == "batch_bad_second" and diagnostic_requests == 2 else {"kind":"full","items":[]}
        send({"id":message["id"],"result":result})
    elif message.get("method") == "textDocument/prepareRename":
        if mode == "rename_null":
            send({"id":message["id"],"result":None})
        else:
            send({"id":message["id"],"result":{"start":{"line":0,"character":3},"end":{"line":0,"character":999 if mode == "rename_bad_range" else 7}}})
    elif message.get("method") == "textDocument/rename":
        pathlib.Path(os.environ["FFSAKE_LSP_TEST_SOURCE"]).write_text("fn external_change() {}")
        send({"id":message["id"],"result":None})
    elif mode == "backend_error" and message.get("method") == "textDocument/references":
        send({"id":message["id"],"error":{"code":-32602,"message":"unsupported fixture query","data":{"private":"hidden backend detail"}}})
    elif message.get("method") == "textDocument/references":
        pathlib.Path(os.environ["FFSAKE_LSP_TEST_SOURCE"]).write_text("fn external_change() {}")
        send({"id":message["id"],"result":None})
'''
    for mode in ["malformed", "stale", "timeout", "rename_stale", "rename_null", "rename_bad_range", "rename_unsupported", "batch_stale", "batch_bad_second", "inspect_stale", "inspect_bad_hover", "inspect_hover_overflow", "inspect_limit", "inspect_duplicate_limit", "backend_error"]:
        with tempfile.TemporaryDirectory(prefix="ffsake-lsp-failure-") as temp:
            directory = Path(temp)
            root = directory / "workspace"
            root.mkdir()
            path = root / "main.rs"
            path.write_text("fn main() {}")
            server = directory / "rust-analyzer"
            server.write_text(fake_server)
            server.chmod(0o755)
            pid_file = directory / "server.pid"
            env = {**os.environ, "PATH": str(directory) + os.pathsep + os.environ.get("PATH", ""),
                   "FFSAKE_LSP_TEST_PID": str(pid_file), "FFSAKE_LSP_TEST_MODE": mode,
                   "FFSAKE_LSP_TEST_SOURCE": str(path), "FFSAKE_LSP_TEST_REQUESTS": str(directory / "requests")}
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "ffsake_rust_references", "arguments": {"files": ["main.rs"],
                "crate_root": "main.rs", "path": "main.rs", "edition": "2024", "limit": 10,
                "position": {"line": 1, "column": 3}}}}
            if mode.startswith("rename_"):
                request["params"]["name"] = "ffsake_rust_rename_proposal"
                request["params"]["arguments"]["new_name"] = "renamed"
            if mode.startswith("batch_"):
                (root / "empty.rs").write_text("")
                request["params"]["name"] = "ffsake_rust_workspace_diagnostics"
                request["params"]["arguments"].update(files=["main.rs", "empty.rs"])
                del request["params"]["arguments"]["path"]
                del request["params"]["arguments"]["position"]
                if mode == "batch_stale":
                    request["params"]["arguments"]["paths"] = ["empty.rs"]
            if mode.startswith("inspect_"):
                (root / "context.rs").write_text("fn context() {}")
                request["params"]["name"] = "ffsake_rust_inspect"
                request["params"]["arguments"].update(files=["main.rs", "context.rs"])
                if mode in ("inspect_limit", "inspect_duplicate_limit"):
                    request["params"]["arguments"]["limit"] = 1 if mode == "inspect_limit" else 2
                if mode == "inspect_stale":
                    env["FFSAKE_LSP_TEST_SOURCE"] = str(root / "context.rs")
            # A second ordinary request verifies resident recovery after the failure.
            read = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "ffsake_read", "arguments": {"path": "main.rs"}}}
            run = subprocess.run([str(binary), str(root)], input=json.dumps(request) + "\n" + json.dumps(read) + "\n",
                                 capture_output=True, text=True, env=env, timeout=38)
            assert run.returncode == 0, run.stderr
            responses = [json.loads(line) for line in run.stdout.splitlines()]
            assert len(responses) == 2, responses
            failed = json.loads(responses[0]["result"]["content"][0]["text"])
            recovered = json.loads(responses[1]["result"]["content"][0]["text"])
            assert failed["ok"] == (mode == "rename_null") and recovered["ok"] is True, (mode, failed, recovered)
            if mode == "backend_error":
                assert failed["error"] == 'rust-analyzer rejected textDocument/references (-32602): "unsupported fixture query"'
                assert "hidden backend detail" not in json.dumps(failed)
                assert not (root / ".ffsake").exists()
            if mode == "rename_null":
                assert failed["result"]["prepared"] is None and failed["result"]["proposal"] is None, failed
            if mode.startswith("batch_"):
                assert (directory / "requests").read_text() == ("1" if mode == "batch_stale" else "2")
                assert "result" not in failed
            if mode.startswith("inspect_"):
                assert (directory / "requests").read_text() == ("2" if mode in ("inspect_limit", "inspect_duplicate_limit") else "3")
                assert "result" not in failed and not (root / ".ffsake").exists()
                if mode == "inspect_stale":
                    assert "source changed" in failed["error"]
                    assert (root / "context.rs").read_text() == "fn external_change() {}"
                else:
                    assert (root / "context.rs").read_text() == "fn context() {}"
            if mode in ("stale", "rename_stale", "batch_stale"):
                assert "source changed" in failed["error"], failed
                assert path.read_text() == "fn external_change() {}"
            else:
                assert path.read_text() == "fn main() {}"
            pid = int(pid_file.read_text())
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError(f"{mode}: language server survived failed request")


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    with tempfile.TemporaryDirectory(prefix="ffsake-lsp-mcp-") as temp:
        root = Path(temp) / "workspace é"
        root.mkdir()
        source = ("mod other;\r\n"
                  "macro_rules! identity { ($e:expr) => { $e }; }\r\n"
                  'pub fn first() -> i32 { let _ = "🚀"; let account = 1; account }\r\n'
                  "fn second() -> i32 { let account = 2; identity!(account) }\r\n"
                  "fn main() { first(); second(); other::call(); }\r\n")
        other = "pub fn call() -> i32 { crate::first() }\n"
        (root / "main.rs").write_bytes(source.encode())
        (root / "other.rs").write_text(other)
        proc = subprocess.Popen([str(binary), str(root)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        request_id = 0

        def call(arguments, ok=True, name="ffsake_rust_references"):
            nonlocal request_id
            request_id += 1
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}) + "\n")
            proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 35)[0], "MCP references timed out"
            response = json.loads(proc.stdout.readline())
            value = json.loads(response["result"]["content"][0]["text"])
            assert response["id"] == request_id and value["ok"] is ok, value
            return value.get("result")

        args = {"files": ["main.rs", "other.rs"], "crate_root": "main.rs", "path": "main.rs",
                "edition": "2024", "limit": 100}
        try:
            for line in [3, 4]:
                column = source.splitlines()[line - 1].index("account")
                result = call({**args, "position": {"line": line, "column": column}})
                assert result["matches"] == 2 and result["complete"] is False
                assert "handle" not in result and set(result["file_hashes"]) == {"main.rs", "other.rs"}
                for span in result["ranges"]:
                    assert span["path"] == "main.rs" and span["line"] == line
                    assert source.encode()[span["start_byte"]:span["end_byte"]] == b"account"
                definition = call({**args, "position": {"line": line,
                    "column": source.splitlines()[line - 1].rindex("account")}}, name="ffsake_rust_definition")
                assert definition["matches"] == 1 and definition["query"] == "definition"
                span = definition["ranges"][0]
                line_start = len("\n".join(source.split("\n")[:line - 1]).encode()) + 1
                assert span["start_byte"] == line_start + len(source.splitlines()[line - 1][:column].encode())
                assert span["line"] == line and span["path"] == "main.rs"
            first_position = {"line": 3, "column": source.splitlines()[2].index("first")}
            result = call({**args, "position": first_position})
            assert result["matches"] == 3 and {r["path"] for r in result["ranges"]} == {"main.rs", "other.rs"}
            for span in result["ranges"]:
                text = source if span["path"] == "main.rs" else other
                assert text.encode()[span["start_byte"]:span["end_byte"]] == b"first"
            definition = call({**args, "path": "other.rs", "position": {
                "line": 1, "column": other.index("first")}}, name="ffsake_rust_definition")
            assert definition["matches"] == 1 and definition["complete"] is False and "handle" not in definition
            span = definition["ranges"][0]
            assert span["path"] == "main.rs" and span["line"] == 3
            assert source.encode()[span["start_byte"]:span["end_byte"]] == b"first"
            assert (root / "main.rs").read_bytes() == source.encode()
            assert (root / "other.rs").read_text() == other
            assert {p.name for p in root.iterdir()} <= {"main.rs", "other.rs", ".ffsake"}
            base = {**args, "position": first_position}
            inspection = call({**base, "limit": 4}, name="ffsake_rust_inspect")
            assert inspection["query"] == "inspect" and inspection["complete"] is False
            assert inspection["compiler_checked"] is False and "handle" not in inspection
            assert inspection["location_count"] == 4
            assert inspection["definition"] == {key: definition[key] for key in ("ranges", "matches")}
            assert inspection["references"] == {key: result[key] for key in ("ranges", "matches")}
            hover_args = {key: value for key, value in base.items() if key != "limit"}
            hover = call({**hover_args, "max_bytes": 8192}, name="ffsake_rust_hover")
            assert inspection["hover"] == {key: hover[key] for key in ("path", "hover", "text_bytes")}
            assert inspection["file_hashes"] == result["file_hashes"] == hover["file_hashes"]
            assert inspection["analysis_context"] == result["analysis_context"]
            for changes in ({"limit": 3}, {"limit": 0}, {"limit": 1001}, {"files": []},
                            {"files": ["main.rs", "./main.rs"]}, {"path": "../outside.rs"}):
                call({**base, **changes}, ok=False, name="ffsake_rust_inspect")
            for changes in [{"limit": 1}, {"limit": 0}, {"limit": 1001}, {"files": []},
                            {"files": ["main.rs", "./main.rs"]}, {"edition": "2030"},
                            {"path": "missing.rs"}, {"crate_root": "missing.rs"},
                            {"position": {"line": 0, "column": 0}},
                            {"position": {"line": 3, "column": 999}},
                            {"files": ["main.rs", ".ffsake/private.rs"]},
                            {"files": ["main.rs", "../outside.rs"]}]:
                call({**base, **changes}, ok=False)
            call({**base, "path": "other.rs", "files": ["main.rs"]}, ok=False, name="ffsake_rust_definition")
            (root / "alias.rs").symlink_to(root / "main.rs")
            call({**base, "files": ["main.rs", "alias.rs"]}, ok=False)
            (root / "alias.rs").unlink()
            # Each query sees fresh source rather than a cached prior analysis.
            source = source.replace("account", "balance")
            (root / "main.rs").write_bytes(source.encode())
            result = call({**args, "position": {"line": 3, "column": source.splitlines()[2].index("balance")}})
            assert result["matches"] == 2
            for span in result["ranges"]:
                assert source.encode()[span["start_byte"]:span["end_byte"]] == b"balance"
            assert (root / "main.rs").read_bytes() == source.encode()
            # Definitions can legitimately point at the beginning of an empty module.
            source = "mod empty;\nfn main() {}\n"
            (root / "main.rs").write_text(source)
            (root / "empty.rs").write_text("")
            empty = call({**args, "files": ["main.rs", "empty.rs"],
                "position": {"line": 1, "column": 4}}, name="ffsake_rust_definition")
            assert empty["ranges"] == [{"path": "empty.rs", "start_byte": 0, "end_byte": 0, "line": 1}]
            assert (root / "main.rs").read_text() == source and (root / "empty.rs").read_bytes() == b""
            empty_inspection = call({**base, "files": ["empty.rs"], "crate_root": "empty.rs",
                "path": "empty.rs", "position": {"line": 1, "column": 0}}, name="ffsake_rust_inspect")
            assert empty_inspection["location_count"] == 0
            assert empty_inspection["definition"]["ranges"] == empty_inspection["references"]["ranges"] == []
            assert empty_inspection["hover"]["hover"] is None
            source = ("/* 🚀 */ mod nested {\r\n"
                      "    pub struct Account { pub amount: i32 }\r\n"
                      "    impl Account { pub fn value(&self) -> i32 { self.amount } }\r\n"
                      "}\r\nfn main() {}\r\n")
            (root / "main.rs").write_bytes(source.encode())
            outline_args = {**args, "files": ["main.rs"]}
            outline = call(outline_args, name="ffsake_rust_symbols")
            assert outline["query"] == "symbols" and outline["complete"] is False and "handle" not in outline
            assert outline["symbol_count"] == 6 and outline["path"] == "main.rs", outline
            nested, main_symbol = outline["symbols"]
            assert nested["name"] == "nested" and nested["kind_name"] == "module"
            assert main_symbol["name"] == "main" and main_symbol["kind_name"] == "function"
            account, implementation = nested["children"]
            assert account["name"] == "Account" and account["kind_name"] == "struct"
            field = account["children"][0]
            method = implementation["children"][0]
            assert field["name"] == "amount" and field["kind_name"] == "field"
            assert method["name"] == "value" and method["kind_name"] == "method"
            for symbol in [nested, main_symbol, account, field, method]:
                span = symbol["selection"]
                assert source.encode()[span["start_byte"]:span["end_byte"]].decode() == symbol["name"]
            for changes in [{"limit": 5}, {"limit": 0}, {"limit": 1001},
                            {"position": {"line": 1, "column": 0}}]:
                call({**outline_args, **changes}, ok=False, name="ffsake_rust_symbols")
            assert (root / "main.rs").read_bytes() == source.encode()
            empty_outline = call({**outline_args, "files": ["empty.rs"], "crate_root": "empty.rs",
                                  "path": "empty.rs"}, name="ffsake_rust_symbols")
            assert empty_outline["symbol_count"] == 0 and empty_outline["symbols"] == []
            source = ("mod other;\n"
                      "pub trait Value { fn value(&self) -> i32; }\n"
                      "struct A;\n"
                      "impl Value for A { fn value(&self) -> i32 { 1 } }\n"
                      "pub struct B;\nstruct Unused;\nfn main() {}\n")
            other = "use crate::{Value, B};\nimpl Value for B { fn value(&self) -> i32 { 2 } }\n"
            (root / "main.rs").write_text(source)
            (root / "other.rs").write_text(other)
            trait_position = {"line": 2, "column": source.splitlines()[1].index("Value")}
            implementations = call({**args, "position": trait_position}, name="ffsake_rust_implementation")
            assert implementations["matches"] == 2 and implementations["query"] == "implementation"
            assert implementations["complete"] is False and "handle" not in implementations
            def selected_text(result):
                return [(source if span["path"] == "main.rs" else other).encode()[
                    span["start_byte"]:span["end_byte"]].decode() for span in result["ranges"]]
            assert set(selected_text(implementations)) == {"A", "B"}
            assert {r["path"] for r in implementations["ranges"]} == {"main.rs", "other.rs"}
            methods = call({**args, "position": {"line": 2, "column": source.splitlines()[1].index("value")}},
                           name="ffsake_rust_implementation")
            assert selected_text(methods) == ["value", "value"]
            single = call({**args, "position": {"line": 3, "column": 7}}, name="ffsake_rust_implementation")
            assert selected_text(single) == ["A"]
            absent = call({**args, "position": {"line": 6, "column": 7}}, name="ffsake_rust_implementation")
            assert absent["matches"] == 0 and absent["ranges"] == []
            call({**args, "position": trait_position, "limit": 1}, ok=False, name="ffsake_rust_implementation")
            assert (root / "main.rs").read_text() == source and (root / "other.rs").read_text() == other
            source = ("/// Converts a local value.\r\n"
                      "fn convert(value: u32) -> u64 { value as u64 }\r\n"
                      'fn main() { let _ = "🚀"; let amount = 7_u32; let _ = convert(amount); }\r\n')
            (root / "main.rs").write_bytes(source.encode())
            hover_args = {key: value for key, value in args.items() if key != "limit"}
            hover_args.update(files=["main.rs"], max_bytes=4096)
            amount_position = {"line": 3, "column": source.splitlines()[2].rindex("amount")}
            hovered = call({**hover_args, "position": amount_position}, name="ffsake_rust_hover")
            assert hovered["query"] == "hover" and hovered["complete"] is False and "handle" not in hovered
            contents = hovered["hover"]["contents"]
            assert "u32" in "\n".join(part["text"] for part in contents)
            assert hovered["text_bytes"] == sum(len(part["text"].encode()) for part in contents)
            span = hovered["hover"]["range"]
            assert source.encode()[span["start_byte"]:span["end_byte"]] == b"amount"
            documented = call({**hover_args, "position": {"line": 3,
                "column": source.splitlines()[2].index("convert")}}, name="ffsake_rust_hover")
            text = "\n".join(part["text"] for part in documented["hover"]["contents"])
            assert "Converts a local value." in text and "u64" in text
            for changes in [{"max_bytes": 1}, {"max_bytes": 0}, {"max_bytes": 65537}, {"limit": 10}]:
                call({**hover_args, "position": amount_position, **changes}, ok=False, name="ffsake_rust_hover")
            empty_hover = call({**hover_args, "files": ["empty.rs"], "crate_root": "empty.rs",
                "path": "empty.rs", "position": {"line": 1, "column": 0}}, name="ffsake_rust_hover")
            assert empty_hover["hover"] is None and empty_hover["text_bytes"] == 0
            assert (root / "main.rs").read_bytes() == source.encode()
            diagnostic_args = {**args, "files": ["main.rs"]}
            # A dormant user build script must never run during native diagnostics.
            (root / "Cargo.toml").write_text('[package]\nname="unused"\nversion="0.1.0"\n')
            (root / "build.rs").write_text('fn main() { std::fs::write("BUILD_SCRIPT_RAN", "bad").unwrap(); }')
            source = 'fn main( {'
            (root / "main.rs").write_text(source)
            syntax = call(diagnostic_args, name="ffsake_rust_diagnostics")
            assert syntax["diagnostic_count"] >= 2 and syntax["query"] == "diagnostics", syntax
            assert all(item["code"] == "syntax-error" and item["category"] == "syntax"
                       for item in syntax["diagnostics"]), syntax
            assert any(item["range"]["start_byte"] == item["range"]["end_byte"]
                       for item in syntax["diagnostics"])
            for changes in [{"limit": 1}, {"limit": 0}, {"limit": 1001},
                            {"position": {"line": 1, "column": 0}}]:
                call({**diagnostic_args, **changes}, ok=False, name="ffsake_rust_diagnostics")
            source = 'mod other;\r\nfn main() { let _ = "🚀"; let number: u32 = true; let _ = number; }\r\n'
            other = 'pub fn value() -> u32 { false }\r\n'
            (root / "main.rs").write_bytes(source.encode())
            (root / "other.rs").write_bytes(other.encode())
            for path, content, token in [("main.rs", source, b"true"), ("other.rs", other, b"false")]:
                result = call({**args, "path": path}, name="ffsake_rust_diagnostics")
                assert result["complete"] is False and result["compiler_checked"] is False
                assert result["experimental_diagnostics"] is True and "handle" not in result
                assert set(result["file_hashes"]) == {"main.rs", "other.rs"}
                mismatch = [item for item in result["diagnostics"] if item["code"] == "E0308"]
                assert len(mismatch) == 1, result
                span = mismatch[0]["range"]
                assert content.encode()[span["start_byte"]:span["end_byte"]] == token
                assert mismatch[0]["category"] == "semantic" and mismatch[0]["severity"] == 1
            (root / "main.rs").write_text('use absent_dependency::Thing; fn main() {}')
            missing = call(diagnostic_args, name="ffsake_rust_diagnostics")
            assert any(item["code"] == "E0432" for item in missing["diagnostics"]), missing
            source = 'fn main() { let number: u32 = 1; let _ = number; }\r\n'
            (root / "main.rs").write_bytes(source.encode())
            clean = call(diagnostic_args, name="ffsake_rust_diagnostics")
            assert clean["diagnostic_count"] == 0 and clean["diagnostics"] == [], clean
            assert clean["complete"] is False and clean["compiler_checked"] is False
            assert (root / "main.rs").read_bytes() == source.encode()
            assert not (root / "BUILD_SCRIPT_RAN").exists()
            source = 'fn main() { let _ = "🚀"; let count = 1_u32; assert_eq!(count, 1); }\r\n'
            (root / "main.rs").write_bytes(source.encode())
            column = source.index("count")
            selection = {"start": {"line": 1, "column": column},
                         "end": {"line": 1, "column": column + len("count")}}
            action_args = {**diagnostic_args, "files": ["main.rs", "other.rs"], "range": selection}
            discovered = call(action_args, name="ffsake_rust_code_actions")
            assert discovered["query"] == "code_actions" and discovered["complete"] is False
            assert "handle" not in discovered and discovered["action_count"] >= 2
            annotation = [item for item in discovered["actions"] if item["title"] == "Insert explicit type `u32`"]
            assert len(annotation) == 1 and annotation[0]["unsupported_reasons"] == [], discovered
            edits = annotation[0]["edits"]
            assert len(edits) == 1 and edits[0]["path"] == "main.rs"
            updated = source.encode()
            for edit in sorted(edits, key=lambda e: (e["start_byte"], e["end_byte"]), reverse=True):
                updated = updated[:edit["start_byte"]] + edit["replacement"].encode() + updated[edit["end_byte"]:]
            assert updated == source.replace("count =", "count: u32 =", 1).encode()
            # Compilation/execution is an external fixture verifier, never an MCP action.
            with tempfile.TemporaryDirectory(prefix="ffsake-action-compile-") as compiled:
                executable = Path(compiled) / "fixture"
                checked = subprocess.run(["rustc", "--edition=2024", "-o", str(executable), "-"],
                                         input=updated, capture_output=True, timeout=20)
                assert checked.returncode == 0, checked.stderr.decode()
                checked = subprocess.run([str(executable)], capture_output=True, timeout=5)
                assert checked.returncode == 0, checked.stderr.decode()
            assert (root / "main.rs").read_bytes() == source.encode()
            action_index = discovered["actions"].index(annotation[0])
            preview_args = {"action_set": discovered["action_set"], "action_index": action_index,
                            "expected_title": annotation[0]["title"], "expected_edits": len(edits)}
            call({"handle": discovered["action_set"]}, ok=False, name="ffsake_apply")
            for changes in [{"expected_title": "different action"}, {"expected_edits": len(edits) + 1},
                            {"action_index": 99}, {"replacement": "untrusted caller edit"}]:
                call({**preview_args, **changes}, ok=False, name="ffsake_preview_rust_action")
            previewed = call(preview_args, name="ffsake_preview_rust_action")
            assert previewed["semantic_validated"] is False and previewed["preview"]["changed_files"] == 1
            assert ": u32" in previewed["preview"]["files"][0]["unified_diff"]
            assert (root / "main.rs").read_bytes() == source.encode()
            # A change to an unedited input invalidates both discovery and its frozen preview.
            before_other = (root / "other.rs").read_bytes()
            (root / "other.rs").write_bytes(before_other + b"\n")
            call(preview_args, ok=False, name="ffsake_preview_rust_action")
            call({"handle": previewed["handle"]}, ok=False, name="ffsake_apply")
            assert (root / "main.rs").read_bytes() == source.encode()
            (root / "other.rs").write_bytes(before_other)
            assert call({"handle": discovered["action_set"]}, name="ffsake_release")["released"]
            call(preview_args, ok=False, name="ffsake_preview_rust_action")
            applied = call({"handle": previewed["handle"]}, name="ffsake_apply")
            assert applied["changed_files"] == 1 and (root / "main.rs").read_bytes() == updated
            call({"handle": previewed["handle"]}, ok=False, name="ffsake_apply")
            call({"transaction": applied["transaction"]}, name="ffsake_undo")
            assert (root / "main.rs").read_bytes() == source.encode()
            call({"transaction": applied["transaction"]}, name="ffsake_redo")
            assert (root / "main.rs").read_bytes() == updated
            call({"transaction": applied["transaction"]}, name="ffsake_undo")
            assert (root / "main.rs").read_bytes() == source.encode()
            for changes in [{"limit": 1}, {"limit": 0}, {"limit": 101},
                            {"range": {"start": selection["end"], "end": selection["start"]}},
                            {"range": {"start": {"line": 1, "column": 9999}, "end": selection["end"]}},
                            {"position": selection["start"]}]:
                call({**action_args, **changes}, ok=False, name="ffsake_rust_code_actions")
            source = 'mod inline { pub fn value() {} } fn main() {}'
            (root / "main.rs").write_text(source)
            resource = call({**action_args, "range": {"start": {"line": 1, "column": 4},
                "end": {"line": 1, "column": 10}}}, name="ffsake_rust_code_actions")
            module_actions = [item for item in resource["actions"] if item["title"] == "Extract module to file"]
            assert len(module_actions) == 1 and module_actions[0]["edits"] is None, resource
            assert "resource_operation" in module_actions[0]["unsupported_reasons"]
            call({"action_set": resource["action_set"], "action_index": resource["actions"].index(module_actions[0]),
                  "expected_title": module_actions[0]["title"], "expected_edits": module_actions[0]["text_edit_count"]},
                 ok=False, name="ffsake_preview_rust_action")
            assert call({"handle": resource["action_set"]}, name="ffsake_release")["released"]
            assert (root / "main.rs").read_text() == source and not (root / "inline.rs").exists()
            assert not (root / "BUILD_SCRIPT_RAN").exists()
            (root / "app").mkdir()
            (root / "model").mkdir()
            app = ('use account_model::value;\r\n'
                   'fn main() { let _ = "🚀"; let amount = value(); let expected: u32 = amount; let _ = expected; }\r\n')
            model = 'pub fn value() -> u32 { 7 }\r\n'
            (root / "app/main.rs").write_bytes(app.encode())
            (root / "model/lib.rs").write_bytes(model.encode())
            project = {"files": ["app/main.rs", "model/lib.rs"], "crate_root": "app/main.rs",
                       "path": "app/main.rs", "edition": "2024", "limit": 100}
            absent_edge = call(project, name="ffsake_rust_diagnostics")
            assert any(item["code"] == "E0432" for item in absent_edge["diagnostics"]), absent_edge
            crates = [{"root": "app/main.rs", "dependencies": {"account_model": "model/lib.rs"}},
                      {"root": "model/lib.rs", "edition": "2021"}]
            project["crates"] = crates
            checked = call(project, name="ffsake_rust_diagnostics")
            assert checked["diagnostic_count"] == 0 and checked["analysis_mode"] == "explicit_project_snapshot", checked
            assert checked["complete"] is False and set(checked["file_hashes"]) == set(project["files"])
            references = call({**project, "path": "model/lib.rs", "position": {"line": 1, "column": 7}})
            assert references["matches"] == 3 and {item["path"] for item in references["ranges"]} == set(project["files"])
            definition = call({**project, "position": {"line": 2, "column": app.splitlines()[1].index("value")}},
                              name="ffsake_rust_definition")
            assert definition["matches"] == 1 and definition["ranges"][0]["path"] == "model/lib.rs"
            amount = app.splitlines()[1].index("amount")
            proposed = call({**project, "range": {"start": {"line": 2, "column": amount},
                "end": {"line": 2, "column": amount + 6}}}, name="ffsake_rust_code_actions")
            candidates = [(index, item) for index, item in enumerate(proposed["actions"])
                          if item["title"] == "Insert explicit type `u32`"]
            assert len(candidates) == 1, proposed
            index, item = candidates[0]
            select_action = {"action_set": proposed["action_set"], "action_index": index,
                             "expected_title": item["title"], "expected_edits": item["text_edit_count"]}
            staged = call(select_action, name="ffsake_preview_rust_action")
            (root / "model/lib.rs").write_text('pub fn value() -> bool { true }\n')
            fresh = call(project, name="ffsake_rust_diagnostics")
            assert any(item["code"] == "E0308" for item in fresh["diagnostics"]), fresh
            call(select_action, ok=False, name="ffsake_preview_rust_action")
            call({"handle": staged["handle"]}, ok=False, name="ffsake_apply")
            assert (root / "app/main.rs").read_bytes() == app.encode()
            (root / "model/lib.rs").write_bytes(model.encode())
            applied = call({"handle": staged["handle"]}, name="ffsake_apply")
            assert (root / "app/main.rs").read_bytes() == app.replace("amount =", "amount: u32 =", 1).encode()
            call({"transaction": applied["transaction"]}, name="ffsake_undo")
            assert (root / "app/main.rs").read_bytes() == app.encode()
            call({"handle": proposed["action_set"]}, name="ffsake_release")
            for invalid in [crates + [crates[0]], [crates[1]],
                            [{"root": "app/main.rs", "dependencies": {"model": "missing.rs"}}],
                            [{"root": "app/main.rs", "dependencies": {"cycle": "app/main.rs"}}],
                            [{"root": "app/main.rs", "command": "never execute"}],
                            [{"root": "../outside.rs"}], crates * 9]:
                call({**project, "crates": invalid}, ok=False, name="ffsake_rust_diagnostics")
            assert not (root / "BUILD_SCRIPT_RAN").exists()
            model = ('#[cfg(all(feature = "fast", local_mode))] pub fn value() -> u32 { 7 }\r\n'
                     '#[cfg(not(all(feature = "fast", local_mode)))] pub fn value() -> bool { true }\r\n')
            (root / "model/lib.rs").write_bytes(model.encode())
            configured = [dict(crate) for crate in crates]
            configured[1].update(features=["fast"], cfg_flags=["local_mode"])
            for graph, expected_line, mismatch in [(crates, 2, True),
                    ([crates[0], {**crates[1], "features": ["fast"]}], 2, True), (configured, 1, False)]:
                analyzed = call({**project, "crates": graph}, name="ffsake_rust_diagnostics")
                assert any(item["code"] == "E0308" for item in analyzed["diagnostics"]) is mismatch, analyzed
                definition = call({**project, "crates": graph,
                    "position": {"line": 2, "column": app.splitlines()[1].index("value")}}, name="ffsake_rust_definition")
                assert definition["matches"] == 1 and definition["ranges"][0]["line"] == expected_line
            action_range = {"start": {"line": 2, "column": amount}, "end": {"line": 2, "column": amount + 6}}
            active = call({**project, "crates": configured, "range": action_range}, name="ffsake_rust_code_actions")
            inactive = call({**project, "crates": crates, "range": action_range}, name="ffsake_rust_code_actions")
            assert any(item["title"] == "Insert explicit type `bool`" for item in inactive["actions"]), inactive
            index = next(i for i, item in enumerate(active["actions"]) if item["title"] == "Insert explicit type `u32`")
            item = active["actions"][index]
            staged = call({"action_set": active["action_set"], "action_index": index,
                           "expected_title": item["title"], "expected_edits": item["text_edit_count"]},
                          name="ffsake_preview_rust_action")
            assert staged["analysis_context"] == active["analysis_context"]
            assert staged["analysis_context"]["crates"][1]["features"] == ["fast"]
            assert staged["analysis_context"]["crates"][1]["cfg_flags"] == ["local_mode"]
            assert (root / "app/main.rs").read_bytes() == app.encode()
            call({"handle": staged["handle"]}, name="ffsake_release")
            for report in [active, inactive]:
                call({"handle": report["action_set"]}, name="ffsake_release")
            for changes in [{"features": ["fast", "fast"]}, {"features": ["dep/fast"]},
                            {"cfg_flags": ["unix"]}, {"cfg_flags": ["target_os"]},
                            {"cfg_flags": ["local_mode", "local_mode"]}, {"cfg": ["anything"]}]:
                call({**project, "crates": [crates[0], {**crates[1], **changes}]},
                     ok=False, name="ffsake_rust_diagnostics")
            batch_sources = {
                "main.rs": 'mod other; mod empty; fn main() { let _ = "🚀"; let _value: u32 = other::value(); }\r\n',
                "other.rs": 'pub fn value() -> bool { let _bad: u32 = false; true }\r\n',
                "empty.rs": "",
            }
            for path, content in batch_sources.items():
                (root / path).write_bytes(content.encode())
            batch_args = {"files": list(batch_sources), "crate_root": "main.rs", "edition": "2024", "limit": 100}
            batch = call(batch_args, name="ffsake_rust_workspace_diagnostics")
            assert batch["file_count"] == 3 and batch["diagnostic_count"] >= 2, batch
            assert batch["complete"] is False and batch["compiler_checked"] is False
            assert set(batch["file_hashes"]) == set(batch_sources) and "handle" not in batch
            assert [item["path"] for item in batch["reports"]] == sorted(batch_sources)
            assert batch["diagnostic_count"] == sum(item["diagnostic_count"] for item in batch["reports"])
            assert batch["text_bytes"] == sum(item["text_bytes"] for item in batch["reports"])
            for item in batch["reports"]:
                single = call({**batch_args, "path": item["path"]}, name="ffsake_rust_diagnostics")
                assert item == {key: single[key] for key in ("path", "diagnostics", "diagnostic_count", "text_bytes")}
                if item["path"] == "empty.rs":
                    assert item["diagnostic_count"] == 0
                else:
                    assert any(d["code"] == "E0308" for d in item["diagnostics"])
            selected = call({**batch_args, "paths": ["other.rs"]}, name="ffsake_rust_workspace_diagnostics")
            assert selected["file_count"] == 1 and selected["reports"] == [item for item in batch["reports"] if item["path"] == "other.rs"]
            assert selected["file_hashes"] == batch["file_hashes"]
            for changes in ({"limit": 1}, {"limit": 0}, {"limit": 1001}, {"files": []},
                            {"files": ["main.rs", "missing.rs"]}, {"path": "main.rs"}, {"paths": []},
                            {"paths": ["other.rs", "other.rs"]}, {"paths": ["omitted.rs"]}):
                call({**batch_args, **changes}, ok=False, name="ffsake_rust_workspace_diagnostics")
            batch_sources["other.rs"] = 'pub fn value() -> u32 { 7 }\r\n'
            (root / "other.rs").write_bytes(batch_sources["other.rs"].encode())
            clean = call(batch_args, name="ffsake_rust_workspace_diagnostics")
            assert clean["diagnostic_count"] == 0 and clean["file_count"] == 3, clean
            assert clean["file_hashes"]["other.rs"] != batch["file_hashes"]["other.rs"]
            assert all((root / path).read_bytes() == content.encode() for path, content in batch_sources.items())
            assert not (root / "BUILD_SCRIPT_RAN").exists()
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
    failure_checks(binary)
    print("MCP Rust navigation: references/definitions/implementations, combined inspection, symbols, hover/types, single/workspace native diagnostics, code-action discovery/guarded previews/history, explicit crate graph/features/cfg, UTF-16/CRLF, bounds, stale rejection, malformed/timeout recovery and child cleanup PASS")


if __name__ == "__main__":
    main()
