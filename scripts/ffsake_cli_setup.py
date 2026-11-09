#!/usr/bin/env python3
"""Install-free, CLI-first host integration for FFSAKE.

Writes one project guidance block and, optionally, a read-redirect integration
that serves native reads through ffsake: a Claude Code PreToolUse hook or an
OpenCode local plugin. No MCP server entry, tool catalog or per-tool approval
list is created. Existing host settings are preserved; the hook is merged into
Claude's project settings and OpenCode's project config is merged without
rewriting unrelated keys.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

START = "<!-- ffsake-cli:start -->"
END = "<!-- ffsake-cli:end -->"
HOOK_NAME = "claude_read_redirect_hook.py"
PLUGIN_PREFIX = "ffsake-read-redirect-"


def semantic_sources(project):
    if (project / "Cargo.toml").is_file():
        return True
    for pattern in ("*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hh"):
        if next(project.rglob(pattern), None) is not None:
            return True
    return next(project.rglob("Cargo.toml"), None) is not None


def guidance(binary, semantic=False):
    semantic_block = ""
    if semantic:
        semantic_block = f"""
For Rust or C++ semantic references or a rename, query the installed language
server instead of a textual replace:
`{binary} . --request '{{"op":"semantic","request":{{"files":["src/lib.rs"],"path":"src/lib.rs","position":{{"line":1,"column":0}},"query":"references","context":{{"language":"rust","crate_root":"src/lib.rs","edition":"2021"}},"limit":100}}}}'`
Use `query:"rename"` with `new_name` and the same context, or
`preview_semantic_rename`, then review and `--apply`. Macro-heavy or cfg-gated
code can be outside the snapshot; run the project build/tests after any rename.
"""
    return f"""{START}
## FFSAKE CLI

