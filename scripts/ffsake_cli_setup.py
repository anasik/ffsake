#!/usr/bin/env python3
"""Install-free, CLI-first host integration for FFSAKE.

Writes one project guidance block and, optionally, a read-redirect integration
that serves native reads through ffsake: a Claude Code PreToolUse hook or an
OpenCode local plugin. No MCP server entry, tool catalog or per-tool approval
list is created.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

START = "<!-- ffsake-cli:start -->"
END = "<!-- ffsake-cli:end -->"


def guidance(binary):
    return f"""{START}
## FFSAKE CLI

Use the guarded FFSAKE CLI through Bash for scoped repository reads and one-call
debugging. The host needs one allowlist rule for the CLI (for Claude Code:
`Bash(ffsake:*)`). With the read-redirect hook (below), native Read is served by
ffsake automatically and needs no extra rule.

Use the absolute path, a literal `.` for the workspace, and no shell variables
or quotes. The host read block rejects commands it cannot parse.

```sh
{binary} read . main.py:10-40 tests/test_main.py:1-80
{binary} probe . --debug .ffsake/rc-debug.json --config main --breakpoint main.py:12 --stops 2
```

Replace the example paths and line number with the real ones; do not copy them
literally.

Combine simple commands without quotes or variables in one Bash call, for
example `git ls-files && python3 main.py`. On debugger tasks, always run one
probe before editing so the required runtime values are observed. Make ordinary
code changes with native Edit/Write; that is faster than reviewed CLI edits.
FFSAKE still enforces workspace policy, stale-state checks, history and debug
cleanup for the operations it runs. Never bypass an access denial.
{END}"""


def hook_settings(binary):
    hook = Path(__file__).resolve().with_name("claude_read_redirect_hook.py")
    return {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Read",
                "hooks": [{
                    "type": "command",
                    "command": f"FFSAKE_BIN={binary} {os.sys.executable} {hook}",
                }],
            }]
        },
        # Three entries replace the native read/glob/grep surface. Native Read
        # is still available to the model; the hook serves it through ffsake.
        "permissions": {"allow": ["Bash", "Edit", "Write"]},
    }


def opencode_plugin_source(project, binary):
    """Render the read-redirect plugin with a workspace-unique id."""
    template = Path(__file__).with_name("opencode_read_redirect_plugin.js").read_text()
    digest = hashlib.sha256(str(project).encode()).hexdigest()[:12]
    plugin_id = f"ffsake-read-redirect-{digest}"
    source = (template
              .replace('"__FFSAKE_BIN__"', json.dumps(str(binary)))
              .replace('"__FFSAKE_ROOT__"', json.dumps(str(project)))
              .replace('"__FFSAKE_PLUGIN_ID__"', json.dumps(plugin_id)))
    return plugin_id, source


def install_opencode_policy(project, binary):
    """Install the local read plugin and a permission-minimal host config."""
    plugin_id, source = opencode_plugin_source(project, binary)
    plugin = project / ".opencode" / "plugins" / plugin_id / "index.js"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(source)
    config_path = project / ".opencode" / "opencode.json"
    config = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
        except ValueError:
            config = {}
        if not isinstance(config, dict):
            config = {}
    permission = config.get("permission")
    if not isinstance(permission, dict):
        permission = {}
    # Native read is served by ffsake; edits and search stay native.
    for name in ("bash", "shell", "read", "edit", "write", "apply_patch",
                 "glob", "grep", "list"):
        permission.setdefault(name, "allow")
    config["permission"] = permission
    config_path.write_text(json.dumps(config, indent=2))
    return plugin, config_path, plugin_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("claude", "codex", "opencode"), required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--prefix", default=str(Path.home() / ".local/share/ffsake"))
    parser.add_argument("--redirect-reads", action="store_true",
                        help="install the Claude read hook or the OpenCode read plugin")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    project = args.project.resolve(strict=True)
    if not project.is_dir():
        parser.error("project must be a directory")
    binary = Path(args.prefix).expanduser().resolve() / "current" / "ffsake"
    block = guidance(binary)
    path = project / ("CLAUDE.md" if args.host == "claude" else "AGENTS.md")
    existing = path.read_text() if path.exists() else ""
    if START in existing:
        action = "present"
    elif args.apply:
        separator = "" if not existing or existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"
        path.write_text(existing + separator + block + "\n")
        action = "appended"
    else:
        action = "would_append"

    settings_path = None
    plugin_path = None
    plugin_id = None
    if args.redirect_reads:
        if args.host == "claude":
            settings_path = project / ".claude" / "ffsake-read-policy.json"
            if args.apply:
                settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(json.dumps(hook_settings(binary), indent=2))
        elif args.host == "opencode":
            if args.apply:
                plugin_path, settings_path, plugin_id = install_opencode_policy(project, binary)
            else:
                plugin_id = opencode_plugin_source(project, binary)[0]
                plugin_path = project / ".opencode" / "plugins" / plugin_id / "index.js"
                settings_path = project / ".opencode" / "opencode.json"
        else:
            parser.error("--redirect-reads is supported for Claude Code and OpenCode")
    rule = ("Bash(ffsake:*)" if args.host == "claude"
            else "allow bash, read, edit, write, glob, grep in opencode.json")
    print(json.dumps({
        "ok": True,
        "host": args.host,
        "project": str(project),
        "binary": str(binary),
        "mcp_config": None,
        "tool_approvals": 0,
        "permission_rule": rule,
        "guidance_file": str(path),
        "action": action,
        "read_policy": str(settings_path) if settings_path else None,
        "read_policy_installed": bool(settings_path and settings_path.exists()),
        "read_plugin": str(plugin_path) if plugin_path else None,
        "read_plugin_id": plugin_id,
        "settings_flag": f"--settings {settings_path}" if settings_path and args.host == "claude" else None,
        "model_calls": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
