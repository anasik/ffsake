# CLI-first integration

FFSAKE can be used without an MCP catalog: the CLI provides scoped reads,
one-call debugging, optional reviewed transactions and selective semantic
operations through ordinary shell commands. The recommended alpha setup also
keeps the MCP server registered for capabilities that need it (Git review,
tasks, access tools and persistent debug sessions).

FFSAKE's editor is the preview/apply path: stale-checked, atomic across changed
files and journaled with undo/redo. Native `Edit`/`Write` are also available for
quick changes and are outside FFSAKE's journal.

The optional redirect serves the host's native `Read` through FFSAKE; it does
not remove FFSAKE's read/write API or disable native edits. In Claude the hook
matches `Read` only, runs `ffsake read` and rewrites the input to cached content;
it returns an allow decision, so FFSAKE's workspace policy governs that read and
host path-specific Read deny rules do not apply after the rewrite. OpenCode's
plugin replaces the `read` executor and rejects shell file-read commands;
edit/write/search remain available.

A harness that calls MCP or the CLI directly is unaffected and has full
read/write access. Use MCP-only setup if native host tools must stay untouched.

## Setup

Use the installer so binaries, project entries and removal stay coordinated:

```sh
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  configure --host claude --project /path/to/repo --cli-first --apply
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  doctor --host claude --project /path/to/repo --cli-first
```

Replace `claude` with `opencode` for OpenCode. Codex receives guidance and the
MCP entry; read redirection is not implemented there.

The standalone integration remains available when an MCP entry is deliberately
not wanted:

```sh
python3 scripts/ffsake_cli_setup.py --host claude --project /path/to/repo \
  --redirect-reads --apply
python3 scripts/ffsake_cli_setup.py --host opencode --project /path/to/repo \
  --redirect-reads --apply
```

Claude Code receives:

- one marked guidance block in `CLAUDE.md`;
- a `PreToolUse` hook merged into `.claude/settings.json` that serves native
  `Read` through `ffsake read` and returns the text through `updatedInput`.

The hook is loaded by Claude's normal project settings; no `--settings` flag is
required. Existing hooks, permissions and unrelated keys are preserved.

OpenCode receives the same guidance block plus:

- `.opencode/plugins/ffsake-read-redirect-<workspace-id>/index.js`, a local
  plugin that replaces the native `read` executor with `ffsake read` and
  rejects file-reading shell commands.

Setup does not write permission entries into `.opencode/opencode.json`: an
existing permission policy is preserved byte-for-byte, and a missing policy
stays missing. OpenCode's normal approval policy governs the CLI, MCP tools and
native tools.

If the project already uses `.opencode/opencode.jsonc`, setup refuses the
redirect and asks for a manual merge instead of creating a shadow config.
Invalid existing `opencode.json` is also refused rather than overwritten.

## Permissions

FFSAKE does not grant permissions and does not reduce the host's approval
surface. Allow the commands your policy requires. For Claude Code,
`Bash(ffsake:*)` is enough for CLI calls; MCP tools follow the host's normal
approval policy. The generated guidance does not modify permission settings.
OpenCode setup writes no `permission` entries at all: existing values, wildcard
rules and absent keys are all left as they are. Approve FFSAKE tool calls (and
the `ffsake` CLI command) according to your own OpenCode policy.

## Commands

Commands use a literal `.` workspace and no variables or quotes so the host's
static read block can parse them:

```sh
~/.local/share/ffsake/current/ffsake read . src/lib.rs:10-40 src/runtime.rs:1-60
~/.local/share/ffsake/current/ffsake probe . --debug .ffsake/rc-debug.json \
  --config app --breakpoint main.py:12 --stops 2
```

Reviewed edit, then apply:

```sh
~/.local/share/ffsake/current/ffsake . --request '{"op":"preview_edit","path":"src/lib.rs","old_text":"old","new_text":"new","expected_matches":1}'
~/.local/share/ffsake/current/ffsake . --apply <token>
```

Semantic rename through an installed Rust/C++ language server:

```sh
~/.local/share/ffsake/current/ffsake . --request '{"op":"preview_semantic_rename","request":{"files":["src/lib.rs"],"path":"src/lib.rs","position":{"line":5,"column":7},"query":"rename","context":{"language":"rust","crate_root":"src/lib.rs","edition":"2021"},"new_name":"sum_all","limit":100}}'
~/.local/share/ffsake/current/ffsake . --apply <token>
```

Semantic coverage is incomplete: dependencies, sysroot, build scripts,
procedural macros, `cfg(test)` and integration-test code may be outside the
explicit snapshot. Run the project build and tests after a rename. See the
[measured boundary](BENCHMARK.md#language-intelligence-boundary-semantic-rename-and-reference-discovery-2026-11-09).

## Removal

```sh
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  configure --host claude --project /path/to/repo --remove --apply
```

This removes the project MCP entry, guidance block, hook or plugin. Other host
settings and permission rules are left alone. Use `--host opencode` for
OpenCode; remove a Codex MCP entry manually.
