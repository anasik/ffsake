#!/usr/bin/env python3
"""Install-free, CLI-first host integration for FFSAKE.

Writes one project guidance block and, optionally, a Claude Code PreToolUse
hook that serves native Read through ffsake. No MCP server entry, tool catalog
or per-tool approval list is created.
"""
import argparse
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
                "matcher": "Read|Bash",
                "hooks": [{
                    "type": "command",
                    "command": f"FFSAKE_BIN={binary} {os.sys.executable} {hook}",
                }],
            }]
        }
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("claude", "codex", "opencode"), required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--prefix", default=str(Path.home() / ".local/share/ffsake"))
    parser.add_argument("--redirect-reads", action="store_true",
                        help="write the Claude read-redirect hook settings")
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
    if args.redirect_reads:
        if args.host != "claude":
            parser.error("--redirect-reads is supported for Claude Code")
        settings_path = project / ".claude" / "ffsake-read-policy.json"
        if args.apply:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(hook_settings(binary), indent=2))
    rule = "Bash(ffsake:*)" if args.host == "claude" else "allow the ffsake executable via host policy"
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
        "settings_flag": f"--settings {settings_path}" if settings_path else None,
        "model_calls": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
