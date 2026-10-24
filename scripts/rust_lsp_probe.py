#!/usr/bin/env python3
"""Bounded, read-only rust-analyzer feasibility check on a disposable fixture.

Requires rustup component add rust-analyzer. This is not a user-facing LSP tool.
The fixture uses an explicit project graph, without Cargo or a sysroot.
"""
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
from urllib.parse import unquote, urlsplit


class ProbeClient:
    def __init__(self, root, options):
        self.options = options
        self.deadline = time.monotonic() + 30
        self.buffer = bytearray()
        self.received = 0
        self.next_id = 1
        self.proc = subprocess.Popen(["rust-analyzer"], cwd=root, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env={**os.environ, "CARGO_NET_OFFLINE": "true"})

    def close(self):
        # Always bound shutdown, including assertion/protocol failures.
        self.proc.kill()
        self.proc.wait(timeout=5)
        self.proc.stdin.close()
        self.proc.stdout.close()

    def send(self, message):
        data = json.dumps({"jsonrpc": "2.0", **message}).encode()
        self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.proc.stdin.flush()

    def read(self):
        while True:
            end = self.buffer.find(b"\r\n\r\n")
            if end >= 0:
                assert end <= 8192, "oversized LSP headers"
                headers = {}
                for line in bytes(self.buffer[:end]).split(b"\r\n"):
                    key, value = line.split(b":", 1)
                    key = key.lower()
                    assert key not in headers, "duplicate LSP header"
                    headers[key] = value.strip()
                length = int(headers[b"content-length"])
                assert 0 < length <= 1024 * 1024, "oversized LSP frame"
                if len(self.buffer) >= end + 4 + length:
                    message = json.loads(self.buffer[end + 4:end + 4 + length])
                    del self.buffer[:end + 4 + length]
                    return message
            else:
                assert len(self.buffer) <= 8192, "oversized LSP headers"
            remaining = self.deadline - time.monotonic()
            assert remaining > 0, "LSP deadline exceeded"
            assert select.select([self.proc.stdout], [], [], remaining)[0], "LSP timeout"
            data = os.read(self.proc.stdout.fileno(), 65536)
            assert data, "unexpected LSP EOF"
            self.received += len(data)
            assert self.received <= 8 * 1024 * 1024, "LSP response budget exceeded"
            self.buffer.extend(data)

    def receive(self):
        message = self.read()
        if "method" in message and "id" in message:
            if message["method"] == "workspace/configuration":
                result = [self.options for _ in message["params"]["items"]]
                self.send({"id": message["id"], "result": result})
            elif message["method"] == "window/workDoneProgress/create":
                self.send({"id": message["id"], "result": None})
            else:
                # Never execute commands or apply server-proposed edits.
                self.send({"id": message["id"], "error": {
                    "code": -32601, "message": "unsupported client request"}})
        return message

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == request_id and "method" not in message:
                assert "error" not in message, message
                return message["result"]


def byte_offset(source, position):
    """Strict LSP UTF-16 -> original UTF-8 byte offset, preserving CRLF."""
    line, column = position["line"], position["character"]
    assert type(line) is int and type(column) is int and line >= 0 and column >= 0
    lines = source.split("\n")
    assert line < len(lines), "line outside document"
    text = lines[line].removesuffix("\r")
    units = 0
    for index, char in enumerate(text):
        if units == column:
            return len(("\n".join(lines[:line]) + ("\n" if line else "") + text[:index]).encode())
        units += len(char.encode("utf-16-le")) // 2
        assert units <= column, "position splits a UTF-16 surrogate pair"
    assert units == column, "column outside line"
    return len(("\n".join(lines[:line]) + ("\n" if line else "") + text).encode())


