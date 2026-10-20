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
- precise byte-range edits with content-hash guards;
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

## Scope

Phase 1: editing, navigation, search integration.

Phase 2: LSP-backed semantic navigation and refactoring.

Later: build/test execution, language adapters, sandboxing, and plugins.

Those later concerns must not block proving that reducing edit/navigation friction is useful on its own.

## Upstream

Search/indexing substrate: https://github.com/dmtrKovalenko/fff
