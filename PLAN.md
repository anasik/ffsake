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
- expose the resident runtime through the compact agent-facing interface;
- benchmark FFF search → MatchSet → preview/transform end to end;
- add richer FFF modes only when a concrete workflow requires them.

## Phase 1E — agent benchmark

Test Claude Code/Codex against representative repositories.

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
