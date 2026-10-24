# Action plan

## Phase 1A — workspace primitives

Implemented/active:

- ranged file reads;
- directory listing;
- safe writes;
- exact, literal, regex, and repo-wide replacement;
- move/rename and delete;
- exact/glob/regex target selection with exclusions;
- precise byte-range edits;
- content-hash preconditions;
- protected internal `.ffsake/` namespace;
- persistent undo/redo journal.

Security invariant: all user-visible paths are workspace-relative and remain inside the canonical workspace root, including through symlinks. The workspace root itself is never a valid mutation target.

## Phase 1B — mutation model

### Direct editor operations

Use an `EditorSession` with an optional sticky active target.

The active target is a resolved file set, analogous to open files in an IDE. It may originate from one path, many paths, directories, globs, regexes, or all files.

Each direct mutation is independently journaled and undoable. Keeping the same target active does not group later edits into one transaction.

### Global transforms

Use:

`Transform { target, operations, preconditions }`

A transform is the composition primitive. It stages all selected files in memory, applies its ordered mutation vector, validates preconditions, and commits the entire workspace mutation as one journal transaction.

Do not add a second scripting layer on top of transforms.

Do not add general `if/else`, loops, variables, recursion, or orchestration. The LLM handles control flow. ffsake only needs fail-closed preconditions such as expected match counts, file hashes, and maximum target size.

## Phase 1C — editor-grade mutation

Implemented:
- line/column coordinate conversion over canonical byte offsets;
- insert/delete/replace exact ranges;
- multi-range edits (the useful semantics of multi-caret editing without GUI caret state);
- match-relative insertion/deletion;
- lexical whole-identifier replacement;
- path-qualified line delete/duplicate/indent/outdent;
- arbitrary selection clear/duplicate plus range-to-line indent/outdent planning;
- same-file and cross-file selection copy/move planning;
- guarded selection transforms with source-hash preconditions;
- dry-run transform previews and per-file unified diffs.

Next primitives:
- case-preserving textual rename after the search/mutation benchmark;
- syntax-aware selection and identifier operations when Tree-sitter is introduced.

Target principle:

`LLM decides intent -> runtime performs exact mutation`

rather than:

`LLM rereads file -> regenerates text -> overwrites file`

## Phase 1D — FFF search integration

Implemented:
- FFF pinned as the resident indexed/watched search backend;
- literal and regex content search through FFF's public `FilePicker::grep` API;
- all search results re-scoped through ffsake's safe `FileSelector`;
- owned `MatchSet` values with exact path-qualified byte ranges and source hashes;
- direct MatchSet → guarded Transform conversion for replace and insert-before/after;
- staged hash assertions plus journal commit guards to reject stale search results.

Next:
- implemented: resident JSONL CLI, workspace-scoped handles, guarded preview/apply;
- implemented: repeatable FFF search → MatchSet → preview → transform → undo benchmark (see docs/BENCHMARK.md);
- add richer FFF modes only when a concrete workflow requires them.

## Phase 1E — agent benchmark

Synthetic executable benchmark is implemented and passing. The real-agent harness now covers Claude Code, Codex, and OpenCode in baseline/FFSAKE modes across micro, medium, application-completion, and from-scratch project tasks with repeated runs and aggregate medians.

Implemented after benchmark feedback:
- guarded multiline block preview/edit/apply;
- file create/copy/move/rename/delete previews with journal apply and undo/redo;
- a from-scratch CLI package task with separate-process persistence checks;
- an opt-in existing-project maintenance task covering schema migration, mixed file operations, byte preservation and strict validation in separate CLI processes;
- mixed batches of existing-file literal edits and create/copy/move/delete operations, with one journal transaction and undo/redo;
- executable permission preservation when edits replace existing files, including undo/redo.

Verified repeated project fixture: 12/12 correct runs, with Claude median time down 42.1% and reported median cost down 48.9%; Codex median time was 1.1% higher. Every FFSAKE run used only FFSAKE tools; this does not establish host-enforced isolation or general performance results.

Real-repository pilot completed: Claude used only FFSAKE tools to implement the existing-file permission fix and its Unix regression test. The host ran validation separately; a parallel test-fixture collision was subsequently fixed.

Verification on 2026-10-24: the user's macOS run passed all 47 Rust tests and the MCP smoke suite, including mixed batch history, stale-content rejection, rollback, and 109 rejected boundary operations. CI also passed 51 Python tests, Clippy, and formatting at `a4c947ca50a766a0fd64e37ff0cfcf7967df9083`.

Maintenance diagnostic on 2026-10-24: both FFSAKE agents used one seven-operation batch and one apply and passed 26/26 checks. Codex baseline also passed; Claude baseline passed all behavior/content checks but generated Python bytecode during its own CLI verification, failing the exact-file-set audit. The harness now suppresses bytecode through a shared child-process environment rather than excluding unexpected files. See docs/BENCHMARK.md for timings and limitations.

The completed post-fix maintenance report has 12/12 correct, valid runs. Median baseline → FFSAKE time: Claude 19.333 → 13.555 s, Codex 38.514 → 32.872 s. Codex still used 24 median calls. Added bounded explicit multi-file reads with missing-file results, shared agent guidance and allowlists, preserving single-file compatibility and mutation guards. See docs/BENCHMARK.md for limits and evidence. These small repeated samples do not establish universal performance or host-enforced isolation.

Targeted Codex diagnostic confirmed adoption: 26/26 checks, four calls (batch read → seven-operation preview → apply → batch verification), 28.637 s. This is one timing sample, not a repeated speed claim. All required local checks passed: 48 Rust and 57 Python tests, Clippy, formatting, executable benchmark and MCP smoke (130 rejected operations).

Next: evaluate larger real-repository workflows before adding more interface primitives; keep runtime execution and metadata recreation gaps explicit.

Remaining gaps:
- build/test/runtime execution remains external;
- directory operations are not exposed by the MCP journal;
- metadata restoration when files are recreated (for example, undoing deletion) is not implemented; existing-file edits preserve permissions;
- native Codex tools are not disabled; enforced isolation remains unverified.

Pass condition:

- ordinary exploration/editing completes without shell grep/find/sed/awk/cat/head/tail;
- no recursive-search hangs;
- deterministic edits;
- safe global transformations;
- reliable undo/redo;
- fewer tool round-trips than baseline.

## Phase 2 — syntax and LSP

Before full LSP, consider parser/tree-sitter-backed syntax-aware mutation for identifiers, comments, strings, imports, and AST node kinds.

Then add LSP/compiler-backed:

- definitions;
- references;
- implementations;
- symbols;
- semantic rename;
- hover/types;
- diagnostics/code actions.

## Later

Build/test/runtime adapters, sandboxing, package-management support, and plugin architecture are tertiary.
