# FFSAKE

FFSAKE is complementary tooling for coding agents. It does not replace your
host's editor: ordinary file changes stay with native Edit/Write. FFSAKE adds
scoped repository reads, one-call debugging, optional reviewed and journaled
edits, selective semantic operations, and MCP-only capabilities such as Git
review and named build/test tasks.

**Status: alpha (`v0.1.0-alpha.1`; [release notes](docs/releases/v0.1.0-alpha.1.md)).** Interfaces and setup details may still
change. FFSAKE does not sandbox project code, does not grant filesystem or host
permissions, and does not make native edits transactional.

## Install

You need Git and Python 3.11+ for the installer. Release archives target macOS
ARM64 (macOS 14+) and Linux x86-64 (glibc 2.35+). Source installation also needs
Rust/Cargo and a C compiler. Windows is not supported. Language servers and
debug adapters are separate optional dependencies.

Release installation (after the first alpha archive is published):

```sh
git clone --depth 1 https://github.com/anasik/ffsake.git "$HOME/.local/src/ffsake"
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" install
```

Before the first archive is published, build from source:

```sh
git clone https://github.com/anasik/ffsake.git "$HOME/.local/src/ffsake"
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" install --source --profile release
```

Install the binaries once per machine, then configure each project. The
recommended setup keeps native editing and adds the FFSAKE CLI for reads,
debugging and semantic operations. It also registers the MCP server for Git,
tasks, access tools and persistent debug sessions.

```sh
# Claude Code (CLI-first; native Edit/Write stays the default)
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  configure --host claude --project "$PWD" --cli-first --apply
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  doctor --host claude --project "$PWD" --cli-first

# OpenCode (CLI-first; native edit/write stays the default)
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  configure --host opencode --project "$PWD" --cli-first --apply
python3 "$HOME/.local/src/ffsake/scripts/ffsake_install.py" \
  doctor --host opencode --project "$PWD" --cli-first
```

`configure` prints a review before writing. With `--cli-first` it:

- adds one marked guidance block to `CLAUDE.md` or `AGENTS.md`;
- installs a Claude `PreToolUse` hook or an OpenCode local plugin that serves
  native `Read` through `ffsake read`;
- merges those entries into existing project settings without replacing other
  keys, hooks or permissions;
- adds the MCP server entry for capabilities that need it.

To register only the MCP server, omit `--cli-first`. Codex uses the same
`configure --host codex` command; it receives guidance and the MCP entry, but
read redirection is not implemented for Codex.

FFSAKE does not grant permissions. Allow the CLI commands and MCP server in
your host policy; for Claude Code an explicit `Bash(ffsake:*)` rule is enough
for the CLI, while MCP tools follow the host's normal approval policy. Approve
or trust the server if prompted. In an existing Claude chat, reconnect the MCP
server after configuration.

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

**Optional reviewed edits.** Native Edit/Write is faster and remains the
default. When you want a reviewable transaction, the agent can preview one or
more changes, show the diff, and apply the frozen result:

- preview writes nothing;
- apply rechecks content hashes, match counts and analysis sources;
- applied FFSAKE edits form one journal transaction with undo/redo;
- stale content rejects instead of applying.

This is stale-state protection and journaled recovery, not a sandbox. Edits made
with native tools are outside FFSAKE's journal and cannot be undone through it.
Git operations, task execution and debuggee side effects are also outside editor
history.

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
- **No permission shortcut.** FFSAKE enforces its own workspace policy, but the
  host remains the permission authority. It does not reduce or bypass host
  approval settings, and it does not sandbox task execution.
- **No universal performance claim.** MCP adds catalog and per-turn cost; the
  earlier broad speed claims do not generalize. Use native editing by default
  and FFSAKE where scoped reads, debugging, reviewed transactions or semantic
  operations help. See the [benchmark evidence](docs/BENCHMARK.md) for the
  measured boundary.
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
