#!/usr/bin/env python3
"""Compare imported context with Cargo and exercise its real MCP analysis graph."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    binary = Path(sys.argv[1] if len(sys.argv) > 1 else "target/debug/ffsake-mcp").resolve()
    with tempfile.TemporaryDirectory(prefix="ffsake-manifest-") as temporary:
        base = Path(temporary)
        root = base / "workspace"
        entries = {
            "Cargo.toml": '[workspace]\nmembers=["app","helper"]\nresolver="3"\n[workspace.package]\nedition="2024"\n[workspace.dependencies]\nalias={package="helper",path="helper"}\n',
            "app/Cargo.toml": '[package]\nname="app"\nversion="0.1.0"\nedition.workspace=true\n[dependencies]\nalias.workspace=true\n',
            "helper/Cargo.toml": '[package]\nname="helper"\nversion="0.1.0"\nedition="2021"\n[lib]\nname="custom_helper"\n',
            "app/src/main.rs": 'fn main() { let value = alias::value(); assert_eq!(value, 7); }\n',
            "app/src/lib.rs": 'pub fn local() {}\n',
            "helper/src/lib.rs": 'pub fn value() -> u32 { 7 }\n',
        }
        for path, content in entries.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        sentinel = base / "EXECUTED"
        commands = base / "commands"
        commands.mkdir()
        for name in ("cargo", "rustc", "rust-analyzer"):
            path = commands / name
            path.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n")
            path.chmod(0o755)
        env = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ.get("PATH", "")}

        def call(name, arguments, environment=None, ok=True):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": name, "arguments": arguments}}
            result = subprocess.run([str(binary), str(root)], input=json.dumps(request) + "\n",
                capture_output=True, text=True, env=environment, timeout=40)
            assert result.returncode == 0, result.stderr
            value = json.loads(json.loads(result.stdout)["result"]["content"][0]["text"])
            assert value["ok"] == ok, value
            return value.get("result", value)

        arguments = {"manifests": [p for p in entries if p.endswith("Cargo.toml")],
                     "files": [p for p in entries if p.endswith(".rs")], "crate_root": "app/src/main.rs"}
        report = call("ffsake_rust_context_from_manifests", arguments, env)
        assert not sentinel.exists(), "importer executed a tool"
        assert report["complete"] is False and report["compiler_checked"] is False
        context = report["analysis_arguments"]
        main_crate = next(c for c in context["crates"] if c["root"] == arguments["crate_root"])
        assert main_crate["dependencies"] == {"alias": "helper/src/lib.rs", "app": "app/src/lib.rs"}
        assert main_crate["edition"] == "2024"
        assert all((root / p).read_text() == text for p, text in entries.items())
        assert not (root / "Cargo.lock").exists() and not (root / "target").exists()
        # Cargo is invoked only by this external verification harness, never the importer.
        metadata = subprocess.run(["cargo", "metadata", "--offline", "--format-version", "1"],
            cwd=root, capture_output=True, text=True, timeout=60)
        assert metadata.returncode == 0, metadata.stderr
        metadata = json.loads(metadata.stdout)
        app = next(p for p in metadata["packages"] if p["name"] == "app")
        node = next(n for n in metadata["resolve"]["nodes"] if n["id"] == app["id"])
        assert node["deps"][0]["name"] == "alias"
        checked = subprocess.run(["cargo", "run", "--offline", "--quiet", "-p", "app"],
            cwd=root, capture_output=True, text=True, timeout=60)
        assert checked.returncode == 0, checked.stderr
        definition = call("ffsake_rust_definition", {**context, "path": "app/src/main.rs",
            "position": {"line": 1, "column": entries["app/src/main.rs"].index("value()")}, "limit": 10})
        assert definition["matches"] == 1 and definition["ranges"][0]["path"] == "helper/src/lib.rs", definition
        # Without an explicit package rename, Cargo exposes the custom library name.
        manifest = root / "app/Cargo.toml"
        manifest.write_text('[package]\nname="app"\nversion="0.1.0"\nedition="2024"\n[dependencies]\nhelper={path="../helper"}\n')
        changed = call("ffsake_rust_context_from_manifests", arguments, env)
        main_crate = next(c for c in changed["analysis_arguments"]["crates"] if c["root"] == arguments["crate_root"])
        assert main_crate["dependencies"]["custom_helper"] == "helper/src/lib.rs"
        assert changed["manifest_hashes"] != report["manifest_hashes"]
        metadata = subprocess.run(["cargo", "metadata", "--offline", "--format-version", "1"],
            cwd=root, capture_output=True, text=True, timeout=60)
        assert metadata.returncode == 0, metadata.stderr
        data = json.loads(metadata.stdout)
        node = next(n for n in data["resolve"]["nodes"] if n["id"] == app["id"])
        assert node["deps"][0]["name"] == "custom_helper"
        # Explicit unsafe aliases and oversized manifests fail before analysis.
        (root / "alias.rs").symlink_to(root / "helper/src/lib.rs")
        call("ffsake_rust_context_from_manifests", {**arguments, "files": arguments["files"] + ["alias.rs"]}, env, ok=False)
        manifest.write_text("#" * (64 * 1024 + 1))
        call("ffsake_rust_context_from_manifests", arguments, env, ok=False)
        assert not sentinel.exists()
        print(json.dumps({"ok": True, "imported_crates": 3, "cargo_dependency_names_verified": True,
                          "native_cross_crate_definition": True, "importer_executed_commands": False}))


if __name__ == "__main__":
    main()
