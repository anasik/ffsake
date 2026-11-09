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


def main():
    with tempfile.TemporaryDirectory(prefix="ffsake-cli-setup-") as td:
        project = Path(td)
        (project / "CLAUDE.md").write_text("# Project\n\nexisting guidance\n")
        first = run("--host", "claude", "--project", str(project), "--apply")
        assert first["mcp_config"] is None and first["tool_approvals"] == 0, first
        assert first["permission_rule"] == "Bash(ffsake:*)", first
        assert first["action"] == "appended", first
        text = (project / "CLAUDE.md").read_text()
        assert text.count("<!-- ffsake-cli:start -->") == 1, text
        assert 'probe . --debug' in text and 'read . ' in text
        second = run("--host", "claude", "--project", str(project), "--apply")
        assert second["action"] == "present", second
        assert (project / "CLAUDE.md").read_text().count("<!-- ffsake-cli:start -->") == 1
        for name in (".mcp.json", ".codex", "opencode.json"):
            assert not (project / name).exists(), name
        policy = project / ".claude" / "ffsake-read-policy.json"
        assert not policy.exists(), policy
        hooked = run("--host", "claude", "--project", str(project),
                     "--redirect-reads", "--apply")
        assert hooked["read_policy_installed"] is True, hooked
        settings = json.loads(policy.read_text())
        command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert "claude_read_redirect_hook.py" in command and "FFSAKE_BIN=" in command, command
        assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Read"
        assert settings["permissions"]["allow"] == ["Bash", "Edit", "Write"]
        assert hooked["settings_flag"] == f"--settings {policy.resolve()}"

        (project / "AGENTS.md").write_text("# Agents\n")
        codex = run("--host", "codex", "--project", str(project), "--apply")
        assert codex["mcp_config"] is None and codex["tool_approvals"] == 0, codex
        assert codex["guidance_file"].endswith("AGENTS.md"), codex
        assert 'probe . --debug' in (project / "AGENTS.md").read_text()

        opencode = run("--host", "opencode", "--project", str(project),
                       "--redirect-reads", "--apply")
        assert opencode["read_policy_installed"] is True, opencode
        assert opencode["settings_flag"] is None, opencode
        assert opencode["read_plugin_id"].startswith("ffsake-read-redirect-"), opencode
        plugin = Path(opencode["read_plugin"])
        assert plugin.is_file() and plugin.parent.name == opencode["read_plugin_id"], opencode
        source = plugin.read_text()
        assert '"__FFSAKE_BIN__"' not in source and '"__FFSAKE_ROOT__"' not in source
        assert 'ffsake-read-redirect-' in source
        host_config = json.loads(Path(opencode["read_policy"]).read_text())
        assert host_config["permission"]["read"] == "allow", host_config
        assert host_config["permission"]["edit"] == "allow", host_config

        print(json.dumps({
            "no_mcp_config": True,
            "tool_approvals": 0,
            "claude_permission_rule": first["permission_rule"],
            "idempotent_guidance": True,
            "codex_guidance": True,
            "read_redirect_hook": True,
            "opencode_read_redirect_plugin": True,
            "model_calls": 0,
        }, indent=2))


if __name__ == "__main__":
    main()
