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
- implemented: case-preserving ASCII identifier rename through a guarded transform and bounded MCP preview;
- implemented: bounded Tree-sitter Rust identifier selection and parser-checked replacement previews.

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

Boundary follow-up: an executable probe showed that single-file read, directory list and exact selection could follow an alias into `.ffsake`. Resolved paths now enforce the reserved namespace too, and recursive selection skips nested reserved directories. Ordinary in-workspace read aliases retain compatibility. Regression coverage includes read/hash/selection/write/delete/move and MCP endpoints.

Repository workflow evaluation: a disposable archive of `b7b02a5` covered 33 tracked files / 459,711 bytes, two batched reads, six mixed operations and byte-exact undo/redo. The executable deletion probe exposed mode loss (0755 → 0644); new Unix manifests now capture ordinary before-side rwx bits and restore them only when recreating an absent file. Older manifests load unchanged, and existing-file permissions retain precedence. Added `scripts/repository_smoke.py` to repeat this model-free integration check in CI. It is not a real-agent timing or isolation comparison.

Local validation passed: 53 Rust tests, 57 Python tests, Clippy, formatting, executable benchmark, MCP smoke, and the repository workflow including executable recreation. Parallel library-test fixtures now reserve unique directories atomically after an observed timestamp collision.

Copy/move destination and redo mode gaps are now covered: previews retain and display source rwx bits, apply rejects changed source modes before any batch member writes, and the journal stores the mode actually created for redo. Reopened copy/move histories and new-file modes have focused regression coverage. No new tool or user-controlled chmod operation was added.

History failure evaluation reproduced partial undo when a later atomic write failed. Undo/redo now reverse earlier members on write or final manifest failure, keep the previous persisted transaction state on successful recovery, and report incomplete rollback with affected paths. Rollback recreation preserves current Unix permissions. Deterministic obstruction tests cover failed undo/redo, retries, manifest failure, mode recovery and explicit incomplete-recovery reporting.

Persisted history now preflights both snapshot sides and verifies each hash again at use; missing/corrupt data rejects before workspace writes. Journal loads validate transaction identity, snapshot keys are bound to their original paths, and manifest/snapshot symlink components are rejected. Tests cover both directions, recovery data, repair-and-retry, malformed identity/keys and symlinked journal paths. Local validation passed 63 Rust tests, 57 Python tests and the full executable/MCP/repository checks.

Atomic-write probing reproduced a pre-existing temporary symlink redirecting bytes outside the target. Direct workspace writes and journal replay now share exclusive temporary-file creation, reject existing collisions without touching them, apply private-file permissions before writing content, and clean up owned temporary files after failure. The resident interface documentation now includes batch reads, mixed file previews and history guarantees.

Case-preserving textual rename is now implemented in the transform engine and `ffsake_preview_rename`. It uses explicit target/count/file limits and frozen previews; supported lower/upper/initial-capital styles are preserved, exact mixed-case source spelling maps to the supplied destination, and ambiguous mixed casing rejects. This is whole-ASCII-identifier text editing, including comments and strings, not semantic rename. Shared prompts and the eighteen-tool allowlist are updated, with smoke checking the actual server catalog against the allowlist.

Parser-backed Rust identifier selection is implemented with Tree-sitter and the Rust grammar. Explicit bounded files produce complete MatchSets with exact original byte ranges and hashes; comments, strings, lifetimes and opaque macro/attribute text are excluded. Parse errors, invalid names, stale content and exceeded limits fail closed. Raw/Unicode spelling and CRLF history are covered; this is not semantic resolution or compiler validation. The MCP catalog and shared allowlist now contain nineteen tools.

Real-source syntax evaluation parsed lib.rs, syntax.rs and resident.rs successfully. Rust syntax-selection handles now retain their origin: preview_matches validates the replacement spelling and reparses staged buffers before returning the existing guarded transform preview. Indexed-search handles retain literal replacements. Tests cover contextual names that produce parse errors, raw/Unicode names, oversized staged results, stale files and release. The disposable repository smoke now verifies exact source-range edits and complete undo/redo on repository Rust files; it does not claim the probe rename compiles or resolves symbols.

Compiler-backed evaluation now covers four disposable fixtures: ordinary binding rename compiles and passes; same-spelling unrelated bindings are both renamed; excluded macro arguments can leave unresolved references; and a new name can shadow another binding, compiling but changing runtime behavior. All preview/apply/undo/redo byte checks pass. scripts/rust_syntax_smoke.py records these intended limitations in CI, with no model calls.

Read-only rust-analyzer feasibility passed with an explicit dependency-free Rust project graph: each same-spelling binding produced only its own declaration/reference, including a declarative macro argument. The probe negotiates UTF-16, verifies byte offsets after emoji and CRLF, waits for quiescent healthy status, enforces frame/time/result-path limits, and checks an unchanged fixture including a dormant Cargo build-script sentinel. No production LSP endpoint is exposed yet; dependency/sysroot/procedural macro completeness is unverified.

Implemented strict UTF-16 ↔ original byte coordinate conversion and a bounded read-only Rust references endpoint. Each call uses a fresh explicit source snapshot, a declared crate root/edition, healthy server readiness, bounded protocol I/O and original hash rechecks. Returned ranges are confined to supplied files; no mutation handle is created and complete=false records the omitted dependencies/sysroot/features. Real MCP regressions cover same-spelling binding identity, declarative macros, cross-file ranges, Unicode/CRLF, fresh analyses and rejected boundaries. The shared catalog has twenty tools.

Definition navigation now shares the snapshot backend and all scope/hash/budget checks. It resolves local bindings (including macro arguments) and cross-file calls; empty-module definitions preserve legitimate zero-width file anchors while reference ranges remain nonempty. Single/array/null definition results are normalized without negotiating LocationLink, and the shared catalog contains twenty-one tools.

A real-source document-symbol probe produced 15 top-level entries / 40 symbols from the then-current LSP module. The shipped outline endpoint preserves hierarchy, names, kinds, details and exact original ranges, with total-count/depth/text limits and parent/selection containment checks. MCP coverage includes nested structs, fields, methods, emoji/CRLF offsets, empty documents and overflow rejection. The shared catalog contains twenty-two tools.

Implementation navigation now uses the same snapshot backend and result guards. Real MCP fixtures cover two trait implementations across files, trait-method implementations, one concrete type implementation, an empty result and overflow rejection. All results remain read-only and explicitly incomplete. The shared catalog contains twenty-three tools.

Next: add bounded hover/type information for local symbols, treating returned documentation as source data, preserving optional source ranges and retaining the same snapshot/incomplete-analysis contract. Keep dependency-aware analysis and semantic rename separate; reference ranges must not become rename capabilities until collision and analysis-completeness behavior are established. Semantic rename must not be inferred from syntax or successful compilation. Reliability checks remain application-level error recovery, not crash-proof transactions or an adversarial filesystem sandbox.

Remaining gaps:
- build/test/runtime execution remains external;
- directory operations are not exposed by the MCP journal;
- metadata beyond ordinary Unix rwx bits remains unrecorded, and legacy manifests cannot recover metadata they never captured;
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

Implemented through an explicitly incomplete dependency-free rust-analyzer source snapshot:

- definitions;
- references;
- hierarchical document symbols;
- implementations.

Still to add/evaluate:

- semantic rename;
- hover/types;
- diagnostics/code actions.

## Later

Build/test/runtime adapters, sandboxing, package-management support, and plugin architecture are tertiary.
