#!/usr/bin/env python3
"""Model-free checks for the CLI-first, no-MCP host integration."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

SCRIPT = Path(__file__).resolve().with_name("ffsake_cli_setup.py")


def run(*args):
    result = subprocess.run([sys.executable, str(SCRIPT), *args],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def run_failure(*args):
    result = subprocess.run([sys.executable, str(SCRIPT), *args],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0, result.stdout
    return result.stderr


def main():
    with tempfile.TemporaryDirectory(prefix="ffsake-cli-setup-") as td:
        project = Path(td)

        claude = project / "claude-project"
        claude.mkdir()
        (claude / "CLAUDE.md").write_text("# Project\n\nKeep this guidance.\n")
        (claude / ".claude").mkdir()
        settings_path = claude / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({
            "permissions": {"deny": ["Bash(rm *)"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash",
                                      "hooks": [{"type": "command", "command": "other-hook"}]}]},
            "custom": True,
        }, indent=2) + "\n")
        original_settings = settings_path.read_text()

        plan = run("--host", "claude", "--project", str(claude), "--redirect-reads")
        assert plan["action"] == "would_append", plan
        assert not (claude / "CLAUDE.md").read_text().count("<!-- ffsake-cli:start -->")
        assert settings_path.read_text() == original_settings

        first = run("--host", "claude", "--project", str(claude), "--redirect-reads", "--apply")
        assert first["mcp_config"] is None and first["tool_approvals"] == 0, first
        assert first["permission_rule"] == "Bash(ffsake:*)", first
        assert first["action"] == "appended", first
        text = (claude / "CLAUDE.md").read_text()
        assert text.startswith("# Project\n\nKeep this guidance.\n"), text
        assert text.count("<!-- ffsake-cli:start -->") == 1, text
        assert 'probe . --debug' in text and 'read . ' in text
        settings = json.loads(settings_path.read_text())
        assert settings["custom"] is True and settings["permissions"] == {"deny": ["Bash(rm *)"]}
        entries = settings["hooks"]["PreToolUse"]
        assert any(entry["matcher"] == "Bash" and entry["hooks"][0]["command"] == "other-hook"
                   for entry in entries), entries
        ffsake_hooks = [entry for entry in entries if "claude_read_redirect_hook.py"
                        in entry["hooks"][0]["command"]]
        assert len(ffsake_hooks) == 1 and ffsake_hooks[0]["matcher"] == "Read", entries
        assert "FFSAKE_BIN=" in ffsake_hooks[0]["hooks"][0]["command"]

        second = run("--host", "claude", "--project", str(claude), "--redirect-reads", "--apply")
        assert second["action"] == "present", second
        assert (claude / "CLAUDE.md").read_text().count("<!-- ffsake-cli:start -->") == 1
        entries = json.loads(settings_path.read_text())["hooks"]["PreToolUse"]
        assert sum("claude_read_redirect_hook.py" in entry["hooks"][0]["command"]
                   for entry in entries) == 1, entries

        removed = run("--host", "claude", "--project", str(claude), "--remove", "--apply")
        assert removed["action"] == "removed" and removed["removed_hooks"] == 1, removed
        assert (claude / "CLAUDE.md").read_text().startswith("# Project\n\nKeep this guidance.\n")
        assert "<!-- ffsake-cli" not in (claude / "CLAUDE.md").read_text()
        remaining = json.loads(settings_path.read_text())
        assert remaining["custom"] is True and remaining["permissions"] == {"deny": ["Bash(rm *)"]}
        assert all("claude_read_redirect_hook.py" not in hook["hooks"][0]["command"]
                   for hook in remaining["hooks"]["PreToolUse"])

        opencode = project / "opencode-project"
        (opencode / ".opencode").mkdir(parents=True)
        config_path = opencode / ".opencode" / "opencode.json"
        config_path.write_text(json.dumps({
            "mcp": {"other": {"type": "local", "command": ["other"]}},
            "permission": {"edit": "ask"},
            "custom": True,
        }, indent=2) + "\n")
        opened = run("--host", "opencode", "--project", str(opencode), "--redirect-reads", "--apply")
        assert opened["read_policy_installed"] is True, opened
        plugin = Path(opened["read_plugin"])
        assert plugin.is_file() and plugin.parent.name.startswith("ffsake-read-redirect-"), opened
        source = plugin.read_text()
        assert '"__FFSAKE_BIN__"' not in source and '"__FFSAKE_ROOT__"' not in source
        config = json.loads(config_path.read_text())
        assert config["custom"] is True and config["mcp"]["other"]["command"] == ["other"]
        assert config["permission"]["edit"] == "ask" and config["permission"]["read"] == "allow"

        removed = run("--host", "opencode", "--project", str(opencode), "--remove", "--apply")
        assert removed["removed_plugins"] and not Path(removed["removed_plugins"][0]).exists(), removed
        assert json.loads(config_path.read_text())["custom"] is True

        jsonc = project / "jsonc-project"
        (jsonc / ".opencode").mkdir(parents=True)
        (jsonc / ".opencode" / "opencode.jsonc").write_text("// keep me\n{}\n")
        message = run_failure("--host", "opencode", "--project", str(jsonc),
                              "--redirect-reads", "--apply")
        assert "manual review" in message, message
        assert not list((jsonc / ".opencode").rglob("index.js"))

        codex = project / "codex-project"
        codex.mkdir()
        (codex / "AGENTS.md").write_text("# Agents\n")
        run("--host", "codex", "--project", str(codex), "--apply")
        assert 'probe . --debug' in (codex / "AGENTS.md").read_text()

        rust = project / "rust-project"
        rust.mkdir()
        (rust / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
        run("--host", "claude", "--project", str(rust), "--apply")
        rust_text = (rust / "CLAUDE.md").read_text()
        assert "preview_semantic_rename" in rust_text and '"op":"semantic"' in rust_text, rust_text

        print(json.dumps({
            "no_mcp_config": True,
            "tool_approvals": 0,
            "claude_permission_rule": first["permission_rule"],
            "idempotent_guidance": True,
            "existing_claude_settings_preserved": True,
            "existing_opencode_config_preserved": True,
            "opencode_jsonc_refused": True,
            "removal_restores_config": True,
            "codex_guidance": True,
            "read_redirect_hook": True,
            "opencode_read_redirect_plugin": True,
            "semantic_guidance": True,
            "model_calls": 0,
        }, indent=2))


if __name__ == "__main__":
    main()