Use the guarded FFSAKE CLI through Bash for scoped repository reads and one-call
debugging. Allow the CLI commands your host policy requires; FFSAKE does not
grant permissions and host approval remains authoritative. With the read-redirect
hook or plugin, native Read is served by ffsake automatically.
{semantic_block}

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
probe before editing so the required runtime values are observed. For quick
ordinary changes, native Edit/Write is usually faster; use FFSAKE preview/apply
when you need review, stale-content checks, atomic multi-file application or
history. FFSAKE enforces workspace policy, stale-state checks, history and debug
cleanup for the operations it runs. Never bypass an access denial.
{END}"""


def hook_entry(binary):
    hook = Path(__file__).resolve().with_name(HOOK_NAME)
    return {
        "matcher": "Read",
        "hooks": [{
            "type": "command",
            "command": f"FFSAKE_BIN={binary} {os.sys.executable} {hook}",
        }],
    }


def is_ffsake_hook(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return False
    return any(isinstance(hook, dict) and HOOK_NAME in str(hook.get("command", ""))
               for hook in entry["hooks"])


def merge_claude_hook(text, binary):
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("existing .claude/settings.json must be a JSON object")
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("existing .claude/settings.json hooks must be an object")
    entries = hooks.setdefault("PreToolUse", [])
    if not isinstance(entries, list):
        raise ValueError("existing .claude/settings.json PreToolUse must be an array")
    hooks["PreToolUse"] = [entry for entry in entries if not is_ffsake_hook(entry)]
    hooks["PreToolUse"].append(hook_entry(binary))
    return data


def remove_claude_hook(text):
    if not text.strip():
        return text, 0
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("existing .claude/settings.json must be a JSON object")
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return text, 0
    entries = hooks.get("PreToolUse")
    if not isinstance(entries, list):
        return text, 0
    kept = [entry for entry in entries if not is_ffsake_hook(entry)]
    removed = len(entries) - len(kept)
    if not removed:
        return text, 0
    hooks["PreToolUse"] = kept
    return json.dumps(data, indent=2) + "\n", removed


def strip_guidance(text):
    start = text.find(START)
    end = text.find(END, start + len(START)) if start >= 0 else -1
    if start < 0 or end < 0:
        return text, False
    end += len(END)
    while end < len(text) and text[end] == "\n":
        end += 1
    before = text[:start].rstrip("\n")
    after = text[end:]
    result = before + ("\n" if before and after else "") + after
    if result and not result.endswith("\n"):
        result += "\n"
    return result, True


def opencode_plugin_source(project, binary):
    """Render the read-redirect plugin with a workspace-unique id."""
    template = Path(__file__).with_name("opencode_read_redirect_plugin.js").read_text()
    digest = hashlib.sha256(str(project).encode()).hexdigest()[:12]
    plugin_id = f"{PLUGIN_PREFIX}{digest}"
    source = (template
              .replace('"__FFSAKE_BIN__"', json.dumps(str(binary)))
              .replace('"__FFSAKE_ROOT__"', json.dumps(str(project)))
              .replace('"__FFSAKE_PLUGIN_ID__"', json.dumps(plugin_id)))
    return plugin_id, source


def load_opencode_config(project):
    directory = project / ".opencode"
    config_path = directory / "opencode.json"
    jsonc_path = directory / "opencode.jsonc"
    if jsonc_path.exists():
        raise ValueError("existing .opencode/opencode.jsonc requires manual review; "
                         "merge the FFSAKE plugin and permission entries yourself")
    if config_path.exists():
        if config_path.is_symlink():
            raise ValueError("existing .opencode/opencode.json cannot be a symlink")
        try:
            config = json.loads(config_path.read_text())
        except ValueError as exc:
            raise ValueError("existing .opencode/opencode.json is not valid JSON; "
                             "manual review required") from exc
        if not isinstance(config, dict):
            raise ValueError("existing .opencode/opencode.json must be an object")
    else:
        config = {}
    return config_path, config


def install_opencode_policy(project, binary):
    """Install the local read plugin and a merged host config."""
    plugin_id, source = opencode_plugin_source(project, binary)
    config_path, config = load_opencode_config(project)
    plugin = project / ".opencode" / "plugins" / plugin_id / "index.js"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(source)
    permission = config.get("permission")
    if not isinstance(permission, dict):
        permission = {}
    # Native read is served by ffsake; edits and search stay native.
    for name in ("bash", "shell", "read", "edit", "write", "apply_patch",
                 "glob", "grep", "list"):
        permission.setdefault(name, "allow")
    config["permission"] = permission
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return plugin, config_path, plugin_id


def remove_opencode_plugins(project):
    root = project / ".opencode" / "plugins"
    removed = []
    if not root.is_dir() or root.is_symlink():
        return removed
    for directory in sorted(root.iterdir()):
        if (directory.name.startswith(PLUGIN_PREFIX) and directory.is_dir()
                and not directory.is_symlink()):
            shutil.rmtree(directory)
            removed.append(str(directory))
    return removed


def write_preserving(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    temporary = path.with_name("." + path.name + ".ffsake-tmp")
    try:
        temporary.write_text(text)
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("claude", "codex", "opencode"), required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--prefix", default=str(Path.home() / ".local/share/ffsake"))
    parser.add_argument("--redirect-reads", action="store_true",
                        help="install the Claude read hook or the OpenCode read plugin")
    parser.add_argument("--remove", action="store_true",
                        help="remove FFSAKE guidance, hook and plugin, preserving other settings")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    project = args.project.resolve(strict=True)
    if not project.is_dir():
        parser.error("project must be a directory")
    binary = Path(args.prefix).expanduser().resolve() / "current" / "ffsake"
    if args.redirect_reads and args.host == "codex":
        parser.error("--redirect-reads is supported for Claude Code and OpenCode")
    if args.remove and args.redirect_reads:
        parser.error("--remove cannot be combined with --redirect-reads")

    path = project / ("CLAUDE.md" if args.host == "claude" else "AGENTS.md")
    settings_path = None
    plugin_path = None
    plugin_id = None
    removed_hooks = 0
    removed_plugins = []
    if args.remove:
        action = "absent"
        if path.exists():
            updated, present = strip_guidance(path.read_text())
            if present:
                if args.apply:
                    write_preserving(path, updated)
                action = "removed" if args.apply else "would_remove"
        if args.host == "claude":
            settings_path = project / ".claude" / "settings.json"
            if settings_path.exists() and not settings_path.is_symlink():
                updated, removed_hooks = remove_claude_hook(settings_path.read_text())
                if removed_hooks and args.apply:
                    write_preserving(settings_path, updated)
        elif args.host == "opencode":
            if args.apply:
                removed_plugins = remove_opencode_plugins(project)
            else:
                root = project / ".opencode" / "plugins"
                if root.is_dir() and not root.is_symlink():
                    removed_plugins = [str(item) for item in sorted(root.iterdir())
                                       if item.name.startswith(PLUGIN_PREFIX) and item.is_dir()]
    else:
        existing = path.read_text() if path.exists() else ""
        action = ("present" if START in existing else
                  "appended" if args.apply else "would_append")
        if args.apply and START not in existing:
            separator = "" if not existing or existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"
            write_preserving(path, existing + separator + guidance(binary, semantic_sources(project)) + "\n")
        if args.redirect_reads:
            if args.host == "claude":
                settings_path = project / ".claude" / "settings.json"
                existing = settings_path.read_text() if settings_path.exists() else ""
                merged = json.dumps(merge_claude_hook(existing, binary), indent=2) + "\n"
                if args.apply and merged != existing:
                    write_preserving(settings_path, merged)
            else:
                if args.apply:
                    plugin_path, settings_path, plugin_id = install_opencode_policy(project, binary)
                else:
                    config_path, _ = load_opencode_config(project)
                    plugin_id = opencode_plugin_source(project, binary)[0]
                    plugin_path = project / ".opencode" / "plugins" / plugin_id / "index.js"
                    settings_path = config_path
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
        "removed_hooks": removed_hooks,
        "removed_plugins": removed_plugins,
        "settings_flag": None,
        "model_calls": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
