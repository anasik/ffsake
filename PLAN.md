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


## Generic file selection and batching

File selection is independent of the operation being performed.

Every batch-capable operation should accept the same selector model:

- `all` / `*` — literally every file in the workspace;
- exact file paths;
- exact directory paths, selecting descendants;
- multiple explicit files/directories;
- glob path selectors;
- regex path selectors;
- arbitrary unions of all of the above;
- exclusions applied after the include union.

Examples:

- one file: `["src/lib.rs"]`
- listed files: `["src/a.rs", "src/b.rs"]`
- file + regex: exact `README.md` union regex `^src/.*\\.rs$`
- all Rust files: regex `\\.rs$`
- literally all files: `*`
- all except generated files: `*` minus regex/glob exclusion

This selector must be reusable by replacement, reads, deletion, metadata inspection, formatting, future transformations, and any other sensible file-scoped operation. Batch execution is therefore a workspace primitive, not a replace-specific feature.


## Editor-grade mutation before LSP

The product should prefer deterministic editor operations over LLM-authored file rewrites.

Target principle:

`LLM decides intent -> runtime performs exact mutation`

rather than:

`LLM rereads file -> regenerates text -> overwrites file`

Before LSP, add editor-grade textual/structural operations such as:

- exact range replace by byte/line/column coordinates;
- insert/delete at exact positions;
- multi-cursor style edits across arbitrary file selectors;
- replace-all with literal or regex patterns;
- whole-identifier replacement with language-aware identifier boundaries;
- case-preserving rename variants where requested;
- occurrence selection (nth match, matches in selected ranges, matches after/before coordinates);
- move/copy selected ranges;
- indent/outdent selected ranges;
- duplicate/delete lines or ranges;
- sort/deduplicate selected lines where appropriate;
- transactional multi-file mutations;
- file-content hash/version preconditions;
- dry-run previews and unified diffs.

A useful intermediate layer before LSP is syntax-aware mutation using parsers such as tree-sitter. This can distinguish identifiers/comments/strings and target AST node kinds without requiring a running language server.

Rename should therefore have tiers:

1. textual rename: exact/regex replace;
2. identifier-aware rename: token-boundary or parser-aware;
3. syntax-aware rename: AST-scoped where supported;
4. semantic rename: LSP/compiler-backed, added later.

The goal is to beat naive LLM mutation even before semantic tooling exists.


## Command language

Consider a compact command syntax with two orthogonal parts:

1. a target expression selecting an arbitrary set of files;
2. a pipeline of deterministic ffsake operations applied to that target.

Conceptually:

`target -> op -> op -> op`

Target expressions should support unions of exact paths, directories, regex paths, globs, exclusions, and `*` for the entire workspace. Operations should be typed ffsake primitives rather than shell commands.

The human/LLM-facing DSL should compile to a structured internal plan before execution. The structured plan is the source of truth for validation, preview, transactions, audit, and MCP/tool use.

Potential syntax direction:

`[src/a.rs, re:^src/.*\\.rs$, !re:^src/generated/] | replace /Foo/ Bar | format`

or

`* | where path ~= /\\.rs$/ | replace /Foo(\\w+)/ Bar$1 | write`

Prefer explicit prefixes for regex/glob/exact selectors to avoid ambiguity.

Pipelines should be transactional by default for mutations, or at minimum support an explicit transaction wrapper. Dry-run/preview should expose the resolved target set and exact mutation plan before commit.
