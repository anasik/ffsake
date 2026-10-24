#!/usr/bin/env python3
"""Launch an interactive Claude session with only the FFSAKE MCP tools."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from agent_benchmark_suite import FFSAKE_MCP, ffsake_prompt, mcp_arguments, mcp_profile_preflight, profile_tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--binary", type=Path, default=FFSAKE_MCP)
    parser.add_argument("--claude", default="claude", help="Claude executable name or path")
    parser.add_argument("--tool-profile", choices=("all", "core"), default="all",
                        help="Full catalog (default) or generic file tools only")
    parser.add_argument("--dry-run", action="store_true", help="Print configuration without launching Claude")
    args = parser.parse_args()
    root = args.workspace.expanduser().resolve()
    binary = args.binary.expanduser().resolve()
    if not root.is_dir():
        parser.error("workspace must be an existing directory")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("build the MCP binary first: cargo build --locked --bin ffsake-mcp")
    executable = shutil.which(args.claude)
    if executable is None:
        parser.error("Claude executable not found")
    if args.tool_profile == "core" and not args.dry_run:
        readiness = mcp_profile_preflight("core", binary)
        if not readiness["ok"]:
            parser.error("core MCP preflight failed before launching Claude: " + readiness["error"])
    config = {"mcpServers": {"ffsake": {"command": str(binary), "args": mcp_arguments(root, args.tool_profile)}}}
    allowed = [f"mcp__ffsake__{tool}" for tool in profile_tools(args.tool_profile)]
    with tempfile.TemporaryDirectory(prefix="ffsake-claude-") as td:
        config_path = Path(td) / "mcp.json"
        config_path.write_text(json.dumps(config))
        command = [
            executable, "--strict-mcp-config", "--mcp-config", str(config_path),
            "--tools", "", "--allowedTools", ",".join(allowed),
            "--append-system-prompt", ffsake_prompt("", args.tool_profile).strip(),
        ]
        if args.dry_run:
            print(json.dumps({"workspace": str(root), "mcp_config": config,
                              "tool_profile": args.tool_profile, "approved_tools": allowed, "command": command}, indent=2))
            return 0
        env = os.environ.copy()
        env["PWD"] = str(root)
        try:
            return subprocess.call(command, cwd=root, env=env)
        except KeyboardInterrupt:
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
