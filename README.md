# FFSAKE

FFSAKE gives coding agents a governed workspace: scoped reads, one-call
debugging, a reviewed and journaled editor, selective semantic operations, and
Git, task and access capabilities through MCP.

FFSAKE's editor is preview/apply. Preview shows the exact diff and writes
nothing. Apply rechecks source hashes and match counts and commits every changed
file as one journal transaction with undo/redo. Native Edit/Write remain
available for quick changes; transactions applied through FFSAKE are the ones
that get review, stale-content protection, multi-file atomicity and history.

**Status: alpha (`v0.1.0-alpha.1`; [release notes](docs/releases/v0.1.0-alpha.1.md)).** Interfaces and setup details may still
change. FFSAKE does not sandbox project code or grant filesystem or host
permissions. Its transaction guarantees cover edits applied through FFSAKE;
native edits are outside that journal.

## Install

You need Git and Python 3.11+ for the installer. Release archives target macOS
ARM64 (macOS 14+) and Linux x86-64 (glibc 2.35+). Source installation also needs
Rust/Cargo and a C compiler. Windows is not supported. Language servers and
debug adapters are separate optional dependencies.

Release installation (`v0.1.0-alpha.1`):

```sh
git clone --depth 1 https://github.com/anasik/ffsake.git "$HOME/.local/src/ffsake"
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" install \
  --version v0.1.0-alpha.1
```

Source installation is also supported:

```sh
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" install --source --profile release
```

Install the binaries once per machine, then configure each project. The
recommended setup keeps native editing and adds the FFSAKE CLI for reads,
debugging and semantic operations. It also registers the MCP server for Git,
tasks, access tools and persistent debug sessions.

```sh
# Claude Code (CLI-first reads/debug/semantic; MCP kept for Git/tasks/access)
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  configure --host claude --project "$PWD" --cli-first --apply
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  doctor --host claude --project "$PWD" --cli-first

# OpenCode (CLI-first reads/debug/semantic; MCP kept for Git/tasks/access)
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  configure --host opencode --project "$PWD" --cli-first --apply
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  doctor --host opencode --project "$PWD" --cli-first
```

`configure` prints a review before writing. With `--cli-first` it:

- adds one marked guidance block to `CLAUDE.md` or `AGENTS.md`;
- installs a Claude `PreToolUse` hook or an OpenCode local plugin that serves
  native `Read` through `ffsake read`;
- merges the Claude hook into existing settings without replacing other keys,
  hooks or permissions; OpenCode gets the plugin and, separately, the MCP entry,
  but no `permission` entries are added;
- adds the MCP server entry for capabilities that need it.

To register only the MCP server, omit `--cli-first`. Codex uses the same
`configure --host codex` command; it receives guidance and the MCP entry, but
read redirection is not implemented for Codex.

FFSAKE does not add host permissions for the CLI or MCP tools. Allow the
commands and server in your host policy; for Claude Code an explicit
`Bash(ffsake:*)` rule is enough for the CLI, while MCP tools follow the host's
normal approval policy. The optional read redirect is itself an explicit host
policy: it serves Read through FFSAKE and FFSAKE's workspace policy governs that
call. Approve or trust the server if prompted. In an existing Claude chat,
reconnect the MCP server after configuration.

## What you can do

**Scoped reads and search.** Ask the agent to read ranges or search the project
through FFSAKE. The project root is the default scope. External paths require
an explicit access grant; a file grant does not open its parent directory.

**One-call debugging.** With a configured DAP adapter, `ffsake probe` launches
the program, stops at requested breakpoints, returns bounded stack/variable
inspection for one or more stops, and cleans up the owned process. Python
debugpy and C# netcoredbg are validated for launch; the installed Apple LLDB
adapter failed its C++ launch test. Adapters are not bundled. Persistent debug
sessions require `ffsake serve` or MCP.

**Reviewed, journaled editing.** FFSAKE's editor previews one or more changes,
shows the diff, and applies the frozen result:

- preview writes nothing;
- apply rechecks content hashes, match counts and analysis sources;
- applied FFSAKE edits form one journal transaction with undo/redo;
- stale content rejects instead of applying.

Use it when a change needs review, atomic multi-file application or history.
Native Edit/Write remain available and are usually faster for small,
unambiguous changes; they are outside FFSAKE's journal and cannot be undone
through it. Git operations, task execution and debuggee side effects are also
outside editor history. This is stale-state protection and recovery, not a
sandbox.

