#!/usr/bin/env python3
"""Install-free, CLI-first host integration for FFSAKE.

Writes one project guidance block and prints the single host permission rule.
No MCP server entry, tool catalog or per-tool approval list is created.
"""
import argparse
import json
from pathlib import Path

START = "<!-- ffsake-cli:start -->"
END = "<!-- ffsake-cli:end -->"
BLOCK = f"""{START}
## FFSAKE CLI

Use the installed `ffsake` CLI through Bash for scoped repository reads and debugging.
The host needs one allowlist rule for the CLI (for Claude Code: `Bash(ffsake:*)`).

```sh
F="$HOME/.local/share/ffsake/current/ffsake"
"$F" read "$PWD" path:10-40 path2
"$F" probe "$PWD" --debug .ffsake/rc-debug.json --config main --breakpoint path:line --stops 2
```

Preview edits with `"$F" edit "$PWD" path --old 'old' --new 'new'` and apply the token
with `"$F" apply "$PWD" <token>`. Read only the lines you need. Make ordinary code
changes with native Edit/Write when that is more efficient. FFSAKE still enforces its
workspace policy, stale-state checks, history and debug cleanup for the operations it
runs. Never bypass an access denial.
{END}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("claude", "codex", "opencode"), required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    project = args.project.resolve(strict=True)
    if not project.is_dir():
        parser.error("project must be a directory")
    path = project / ("CLAUDE.md" if args.host == "claude" else "AGENTS.md")
    existing = path.read_text() if path.exists() else ""
    if START in existing:
        action = "present"
    elif args.apply:
        separator = "" if not existing or existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"
        path.write_text(existing + separator + BLOCK + "\n")
        action = "appended"
    else:
        action = "would_append"
    rule = "Bash(ffsake:*)" if args.host == "claude" else "allow the ffsake executable via host policy"
    print(json.dumps({
        "ok": True,
        "host": args.host,
        "project": str(project),
        "binary": "~/.local/share/ffsake/current/ffsake",
        "mcp_config": None,
        "tool_approvals": 0,
        "permission_rule": rule,
        "guidance_file": str(path),
        "action": action,
        "model_calls": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
