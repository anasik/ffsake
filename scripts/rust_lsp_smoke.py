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
        send({"id":message["id"],"result":{"capabilities":{"positionEncoding":"utf-16"}}})
    elif message.get("method") == "initialized":
        send({"method":"experimental/serverStatus","params":{"health":"ok","quiescent":True}})
    elif message.get("method") == "textDocument/references":
        pathlib.Path(os.environ["FFSAKE_LSP_TEST_SOURCE"]).write_text("fn external_change() {}")
        send({"id":message["id"],"result":None})
'''
    for mode in ["malformed", "stale", "timeout"]:
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
                   "FFSAKE_LSP_TEST_SOURCE": str(path)}
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "ffsake_rust_references", "arguments": {"files": ["main.rs"],
                "crate_root": "main.rs", "path": "main.rs", "edition": "2024", "limit": 10,
                "position": {"line": 1, "column": 3}}}}
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
            assert failed["ok"] is False and recovered["ok"] is True, (mode, failed, recovered)
            if mode == "stale":
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

        def call(arguments, ok=True):
            nonlocal request_id
            request_id += 1
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": "ffsake_rust_references", "arguments": arguments}}) + "\n")
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
            first_position = {"line": 3, "column": source.splitlines()[2].index("first")}
            result = call({**args, "position": first_position})
            assert result["matches"] == 3 and {r["path"] for r in result["ranges"]} == {"main.rs", "other.rs"}
            for span in result["ranges"]:
                text = source if span["path"] == "main.rs" else other
                assert text.encode()[span["start_byte"]:span["end_byte"]] == b"first"
            assert (root / "main.rs").read_bytes() == source.encode()
            assert (root / "other.rs").read_text() == other
            assert {p.name for p in root.iterdir()} <= {"main.rs", "other.rs", ".ffsake"}
            base = {**args, "position": first_position}
            for changes in [{"limit": 1}, {"limit": 0}, {"limit": 1001}, {"files": []},
                            {"files": ["main.rs", "./main.rs"]}, {"edition": "2030"},
                            {"path": "missing.rs"}, {"crate_root": "missing.rs"},
                            {"position": {"line": 0, "column": 0}},
                            {"position": {"line": 3, "column": 999}},
                            {"files": ["main.rs", ".ffsake/private.rs"]},
                            {"files": ["main.rs", "../outside.rs"]}]:
                call({**base, **changes}, ok=False)
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
    print("MCP Rust references: binding identity, macro/cross-file ranges, UTF-16/CRLF, boundaries, stale rejection, malformed/timeout recovery and child cleanup PASS")


if __name__ == "__main__":
    main()