**Optional read redirection is host middleware, not an API restriction.**
Claude and OpenCode setup can serve the host's native `Read` through FFSAKE.
This does not remove FFSAKE's own read/write operations and does not disable
native Edit/Write. In Claude, the hook intercepts `Read` and returns an allow
decision, so FFSAKE's workspace policy governs that call; host path-specific
Read deny rules do not apply after the rewrite. OpenCode's plugin also rejects
shell file-read commands such as `cat` and `grep`. MCP-only setup leaves native
tools alone.

**Selective semantic operations.** For Rust and C++, the full profile exposes
bounded definition, reference, hover, symbol, diagnostics and rename proposals
through installed language servers. The one-shot CLI can preview and apply a
semantic rename with the same stale-source guards. Coverage is incomplete: the
explicit snapshot does not load Cargo dependencies, sysroot, build scripts,
procedural macros or configured features, and macro-expanded, `cfg(test)` and
integration-test code may be missing. Always run the project build and tests;
the macro-heavy `itoa` check applied a rename that broke the build until the
omitted macro-body references were fixed. C++ uses clangd with the same
limitations.

**Git, tasks and access through MCP.** The MCP server reviews selected-file
staging and commits, branch creation/switching/fetch/non-force push, named
project tasks with incremental output and cancellation, and persistent access
grants. Task execution is not sandboxed and is not part of editor history.
Force push, tags, merge, rebase and arbitrary refspecs are out of scope.

## Building a harness

The read redirect is optional host integration. A custom harness can call
FFSAKE directly and keeps full read/write access:

- **MCP over stdio:** `ffsake_read`, `ffsake_read_files`, `ffsake_preview_edit`,
  `ffsake_preview_files`, `ffsake_apply`, `ffsake_undo`/`ffsake_redo`, plus the
  Git, task, access and debug tools.
- **CLI:** `ffsake read`, `ffsake . --request '{"op":"preview_edit",...}'`, then
  `ffsake . --apply TOKEN`; `ffsake . --request '{"op":"undo",...}'`. Use
  `ffsake serve` when preview/match/debug handles must persist across calls.

Preview/apply checks hashes and match counts, and applied changes form one
journal transaction. Harness code that calls these endpoints is not affected by
the Claude hook or OpenCode plugin, and MCP-only setup does not modify native
host tools.

## Update and removal

```sh
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" check-update
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" update
```

Updates are never automatic. Pinned installs require an explicit version or
`--unpin`. Retained generations support `rollback`.

Remove FFSAKE from one project without touching unrelated host configuration:

```sh
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  configure --host claude --project "$PWD" --remove --apply
```

This removes the project MCP entry, the FFSAKE guidance block, the Claude hook
or OpenCode plugin. Other settings, permissions and instructions stay. Remove
the managed binaries with:

```sh
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" uninstall
```

`uninstall` leaves the source checkout, project files, journals and host
configuration in place. Remove those separately if desired.

## Honest limitations

- **Native editing is not journaled.** Only FFSAKE preview/apply transactions
  can be undone or redone through FFSAKE.
- **Read redirection changes the read authority.** Without `--cli-first`, the
  host remains the permission authority. The optional Claude hook or OpenCode
  plugin serves native Read through FFSAKE and allows the redirected read, so
  FFSAKE's workspace policy governs that call; host path-specific Read deny
  rules do not apply after the rewrite. Edit/Write are not intercepted. FFSAKE
  does not sandbox task execution.
- **No universal performance claim.** MCP adds catalog and per-turn cost; the
  earlier broad speed claims do not generalize. Choose the editing path per
  task: native Edit/Write for quick unambiguous changes, FFSAKE preview/apply
  when review, stale protection, multi-file atomicity or history matter. See
  the [benchmark evidence](docs/BENCHMARK.md) for the measured boundary.
- **Semantic coverage is incomplete.** Macros, dependencies, sysroot, build
  scripts, test configurations and generated code can be missing from the
  snapshot. A build/test check is mandatory; language-server acceptance is not
  compiler or semantic correctness.
- **Debug adapters are external.** Only debugpy and netcoredbg launch are
  validated locally; LLDB C++ failed. Attachment requires an explicit process
  selection and host approval.
- **Alpha interfaces.** Installation, configuration and tool details may change
  before a stable release.

## Development and references

- [Installation, configuration and troubleshooting](docs/INSTALL.md)
- [CLI-first setup details](docs/CLI_FIRST.md)
- [MCP and resident CLI contracts](docs/CLI.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Benchmark methodology and evidence](docs/BENCHMARK.md)
- [Roadmap and development record](PLAN.md)

FFSAKE uses [FFF](https://github.com/dmtrKovalenko/fff) as its search and
indexing substrate.