def scoped_ranges(root, source, locations):
    assert isinstance(locations, list) and len(locations) <= 100
    ranges = []
    for location in locations:
        uri = urlsplit(location["uri"])
        assert uri.scheme == "file" and not uri.netloc and not uri.query and not uri.fragment
        path = Path(unquote(uri.path, errors="strict"))
        relative = path.relative_to(root)
        assert ".." not in relative.parts and ".ffsake" not in relative.parts
        candidate = root
        for part in relative.parts:
            candidate /= part
            assert not candidate.is_symlink(), "symlink LSP result"
        assert path.resolve() == root / "main.rs", "unexpected result file"
        start = byte_offset(source, location["range"]["start"])
        end = byte_offset(source, location["range"]["end"])
        assert start < end and source.encode()[start:end] == b"account"
        ranges.append((start, end))
    return sorted(set(ranges))


def main():
    version = subprocess.check_output(["rust-analyzer", "--version"], text=True, timeout=5).strip()
    with tempfile.TemporaryDirectory(prefix="ffsake-lsp-probe-") as temp:
        root = (Path(temp) / "workspace with spaces é").resolve()
        root.mkdir()
        path = root / "main.rs"
        source = ("macro_rules! identity { ($e:expr) => { $e }; }\r\n"
                  'fn first() -> i32 { let _ = "🚀"; let account = 1; account }\r\n'
                  "fn second() -> i32 { let account = 2; identity!(account) }\r\n"
                  "fn main() { first(); second(); }\r\n")
        path.write_bytes(source.encode())
        # Discovery must not load this Cargo project or execute its build script.
        (root / "Cargo.toml").write_text('[package]\nname="probe"\nversion="0.1.0"\nedition="2024"\n'
                                         '[[bin]]\nname="probe"\npath="main.rs"\n')
        (root / "build.rs").write_text('fn main() { std::fs::write("BUILD_SCRIPT_RAN", "bad").unwrap(); }')
        original = {p.name: p.read_bytes() for p in root.iterdir()}
        options = {
            "linkedProjects": [{"crates": [{"root_module": str(path), "edition": "2024",
                                            "deps": [], "cfg": [], "is_workspace_member": True}]}],
            "cargo": {"buildScripts": {"enable": False}, "noDeps": True, "sysroot": None},
            "procMacro": {"enable": False}, "checkOnSave": False,
            "cachePriming": {"enable": False}, "numThreads": 2,
        }
        client = ProbeClient(root, options)
        results = []
        try:
            initialized = client.request("initialize", {"processId": None, "rootUri": root.as_uri(),
                "capabilities": {"general": {"positionEncodings": ["utf-16"]},
                                 "experimental": {"serverStatusNotification": True}},
                "initializationOptions": options})
            assert initialized["capabilities"].get("positionEncoding", "utf-16") == "utf-16"
            client.send({"method": "initialized", "params": {}})
            while True:
                message = client.receive()
                if message.get("method") == "experimental/serverStatus" and message["params"]["quiescent"]:
                    assert message["params"]["health"] == "ok", message
                    break
            client.send({"method": "textDocument/didOpen", "params": {"textDocument": {
                "uri": path.as_uri(), "languageId": "rust", "version": 1, "text": source}}})
            for line, label in [(1, "first_binding"), (2, "declarative_macro_binding")]:
                text = source.split("\n")[line]
                column = len(text[:text.index("account")].encode("utf-16-le")) // 2
                locations = client.request("textDocument/references", {"textDocument": {"uri": path.as_uri()},
                    "position": {"line": line, "character": column}, "context": {"includeDeclaration": True}})
                ranges = scoped_ranges(root, source, locations)
                line_start = len("\n".join(source.split("\n")[:line]).encode()) + 1
                expected = [(line_start + len(text[:offset].encode()),
                             line_start + len(text[:offset].encode()) + 7)
                            for offset in range(len(text)) if text.startswith("account", offset)]
                assert ranges == expected, (label, ranges, expected)
                results.append({"case": label, "references_including_declaration": len(ranges)})
            client.request("shutdown", None)
            client.send({"method": "exit"})
        finally:
            client.close()
        assert {p.name: p.read_bytes() for p in root.iterdir()} == original
    print(json.dumps({"server": version, "encoding": "utf-16", "cases": results,
                      "fixture_unchanged": True, "project_mode": "explicit_graph_without_dependencies",
                      "model_calls": 0, "production_lsp_tool": False}, indent=2))


if __name__ == "__main__":
    main()
