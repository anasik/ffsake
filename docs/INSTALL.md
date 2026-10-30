# Install FFSAKE for an alpha project

An agent can follow these instructions when given the FFSAKE repository URL and the project to configure. The supported source-build path uses Git, Python 3.11 or later, a current stable Rust/Cargo toolchain, a C compiler and build tools. It has been exercised on macOS; the same Unix path is checked on Linux in CI. Windows is not supported by this installer or the current process-group runner. Language servers and debug adapters are optional and are not installed here. FFSAKE has no permanent daemon: the coding host starts and stops its MCP server over stdio.

There were no published GitHub release binaries when this path was audited. Do not download an unverified build or assume another platform is supported. Source builds use the repository's locked Cargo dependencies.

## One-time installation

From the project to configure, record its absolute path. Keep the source checkout for later configuration and updates; it is separate from the installed binaries. The default source location below is an example. If it already exists, inspect its remote and local changes and reuse it only if it is the intended FFSAKE checkout; do not overwrite or reset it.

```sh
PROJECT_DIR="$(pwd -P)"
FFSAKE_SOURCE="$HOME/.local/src/ffsake"
git clone https://github.com/anasik/ffsake.git "$FFSAKE_SOURCE"
cd "$FFSAKE_SOURCE"
python3 scripts/ffsake_install.py install
```

The installer builds `ffsake` and `ffsake-mcp`, verifies the MCP catalog, and publishes them under `~/.local/share/ffsake/current/`. MCP configuration uses that stable path, not the checkout's `target` directory. Repeating `install` reuses an unchanged installation. If the source revision differs, it reports the installed and available revisions without upgrading. Builds and Git may execute code; the host's normal approval policy applies, including deliberate auto/allow settings.

For isolated tests or a different location, put `--prefix /absolute/path` **before** the subcommand on every invocation. `--profile debug` is available for development; the default is `release`.

## Configure a project

Choose exactly one host (`claude`, `codex`, or `opencode`). First print and review the proposed FFSAKE entry and any new permission defaults, then approve the explicit apply command through the host. Substitute the chosen host below:

```sh
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" configure --host claude --project "$PROJECT_DIR" --guidance
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" configure --host claude --project "$PROJECT_DIR" --guidance --apply
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" doctor --host claude --project "$PROJECT_DIR"
```

The optional `--guidance` adds a clearly marked block to `CLAUDE.md` for Claude or `AGENTS.md` for Codex/OpenCode. The preview prints the proposed block. Existing instructions are retained, repeating setup leaves a matching block unchanged, and edited or malformed markers require manual review. Because OpenCode selects the nearest instruction file, adding a new project `AGENTS.md` is refused if it would shadow an existing project or ancestor `CLAUDE.md` or `AGENTS.md`; merge guidance manually in that case. Omit `--guidance` if you do not want project guidance. Hosts may have other instruction precedence and trust settings; installing guidance does not prove that an agent followed it.

The configuration is project-scoped: Claude Code uses `.mcp.json` and, if absent, `.claude/settings.json`; Codex uses `.codex/config.toml`; OpenCode 1.x uses `opencode.json`. Existing unrelated entries are preserved. A compatible entry in the project or the standard user configuration is reused, and repeated setup leaves it unchanged. Existing duplicate entries, entries under another name pointing to an `ffsake-mcp` executable, and conflicting entries require manual review. Custom or managed configuration locations and differently named executables cannot all be discovered automatically. A conflicting FFSAKE entry, unsafe symlink, invalid file or existing OpenCode `opencode.jsonc` stops for manual review without replacing it. Existing Claude settings and existing OpenCode permission policies are left alone, including deliberate auto/allow rules. When new defaults are created, ordinary file tools are allowed and access changes, task execution, debugger launch and Git mutations follow the host's normal approval policy. Host or organization policy may override these defaults.

These project files can contain machine-specific absolute paths. Review them before committing or sharing them. Claude may ask you to trust the project and approve its MCP server; Codex may require the project to be trusted. New chats in the same configured project do not need setup again. In an already-running Claude chat, use `/mcp`, select `ffsake` and choose **Reconnect** after setup or update. Other hosts may require reconnecting or restarting their MCP session.

The doctor validates both installed binary checksums and the project entry, then launches the installed MCP server in a disposable workspace, performs `initialize`, and checks tool discovery and the expected catalog. It makes no model calls and does not create a project journal. It reports whether the host CLI is on PATH but cannot confirm workspace trust, global/managed configuration overrides, or an already-running host connection.

## Update

Only update when the user authorizes it through the host. Inspect the source checkout for local changes first; do not discard or reset them. Fetch/pull and build are separate actions subject to host approval:

```sh
git -C "$FFSAKE_SOURCE" status --short
git -C "$FFSAKE_SOURCE" pull --ff-only
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" install
# After reviewing the reported revision and obtaining host approval:
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" update
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" doctor --host claude --project "$PROJECT_DIR"
```

An explicit `update` builds and verifies a new generation before atomically switching the stable `current` link. A running MCP server continues on its old process until reconnected; new sessions use the updated binary. The installer removes the previous generation only if it contains exactly its own verified files; `previous_generation_retained: true` means it left that generation for inspection. An interrupted install may leave an `.install.lock`; inspect it and any temporary files before removing them.

## Uninstall

```sh
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" uninstall
```

This removes the managed active binaries and link, not project files, `.ffsake` journals, source checkout or host configuration. The host entry will point to a missing binary until you remove only its FFSAKE entry using your host's configuration mechanism. Review the relevant project files rather than deleting them wholesale. Repeating uninstall is harmless.

Host references: [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Codex MCP](https://developers.openai.com/codex/mcp), and [OpenCode MCP](https://opencode.ai/docs/mcp-servers/).
