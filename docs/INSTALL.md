# Install FFSAKE for an alpha project

An agent can follow these instructions when given the FFSAKE repository URL and the project to configure. The installer uses Git and Python 3.11 or later. Release binaries target macOS ARM64 (macOS 14 or later) and Linux x86-64 (glibc 2.35 or later). Source builds additionally need stable Rust/Cargo and a C compiler/build tools. It has been exercised on macOS; the same Unix path is checked on Linux in CI. Windows is not supported by this installer or the current process-group runner. Language servers and debug adapters are optional and are not installed here. FFSAKE has no permanent daemon: the coding host starts and stops its MCP server over stdio.

Alpha archives and SHA-256 checksums are published on GitHub Releases once a release is approved and tagged. Before the first release, `install` reports that no release is available; use `install --source` as an explicit fallback. Checksums verify downloaded bytes against the release checksum file; they are not an independent signature. Source builds use locked Cargo dependencies. The versioned binaries may require standard OS runtime libraries; Git is required for Git operations, and `curl` is used for optional update awareness. Language servers and debug adapters are separate optional dependencies.

## One-time installation

From the project to configure, record its absolute path. Keep the source checkout for later configuration and updates; it is separate from the installed binaries. The default source location below is an example. If it already exists, inspect its remote and local changes and reuse it only if it is the intended FFSAKE checkout; do not overwrite or reset it.

```sh
PROJECT_DIR="$(pwd -P)"
FFSAKE_SOURCE="$HOME/.local/src/ffsake"
git clone https://github.com/anasik/ffsake.git "$FFSAKE_SOURCE"
cd "$FFSAKE_SOURCE"
python3 scripts/ffsake_install.py install
```

The installer downloads the latest alpha for the supported platform, verifies its SHA-256 and MCP catalog, and installs `ffsake` and `ffsake-mcp` under `~/.local/share/ffsake/current/`. MCP configuration uses that stable path, not the checkout's `target` directory. Repeating `install` reuses a matching installation. Use `install --version v0.1.0-alpha.1` to select and pin a release. If a different version is available, install reports it without upgrading. Builds and Git may execute code; the host's normal approval policy applies, including deliberate auto/allow settings.

For isolated tests or a different location, put `--prefix /absolute/path` **before** the subcommand on every invocation. `--source` explicitly selects a source build; `--profile debug` is available with `--source`.

## Configure a project

Choose exactly one host (`claude`, `codex`, or `opencode`). First print and review the proposed FFSAKE entry and any new permission defaults, then approve the explicit apply command through the host. Substitute the chosen host below:

```sh
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" configure --host claude --project "$PROJECT_DIR" --guidance
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" configure --host claude --project "$PROJECT_DIR" --guidance --apply
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" doctor --host claude --project "$PROJECT_DIR"
```

The optional `--guidance` adds clearly marked blocks to `CLAUDE.md` for Claude or `AGENTS.md` for Codex/OpenCode. The preview prints the proposed blocks, including Git repository selection guidance. Existing instructions are retained, repeating setup leaves a matching block unchanged, and edited or malformed markers require manual review. Because OpenCode selects the nearest instruction file, adding a new project `AGENTS.md` is refused if it would shadow an existing project or ancestor `CLAUDE.md` or `AGENTS.md`; merge guidance manually in that case. Omit `--guidance` if you do not want project guidance. Hosts may have other instruction precedence and trust settings; installing guidance does not prove that an agent followed it.

The configuration is project-scoped: Claude Code uses `.mcp.json` and, if absent, `.claude/settings.json`; Codex uses `.codex/config.toml`; OpenCode 1.x uses `opencode.json`. Existing unrelated entries are preserved. A compatible entry in the project or the standard user configuration is reused, and repeated setup leaves it unchanged. Existing duplicate entries, entries under another name pointing to an `ffsake-mcp` executable, and conflicting entries require manual review. Custom or managed configuration locations and differently named executables cannot all be discovered automatically. A conflicting FFSAKE entry, unsafe symlink, invalid file or existing OpenCode `opencode.jsonc` stops for manual review without replacing it. Existing Claude settings and existing OpenCode permission policies are left alone, including deliberate auto/allow rules. When new defaults are created, ordinary file tools are allowed and access changes, task execution, debugger launch and Git mutations follow the host's normal approval policy. Host or organization policy may override these defaults.

These project files can contain machine-specific absolute paths. Review them before committing or sharing them. Claude may ask you to trust the project and approve its MCP server; Codex may require the project to be trusted. New chats in the same configured project do not need setup again. In an already-running Claude chat, use `/mcp`, select `ffsake` and choose **Reconnect** after setup or update. Other hosts may require reconnecting or restarting their MCP session.

Forks and subagents need special care when they use another worktree: a shared MCP server remains rooted at its original project. See [subagent setup and worktree checks](SUBAGENTS.md) for a reviewable root-only rebind and host-specific limitations.

The doctor validates both installed binary checksums and the project entry, then launches the installed MCP server in a disposable workspace, performs `initialize`, and checks tool discovery and the expected catalog. It makes no model calls and does not create a project journal. It reports whether the host CLI is on PATH but cannot confirm workspace trust, global/managed configuration overrides, or an already-running host connection.

## Update

Only update when the user authorizes it through the host. Inspect the source checkout for local changes first; do not discard or reset them. Fetch/pull and build are separate actions subject to host approval:

```sh
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" check-update
# Explicit host approval is required before changing the installed version:
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" update
# Select and pin a specific version instead:
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" update --version v0.1.0-alpha.1
# Restore an already retained release (or use `--revision REVISION` for a source build):
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" rollback --version v0.1.0-alpha.1
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" doctor --host claude --project "$PROJECT_DIR"
```

An explicit update verifies a new generation before atomically switching `current` and keeps the old generation for rollback. A running MCP process keeps its existing binary; reconnect the host session to use the replacement. `update --source` is the explicit source-build alternative; inspect and update the source checkout separately without discarding changes. An interrupted install may leave an `.install.lock`; inspect it before removing it.

Unpinned alpha servers check the configured channel asynchronously at startup and at most once per day during use, using a shared per-installation cache and a short timeout. A newer compatible release is included once in an ordinary tool response; the agent should relay it. Offline failures do not block tools. A pinned installation suppresses automatic notices and ordinary `update` will not move it. Use an explicit `--version` to change the pin or `update --unpin` to follow the channel again; `check-update` remains available. No automatic installation, background service or forced restart occurs. FFSAKE release versions (for example `v0.1.0-alpha.1`) are distinct from MCP protocol versions; MCP manages the server connection, not software installation.

## Uninstall

```sh
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" uninstall
```

This removes the managed active binaries and link; retained generations are left for manual review. It does not remove project files, `.ffsake` journals, source checkout or host configuration. The host entry will point to a missing binary until you remove only its FFSAKE entry using your host's configuration mechanism. Review the relevant project files rather than deleting them wholesale. Repeating uninstall is harmless.

Host references: [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Codex MCP](https://developers.openai.com/codex/mcp), and [OpenCode MCP](https://opencode.ai/docs/mcp-servers/).
