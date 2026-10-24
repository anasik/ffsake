#!/usr/bin/env python3
"""Native rename feasibility: disposable source, raw LSP, no workspace mutations."""
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

from rust_lsp_probe import ProbeClient, byte_offset


def relative_uri(root, uri):
    parsed = urlsplit(uri)
    assert parsed.scheme == "file" and not parsed.netloc and not parsed.query and not parsed.fragment
    path = Path(unquote(parsed.path, errors="strict"))
    relative = path.relative_to(root)
    assert ".." not in relative.parts and relative.suffix == ".rs"
    return str(relative)


def apply_text_proposal(root, sources, proposal):
    changes = proposal["documentChanges"]
    assert isinstance(changes, list) and len(changes) <= 32
    result = dict(sources)
    seen = set()
    edits_count = 0
    for change in changes:
        assert "kind" not in change, "resource operations are never applied by this probe"
        document = change["textDocument"]
        path = relative_uri(root, document["uri"])
        assert path in sources and path not in seen and document["version"] == 1
        seen.add(path)
        edits = []
        for edit in change["edits"]:
            assert edit["newText"] == "customer", edit
            start = byte_offset(sources[path], edit["range"]["start"])
            end = byte_offset(sources[path], edit["range"]["end"])
            assert start < end and sources[path].encode()[start:end] == b"account"
            edits.append((start, end))
        edits.sort()
        assert all(left[1] <= right[0] for left, right in zip(edits, edits[1:]))
        edits_count += len(edits)
        assert edits_count <= 100
        data = sources[path].encode()
        for start, end in reversed(edits):
            data = data[:start] + b"customer" + data[end:]
        result[path] = data.decode()
    return result, edits_count


def check_program(directory, sources, expected):
    directory.mkdir()
    for path, source in sources.items():
        (directory / path).write_bytes(source.encode())
    executable = directory / "fixture"
    compiled = subprocess.run(["rustc", "--edition=2024", "--crate-name", "rename_fixture",
        str(directory / "main.rs"), "-o", str(executable)], capture_output=True, text=True, timeout=30)
    if expected == "compile_error":
        assert compiled.returncode != 0 and "E0425" in compiled.stderr, compiled.stderr
        return
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=5)
    if expected == "runtime_changed":
        assert ran.returncode != 0 and "CHANGED" in ran.stderr, ran.stderr
    else:
        assert expected == "passes" and ran.returncode == 0, ran.stderr


def rename_cases():
    return [
        ("distinct_bindings", "main.rs", 2, "passes", {"main.rs":
            'fn first() -> u32 { let account = 1; account }\n'
            'fn second() -> u32 { let account = 2; account }\n'
            'fn main() { if first() + second() != 3 { panic!("CHANGED"); } }\n'}),
        ("declared_macro", "main.rs", 2, "passes", {"main.rs":
            'macro_rules! id { ($e:expr) => { $e }; }\n'
            'fn main() { let account = 1; if id!(account) != 1 { panic!("CHANGED"); } }\n'}),
        ("omitted_std_macro", "main.rs", 1, "compile_error", {"main.rs":
            'fn main() { let account = 1; assert_eq!(account, 1); }\n'}),
        ("binding_collision", "main.rs", 2, "runtime_changed", {"main.rs":
            'fn main() { let customer = 9; let account = 2; if customer + account != 11 { panic!("CHANGED"); } }\n'}),
        ("cross_file_unicode_crlf", "other.rs", 2, "passes", {
            "main.rs": 'mod other;\r\nfn main() { let _ = "🚀"; if other::account() != 7 { panic!("CHANGED"); } }\r\n',
            "other.rs": 'pub fn account() -> u32 { 7 }\r\n'}),
        ("module_resource_rename", "main.rs", None, "not_applied", {
            "main.rs": 'mod account; fn main() { account::value(); }\n', "account.rs": 'pub fn value() {}\n'}),
    ]


def main():
    cases = rename_cases()
    results = []
    version = subprocess.check_output(["rust-analyzer", "--version"], text=True, timeout=5).strip()
    for name, query, count, outcome, sources in cases:
        with tempfile.TemporaryDirectory(prefix="ffsake-rename-probe-") as temporary:
            base = Path(temporary).resolve()
            root = base / "analysis"
            root.mkdir()
            for path, source in sources.items():
                (root / path).write_bytes(source.encode())
            check_program(base / "before", sources, "passes")
            options = {"linkedProjects": [{"crates": [{"root_module": str(root / "main.rs"),
                "edition": "2024", "deps": [], "cfg": [], "is_workspace_member": True,
                "source": {"include_dirs": [str(root)], "exclude_dirs": []}}]}],
                "cargo": {"buildScripts": {"enable": False}, "sysroot": None, "noDeps": True},
                "procMacro": {"enable": False}, "checkOnSave": False,
                "cachePriming": {"enable": False}, "numThreads": 2}
            client = ProbeClient(root, options)
            try:
                client.request("initialize", {"rootUri": root.as_uri(), "initializationOptions": options,
                    "capabilities": {"general": {"positionEncodings": ["utf-16"]},
                        "textDocument": {"rename": {"prepareSupport": True}, "diagnostic": {}},
                        "workspace": {"workspaceEdit": {"documentChanges": True, "resourceOperations": ["rename"]}},
                        "experimental": {"serverStatusNotification": True}}})
                client.send({"method": "initialized", "params": {}})
                while True:
                    message = client.receive()
                    if message.get("method") == "experimental/serverStatus" and message["params"]["quiescent"]:
                        assert message["params"]["health"] == "ok", message
                        break
                for path, source in sources.items():
                    client.send({"method": "textDocument/didOpen", "params": {"textDocument": {
                        "uri": (root / path).as_uri(), "languageId": "rust", "version": 1, "text": source}}})
                source = sources[query]
                start = source.index("account")
                line = source[:start].count("\n")
                column = len(source[:start].rsplit("\n", 1)[-1].encode("utf-16-le")) // 2
                params = {"textDocument": {"uri": (root / query).as_uri()},
                          "position": {"line": line, "character": column}}
                prepared = client.request("textDocument/prepareRename", params)
                selected_range = prepared.get("range", prepared)
                begin = byte_offset(source, selected_range["start"])
                end = byte_offset(source, selected_range["end"])
                assert source.encode()[begin:end] == b"account"
                proposal = client.request("textDocument/rename", {**params, "newName": "customer"})
            finally:
                client.close()
            assert {p.name: p.read_bytes() for p in root.iterdir()} == {p: s.encode() for p, s in sources.items()}
            if outcome == "not_applied":
                resources = [change for change in proposal["documentChanges"] if "kind" in change]
                assert len(resources) == 1 and resources[0]["kind"] == "rename", proposal
                assert relative_uri(root, resources[0]["oldUri"]) == "account.rs"
                assert relative_uri(root, resources[0]["newUri"]) == "customer.rs"
                assert not (root / "customer.rs").exists()
            else:
                staged, actual_count = apply_text_proposal(root, sources, proposal)
                assert actual_count == count, (name, actual_count, proposal)
                if name == "distinct_bindings":
                    assert 'let account = 2; account' in staged["main.rs"]
                check_program(base / "after", staged, outcome)
            results.append({"case": name, "text_edits": count, "outcome": outcome, "analysis_source_unchanged": True})
    print(json.dumps({"server": version, "cases": results, "production_tool_used": False,
                      "complete_analysis": False, "model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
