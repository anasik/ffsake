#!/usr/bin/env python3
"""Verify critical snapshot settings against a controlled ambient RA config.

The upstream test-only config-directory hook is used only by this regression,
never by production. No actual user configuration is read or changed here.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from rust_lsp_probe import ProbeClient


def calibrate(root, config, sources):
    """Prove this analyzer actually reads the temporary user-config fixture."""
    options = {"linkedProjects": [{"crates": [{"root_module": str(root / "main.rs"),
        "edition": "2024", "deps": [], "cfg": [], "is_workspace_member": True}]}],
        "cargo": {"buildScripts": {"enable": False}, "sysroot": None},
        "procMacro": {"enable": False}, "checkOnSave": False, "numThreads": 2,
        "diagnostics": {"enable": True, "experimental": {"enable": True}}}
    previous = os.environ.get("__TEST_RA_USER_CONFIG_DIR")
    os.environ["__TEST_RA_USER_CONFIG_DIR"] = str(config)
    try:
        client = ProbeClient(root, options)
    finally:
        if previous is None:
            del os.environ["__TEST_RA_USER_CONFIG_DIR"]
        else:
            os.environ["__TEST_RA_USER_CONFIG_DIR"] = previous
    try:
        client.request("initialize", {"rootUri": root.as_uri(), "initializationOptions": options,
            "capabilities": {"textDocument": {"diagnostic": {}},
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
        report = client.request("textDocument/diagnostic", {"textDocument": {"uri": (root / "main.rs").as_uri()}})
        assert report["items"] == [], "calibration must reproduce inherited E0308 suppression"
    finally:
        client.close()


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    with tempfile.TemporaryDirectory(prefix="ffsake-ambient-config-") as temporary:
        base = Path(temporary).resolve()
        root = base / "workspace"
        root.mkdir()
        config = base / "user-config"
        config.mkdir()
        config_file = config / "rust-analyzer.toml"
        sources = {"main.rs": 'mod model; fn main() { let _value: u32 = model::value(); }',
                   "model.rs": '#[cfg(hidden_flag)] pub fn value() -> u32 { 7 }\n'
                               '#[cfg(not(hidden_flag))] pub fn value() -> bool { true }\n'}
        for path, source in sources.items():
            (root / path).write_bytes(source.encode())
        config_file.write_text('[diagnostics]\ndisabled = ["E0308"]\n')
        calibrate(root, config, sources)
        sentinel = base / "COMMAND_RAN"
        command = json.dumps([sys.executable, "-c", f"from pathlib import Path; Path({str(sentinel)!r}).write_text('ran')"])
        settings = ('[diagnostics]\nenable = false\ndisabled = ["E0308"]\n'
                    '[diagnostics.experimental]\nenable = false\n'
                    '[procMacro]\nenable = true\n'
                    '[files]\nexclude = ["model.rs"]\n'
                    '[workspace.discoverConfig]\ncommand = ' + command + '\n'
                    'progressLabel = "controlled config probe"\nfilesToWatch = ["main.rs"]\n')
        config_file.write_text(settings)
        env = {**os.environ, "__TEST_RA_USER_CONFIG_DIR": str(config)}
        common = {"files": list(sources), "crate_root": "main.rs", "path": "main.rs", "edition": "2024", "limit": 100}
        requests = [{"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {
            "name": name, "arguments": arguments}} for index, (name, arguments) in enumerate([
                ("ffsake_rust_diagnostics", common),
                ("ffsake_rust_definition", {**common, "position": {"line": 1, "column": sources["main.rs"].index("value()")}}),
            ], 1)]
        run = subprocess.run([str(binary), str(root)], input="".join(json.dumps(r) + "\n" for r in requests),
                             capture_output=True, text=True, env=env, timeout=70)
        assert run.returncode == 0, run.stderr
        responses = [json.loads(line) for line in run.stdout.splitlines()]
        assert len(responses) == len(requests), responses
        results = []
        for response, request in zip(responses, requests):
            assert response["id"] == request["id"]
            value = json.loads(response["result"]["content"][0]["text"])
            assert value["ok"], value
            results.append(value["result"])
        diagnostics, definition = results
        assert any(item["code"] == "E0308" for item in diagnostics["diagnostics"]), diagnostics
        assert definition["matches"] == 1 and definition["ranges"][0]["path"] == "model.rs"
        assert definition["ranges"][0]["line"] == 2, definition
        assert not sentinel.exists(), "ambient command was executed"
        assert config_file.read_text() == settings
        # Workspace-only cfg settings are invalid in the global config. Preserve
        # the health failure instead of weakening readiness to tolerate them.
        config_file.write_text('[cargo]\ncfgs = ["hidden_flag"]\n')
        rejected = subprocess.run([str(binary), str(root)], input=json.dumps(requests[0]) + "\n",
                                  capture_output=True, text=True, env=env, timeout=35)
        assert rejected.returncode == 0, rejected.stderr
        response = json.loads(rejected.stdout)
        value = json.loads(response["result"]["content"][0]["text"])
        assert value["ok"] is False and "not healthy" in value["error"], value
        assert not sentinel.exists()
        for path, source in sources.items():
            assert (root / path).read_bytes() == source.encode()
    print("Rust ambient config: calibrated exclusion leak, pinned diagnostics/scope, invalid config rejected, no discovery commands, unchanged config/source PASS")


if __name__ == "__main__":
    main()
