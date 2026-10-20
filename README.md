# ffsake

A headless, agent-facing repository editing and navigation substrate.

The immediate goal is deliberately narrow: make coding agents excellent at ordinary repository navigation and editing without falling back to fragile shell plumbing such as `grep`, `find`, `sed`, `cat`, `head`, or `tail`.

## Phase 1

The first implementation is `crates/fff-workspace`, a workspace-scoped file API with:

- ranged UTF-8 reads;
- directory listing;
- atomic writes;
- deterministic exact replacement with occurrence guards;
- move/rename;
- delete;
- rejection of absolute paths and `..` traversal;
- canonical workspace containment;
- symlink-escape protection.

Search/indexing will reuse FFF rather than reimplement it. The intended next step is to expose this workspace layer beside FFF's existing MCP search tools.

## Scope

Phase 1: editing, navigation, search integration.

Phase 2: LSP-backed semantic navigation.

Later: build/test execution, language adapters, sandboxing, and plugins.

Those later concerns must not block proving that reducing edit/navigation friction is useful on its own.

## Upstream

Search/indexing substrate: https://github.com/dmtrKovalenko/fff
