#!/usr/bin/env python3
"""Claude Code PreToolUse policy: force ffsake for repository reads.

Native Read/Glob/Grep and common file-reading shell commands are denied.
Native Edit/Write and program/test commands remain allowed. The script reads
one hook event as JSON on stdin and prints a Claude Code hook decision.
"""
import json
import sys

READ_TOOLS = {"Read", "Glob", "Grep"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_COMMANDS = {
    "cat", "head", "tail", "sed", "awk", "grep", "rg", "find", "less", "more",
    "nl", "cut", "sort", "uniq", "wc",
}


def decide(decision, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    raise SystemExit(0)


def main():
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        decide("deny", "ffsake policy could not parse the tool event")
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    if tool in READ_TOOLS:
        decide("deny", "use the ffsake CLI read command for repository contents")
    if tool in EDIT_TOOLS:
        decide("allow", "native edits are allowed")
    if tool == "Bash":
        command = str(tool_input.get("command", "")).strip()
        words = command.replace(";", " ").replace("|", " ").split()
        if words and words[0] in READ_COMMANDS:
            decide("deny", "use the ffsake CLI read command for repository contents")
        if any(word in READ_COMMANDS for word in words[1:]):
            # Pipelines such as `python ... | head` are still allowed only when
            # the first command is a program/test runner.
            if words[0] not in ("ffsake", "python", "python3", "pytest", "git", "ls", "pwd"):
                decide("deny", "use the ffsake CLI read command for repository contents")
    decide("allow", "allowed by ffsake policy")


if __name__ == "__main__":
    main()
