# ffsake

A headless, agent-facing repository editing and navigation substrate.

The immediate goal is deliberately narrow: make coding agents excellent at ordinary repository navigation and editing without falling back to fragile shell plumbing such as `grep`, `find`, `sed`, `cat`, `head`, or `tail`.

## Phase 1

The first implementation is `crates/fff-workspace`, a workspace-scoped file API with:

- ranged UTF-8 reads;
- directory listing;
- safe writes;
- exact, regex, and repo-wide replacement;
- arbitrary exact/glob/regex file targeting with exclusions;
- precise byte-range and multi-range edits with content-hash guards;
- editor-style selection planners for clear, duplicate, indent, outdent, copy, and move;
- non-committing transform previews with unified diffs;
- resident FFF-backed indexed content search that returns reusable path-qualified match sets;
- move/rename and delete;
- strict workspace containment and symlink-escape protection;
- an internal `.ffsake/` journal with undo/redo and stale-state guards.

## Mutation model

ffsake intentionally separates editor-style work from workspace-wide transformations.

### Editor session

An `EditorSession` can hold a sticky active target: a resolved set of files analogous to open files in an IDE, except the set may originally come from exact paths, globs, or regex selectors.

Each direct mutation against the active target is its own journal entry and is independently undoable.

### Transform

A `Transform` is the composition primitive for broad workspace mutations. It contains:

- one arbitrary file target;
- an ordered vector of typed mutation operations;
- optional fail-closed preconditions such as a maximum file count.

The entire transform is evaluated in memory and committed as one journal transaction. This is intended for global replace/refactor-style operations, including files that were never part of the active editor target.

There is no general condition/control-flow language. The LLM handles branching and orchestration; ffsake provides deterministic selectors, matches/ranges, preconditions, staging, mutation, and undo/redo.

### Search to mutation

A resident FFF index performs watched/indexed literal and regex content search. Search hits are converted immediately into owned `MatchSet` values containing workspace-relative byte ranges and source hashes. Match sets can compile directly into guarded transforms for replace or insert-before/after operations, so the model does not need to reconstruct locations from search output.

### Preview

The preview API runs the same transform staging path without committing it and returns per-file unified diffs. This keeps preview semantics aligned with the mutation that would actually be committed.

## Scope

Phase 1: editing, navigation, search integration.

Phase 2: LSP-backed semantic navigation and refactoring.

Later: build/test execution, language adapters, sandboxing, and plugins.

Those later concerns must not block proving that reducing edit/navigation friction is useful on its own.

## Upstream

Search/indexing substrate: https://github.com/dmtrKovalenko/fff

## Resident CLI

Run `cargo run --bin ffsake -- serve /absolute/workspace` and keep the JSONL stdin/stdout session open. See [the protocol and validation guide](docs/CLI.md).


## Resident interfaces

`ffsake serve WORKSPACE` exposes the native newline-delimited JSON resident interface. Keep the process alive so FFF indexing and server-owned MatchSet/preview handles remain resident.

`ffsake-mcp WORKSPACE` exposes a thin MCP stdio adapter over the same `Resident` backend. It does not duplicate mutation or search logic; MCP tools translate directly to the existing resident requests.

The reproducible benchmark and safety checks live under `scripts/benchmark.py` and `docs/BENCHMARK.md`. The MCP path has its own end-to-end smoke test in `scripts/mcp_smoke.py`.


## Real agent benchmark

After building the MCP binary, run a disposable controlled benchmark with an installed coding-agent CLI:

```sh
cargo build --locked --bin ffsake-mcp
python3 scripts/agent_benchmark.py claude
```

or:

```sh
python3 scripts/agent_benchmark.py codex
```

Use `all` to run both. The harness creates temporary fixture repositories, runs the same rename task once with the agent's normal tools and once with FFSAKE MCP, checks the exact resulting bytes, records structured agent traces, and writes the full result under `docs/agent-benchmark-runs/`. It never points the agent at this repository or another existing user repository.

Claude Code is invoked non-interactively with stream-JSON output and a temporary MCP config. The FFSAKE run disables its normal Bash/read/write/edit/search tools for the fixture. Codex is invoked with `codex exec --json` and a temporary project-level MCP configuration inside the disposable fixture.


### Multi-agent suite

The broader benchmark matrix supports Claude Code, Codex, and OpenCode across four task types:

- `micro`: deterministic repository-wide identifier rename;
- `medium`: implement a small request-policy engine in an existing file;
- `app`: complete a JSON-backed issue-tracker application scaffold with hidden verification;
- `project` (opt-in): create a three-file persistent CLI package from requirements, verified across separate processes.

Each agent is run in both baseline and FFSAKE modes. Use repeated runs and compare medians:

```sh
cargo build --locked --bin ffsake-mcp
python3 scripts/agent_benchmark_suite.py \
  --agents claude,codex,opencode \
  --tasks micro,medium,app \
  --repetitions 3
```

To test only the installed agents you care about, pass a smaller `--agents` list. OpenCode uses its configured default model unless `--opencode-model provider/model` is supplied.

Every run gets a fresh committed temporary Git repository. The harness performs correctness verification outside the agent session, retains raw JSON traces, and writes one aggregate report under `docs/agent-benchmark-runs/`.

MCP now exposes guarded multiline editing and file create/copy/move/rename/delete through preview/apply and undo/redo. File-operation destinations must be absent; directory operations are not exposed. Build/test execution remains external. The scaffold task is retained for historical comparisons; use `--tasks project` to measure creation from scratch.


## Use on a real repository with Claude

Build once, then launch an interactive session for a chosen workspace:

```sh
cargo build --locked --bin ffsake-mcp
python3 scripts/ffsake_claude.py /absolute/path/to/worktree
```

The launcher supplies a temporary MCP configuration, disables Claude's built-in tools with `--tools ""`, loads only this MCP configuration with `--strict-mcp-config`, and approves only the seventeen named FFSAKE tools for this session. It does not use the broad skip-permissions flag or modify user/project configuration. Other host settings, including enterprise policy and startup hooks, are not isolated by this launcher. FFSAKE is an application-level workspace boundary, not an OS sandbox.

Use a separate git worktree for the first pilot. Start with one small repository editing task; run builds/tests and review the git diff in a separate terminal. FFSAKE currently supplies editing/navigation, not a build/test runner. Existing-file replacements preserve current permissions. New Unix journal entries also capture ordinary rwx permission bits when committing a deletion, so undo can recreate executable/private files correctly. Older entries and record-after-the-fact calls lack original modes. Ownership, ACLs, extended attributes, special mode bits, destination modes for copy/move, and new-file modes across redo are not restored.

To preview the generated command and MCP configuration without starting Claude:

```sh
python3 scripts/ffsake_claude.py /absolute/path/to/worktree --dry-run
```

The launcher's CLI flags follow the [Claude CLI reference](https://code.claude.com/docs/en/cli-reference). Its process/configuration behavior is regression-tested with a fake CLI; interactive tool availability must still be checked with the installed Claude version.
