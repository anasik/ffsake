#!/usr/bin/env python3
"""Claude Code PreToolUse middleware: route native Read through ffsake.

The hook executes the scoped ffsake read for the requested file/range, writes
the returned text to a private cache file under `.ffsake/hook-reads`, and
rewrites the Read input to that cache file. Native Read therefore still works
for the model, but repository content is produced by ffsake's access policy
instead of a direct native read. File-reading shell commands are denied.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

READ_COMMANDS = {
    "cat", "head", "tail", "sed", "awk", "grep", "rg", "find", "less", "more",
    "nl", "cut", "sort", "uniq", "wc",
}


def output(decision, reason=None, updated=None):
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        payload["hookSpecificOutput"]["permissionDecisionReason"] = reason
    if updated is not None:
        payload["hookSpecificOutput"]["updatedInput"] = updated
    print(json.dumps(payload))
    raise SystemExit(0)


def main():
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        output("deny", "ffsake read policy could not parse the tool event")
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    cwd = Path(event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if tool == "Bash":
        command = str(tool_input.get("command", "")).strip()
        words = command.replace(";", " ").replace("|", " ").split()
        if words and words[0] in READ_COMMANDS:
            output("deny", "use the ffsake CLI read command for repository contents")
        # Defer every other command to the host's normal permission policy.
        raise SystemExit(0)
    if tool != "Read":
        # Never blanket-approve edits, tasks or other tools from a read policy.
        raise SystemExit(0)
    path = str(tool_input.get("file_path", ""))
    if not path:
        output("allow")
    cache = (cwd / ".ffsake" / "hook-reads").resolve()
    if Path(path).resolve().is_relative_to(cache):
        output("allow")
    binary = os.environ.get("FFSAKE_BIN")
    if not binary or not Path(binary).is_file():
        output("deny", "ffsake binary is not configured for the read policy")
    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    spec = path
    try:
        if offset is not None:
            start = int(offset) + 1
            end = start + (int(limit) if limit else 2000) - 1
            spec = f"{path}:{start}-{end}"
        elif limit:
            spec = f"{path}:1-{int(limit)}"
    except (TypeError, ValueError):
        output("deny", "invalid Read offset/limit")
    run = subprocess.run([binary, "read", str(cwd), spec, "--raw"],
                         capture_output=True, text=True, timeout=30)
    try:
        value = json.loads(run.stdout)
    except ValueError:
        output("deny", "ffsake read returned invalid output")
    if value.get("ok") is not True:
        output("deny", str(value.get("error") or "ffsake read failed"))
    text = value.get("text")
    if text is None:
        text = value.get("result", {}).get("text", "")
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{path}:{spec}:{text}".encode()).hexdigest()[:32]
    target = cache / f"{digest}.txt"
    target.write_text(text)
    output("allow", "served by ffsake read policy",
           {"file_path": str(target), "offset": 0, "limit": max(2000, len(text.splitlines()) + 1)})


if __name__ == "__main__":
    main()
