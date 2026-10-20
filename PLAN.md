# Action plan

## Phase 1A — workspace primitives

Implement and verify:

- `read_file(path, start_line?, end_line?)`
- `list_dir(path)`
- `write_file(path, text)`
- `replace_exact(path, old, new, expected_occurrences)`
- `move_path(from, to)`
- `delete_path(path)`

Security invariant: all paths are workspace-relative and remain inside the canonical workspace root, including through symlinks.

## Phase 1B — FFF MCP integration

- reuse FFF's current indexed search;
- expose workspace operations beside `find_files`, `grep`, and `multi_grep`;
- teach MCP instructions to prefer workspace tools over shell equivalents;
- no generic shell/exec tool.

## Phase 1C — editing quality

- multi-file transactions;
- line/byte-range replace;
- unified diffs after mutations;
- optimistic file-version/hash preconditions;
- undo token;
- recursive tree listing with limits and pagination.

## Phase 1D — agent benchmark

Test Claude Code/Codex against representative repositories.

Pass condition:

- ordinary exploration/editing completes without shell grep/find/sed/awk/cat/head/tail;
- no recursive-search hangs;
- deterministic edits;
- fewer tool round-trips than baseline.

## Phase 2 — LSP

Only after Phase 1 proves useful:

- definitions;
- references;
- implementations;
- symbols;
- rename;
- hover/types;
- diagnostics/code actions.

## Later

Build/test/runtime adapters, sandboxing, package-management support, and plugin architecture are tertiary.
