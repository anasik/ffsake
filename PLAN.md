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

Hover/type information now shares the snapshot transport, with a 1–65,536-byte text budget, at most 32 normalized content parts, optional original source ranges and no mutation handle. Markdown/plaintext/code formats and documentation remain data. Coverage includes local inferred integer types, documented signatures, emoji/CRLF ranges, null hover and overflow/schema rejection. The shared catalog contains twenty-four tools.

Native diagnostic evaluation isolated a startup-order issue: opening documents before the explicit project finished loading silently suppressed syntax errors, while opening after healthy/quiescent readiness returned parser, local type and unresolved-import diagnostics. The backend now opens documents after readiness. A bounded primary pull-diagnostics endpoint reports native experimental diagnostics with exact original ranges, source hashes, count/text limits and explicit incomplete/non-compiler metadata. Real MCP fixtures cover syntax anchors, local and cross-file type mismatches, emoji/CRLF, omitted-dependency errors, a fresh clean result and an untouched build-script sentinel. The shared catalog contains twenty-five tools.

Code-action evaluation found plain text edits for local type annotations/extraction and resource operations for module extraction. Read-only discovery now returns bounded action metadata and exact original-byte edits for text-only actions. Commands, resources, annotations, snippets, disabled/unresolved actions return explicit reasons and no partial edits. A real-server fixture verifies a Unicode/CRLF type annotation, independently compiles/runs its proposed bytes, and verifies module extraction never creates a file. All discovery remains explicitly incomplete and creates no mutation capability. The shared catalog contains twenty-six tools.

Guarded action previews now select a frozen server-owned discovery by action-set/index, exact title and edit count. The resident cache shares the handle limit and has a 4 MiB report budget. Preview rechecks every analysis source, stages text edits and reparses all results; apply retains all original hashes, including unedited files. Unsupported actions cannot yield previews. Tests cover multi-file history, invalid staged syntax, identity/count guards, stale unedited files at preview/apply, released discovery sets, one-shot apply and exact Unicode/CRLF undo/redo. The shared catalog contains twenty-seven tools.

Compiler/runtime evaluation of native actions now records four cases in scripts/rust_action_smoke.py and CI: a type annotation passes; promoting a local constant can shadow an outer constant and change behavior despite compilation; omitted standard-library macro context can leave an unresolved reference; and a generated todo!() placeholder compiles but panics. Every parser-checked preview and byte-exact undo/redo passes. Negative outcomes are explicit expected evidence, not skipped checks or claims of safe semantic refactoring.

Native prepare-rename/rename feasibility is now captured by scripts/rust_rename_probe.py: distinct bindings, declared macro references and cross-file Unicode/CRLF proposals pass independent compiler/runtime checks; omitted standard-library macros leave unresolved references, and an accepted colliding name compiles with changed behavior. Module rename yields a resource operation that the probe inspects without applying. Analysis sources remain unchanged and no production rename endpoint is added.

Explicit multi-crate snapshot context is implemented as an optional crates array on every Rust analysis request. Bounded declared roots/editions/dependency names map only to supplied files; cycles, unknown roots and oversized graphs reject before startup. A model-free comparison changed one declaration-only reference to three cross-crate references and cleared unresolved imports. Dependency type changes produce fresh diagnostics and invalidate cached actions/previews through all-input hashes. No Cargo discovery/builds/downloads, sysroot or implicit dependencies are added, and complete=false remains mandatory.

Explicit per-crate features/custom cfg flags are now bounded and validated. Names are literal, duplicate/oversized entries and reserved platform/compiler flags reject; no Cargo feature expansion or configuration discovery is performed. Real MCP tests show active/inactive definitions and changing type diagnostics. Every analysis returns its exact context, and a preview retains the context of its frozen action even after another analysis uses different flags.

Full-repository LSP evaluation now archives the committed source into a disposable workspace and covers all 23 Rust files as three explicit crates. At e2fe458 it found 19 Utf16Position references across six files and 53 outline symbols, resolved a cross-module definition and constant hover, and preserved whole-snapshot bytes through a native type-annotation preview/apply/undo/redo. CI repeats the model-free check. Single local timings are recorded separately in docs/BENCHMARK.md and are not agent-performance claims.

Ambient-config regression reproduced silent suppression of a type diagnostic by the user-global exclusion list. Initialization now pins empty diagnostic/file exclusions and disables workspace-discovery commands. The calibrated temporary-config test verifies diagnostics and definition scope, unchanged source/configuration and a discovery-command sentinel that never executes. Workspace-only cfg settings in a global file are invalid, not a supported override; the existing unhealthy-server rejection is preserved. Production does not use the test-only config-directory hook or claim full environment/OS isolation.

Source-loading regression now calibrates module paths, builtin include! and include_str! with explicit-file positive controls. Fifteen real-server cases distinguish supplied relative files from omitted files, absolute external/original paths and escaping paths; external sentinel contents never appear, definition locations remain confined, and fixture bytes remain unchanged. This observes exposed results, not process read access or OS isolation.

Bounded native rename discovery is now exposed as read-only proposals. It requires prepare-rename support, validates an exact Rust identifier and the prepared original range, and reuses whole-workspace-edit scope/overlap/resource/budget checks. All analysis hashes and explicit context are retained; no cache or mutation handle is created. Real MCP fixtures preserve distinct bindings, declared macros and cross-file Unicode/CRLF, while independently reproducing omitted-macro compilation failure and binding-collision behavior changes. Resource rename withholds every edit. The shared catalog contains twenty-eight tools.

Guarded native rename previews now cache frozen reports alongside actions under shared count/byte budgets. Exact selected path/start/text, requested name and edit count identify the proposal; all input hashes are checked at preview and apply. Parser validation and the existing one-shot journal preserve byte-exact undo/redo without claiming semantics. Real MCP coverage includes stale unedited context, release/replay/type-confusion rejection and the same compiler/runtime negative cases. The shared catalog contains twenty-nine tools.

Full-repository rename evaluation now runs independent offline Cargo tests in a disposable archive. At 835c6f1 the baseline passed 98 Rust tests, while the native type rename changed 22 occurrences in seven files but left five macro-contained references, producing expected E0422/E0425 errors. The regression rejects unrelated failures and checks byte-exact history. No actual checkout changes or compiler-validation claim are made by analysis endpoints.

Explicit external-macro regression now compiles a real helper rlib and compares omitted versus declared graph edges. Identity, repeated-expression and hygienic-local macros all produce one incomplete edit/E0425 without the edge, versus two source edits and compiler/runtime success with it. Repeated expansions deduplicate original argument edits; unrelated macro-local bindings stay unchanged. UTF-16/CRLF positions and byte-exact history pass. No fabricated standard-library context is shipped.

Workspace diagnostics now collect selected primary file reports in one fresh analyzer session with shared count/text/time budgets. Optional paths selects reports while all supplied files remain hash-checked context; clean files are included. Real-server reports match individual queries, and controlled-server checks reject malformed later reports and changed unqueried sources. The full dependency-incomplete repository exceeds the 1000-diagnostic batch cap, so report selection is supported without loosening limits. The shared catalog contains thirty tools.

Selected diagnostic evaluation at 59c0db9 retained all 24 source files and produced identical reports for coordinates.rs/edits.rs (65 diagnostics, 3579 text bytes). Five alternating local samples measured 0.480 s median for one batch versus 0.954 s for two individual requests. CI checks report equivalence with no timing threshold; these are model-free, dependency-incomplete observations, not agent speed or compiler accuracy claims.

Catalog audit measured 34,866 UTF-8 bytes for all thirty tools versus 11,691 for the eighteen generic file tools. An opt-in --profile core now advertises only those tools and rejects Rust-specific dispatch before backend work. The default/all catalog and all workspace/history protections remain unchanged. Real-process checks verify catalog equality, disabled calls, no analyzer launch, malformed arguments and core Unicode/CRLF executable-file history. This does not measure agent speed/cost or establish host isolation.

Parallel validation exposed a timestamp-only fixture collision: concurrent transform tests shared inputs and observed one match instead of three. Remaining timestamp-only helpers now atomically reserve temporary directories. A 32-worker isolation regression and twenty complete Rust suite repetitions at 32 test threads pass; production transaction guards are unchanged.

The shared benchmark harness now accepts --tool-profile all|core, with matching guidance, launch configuration, trace auditing and schema-version-3 result/aggregate metadata. Default all guidance remains byte-identical; baseline configuration is unchanged. Seventy Python tests and the real core catalog preflight pass without model calls. The server core allowlist explicitly excludes future tools. User-local benchmark changes remain untouched, and OpenCode checks are mocked only.

The explicitly approved core-profile Codex maintenance diagnostic at a807c4f passed 26/26 checks with a valid trace: read_files, preview_files, apply, read_files; all four succeeded with no observed policy/profile violations. Its 32.869 s single sample establishes adoption, not speed/cost or host-isolation benefits. No further model run is authorized by this one-run approval.

The stalled CI run on 0d77ab9 succeeded on retry. Core profile, fixture isolation, harness integration, adoption evidence and provenance were pushed as separate commits through 4a7df55; its exact CI run passed.

Benchmark provenance now records source revision, harness/MCP hashes, exact submitted prompt hash and initial task-file identities (paths, kinds, permissions and byte hashes) before runner edits. Identities distinguish local fixtures without following symlinks or claiming that the binary matches source. Seventy-four model-free Python tests cover changed bytes/names/modes, root independence, external symlink privacy, local script/binary changes and capture-before-mutation.

Native shorthand-rename regression now verifies exact field/local/pattern binding preservation, including zero-width insertions and expanded replacement text. All three fixtures pass external rustc/runtime and byte-exact Unicode/CRLF undo/redo. CI includes the regression without weakening the existing identifier-only checks.

Imported-definition rename now has raw-LSP and MCP coverage showing that local aliases remain unchanged and external compilation/runtime/history pass. Alias renaming itself is rejected by the tested native analyzer; regression checks require unchanged source, no handle and no journal state. No textual fallback was added.

Shorthand and import-alias regressions were pushed separately through 2656a9b and their exact CI run passed.

Combined symbol inspection now collects definition, references and hover at one position in one fresh analyzer session. Location entries share the caller's 1..1000 limit before deduplication, hover has an 8192-byte cap, and all input hashes/time/transport guards remain shared. Real-server equivalence, empty results and controlled late-malformed/overflow/stale-context failures pass. Full catalog has 31 tools; core remains 18. Five alternating model-free samples on the 2656a9b source archive returned identical 23 locations/296 hover bytes, with medians 0.506 s combined versus 1.516 s for three individual requests. This is not an agent-performance claim.

The committed inspection feature f5ec0b3 passed the full-repository equivalence, external Cargo baseline/expected-negative rename and byte-history regression, and was pushed for exact CI verification.

The interactive Claude launcher now accepts --tool-profile all|core, sharing catalog arguments, approvals and guidance with the harness. Its core launch checks the selected custom binary's catalog before starting Claude; dry-run remains configuration-only. Fake-CLI tests cover catalog mismatch prevention, actual core configuration, working directory, temporary-config lifetime and exit status without model calls. The README's stale fixed tool count was replaced with the selected-profile contract.

Launcher validation passed all 76 model-free Python tests and the change is committed separately as 44d1d4d, pending inspection CI.

Native rejection messages now retain the method, signed error code and bounded quoted reason, addressing the observed opaque alias-rename failure. Invalid/oversized messages use fixed fallbacks; arbitrary backend data is withheld. All 104 Rust tests, Clippy, the real rename matrix and controlled failure/recovery/process-cleanup checks pass.

Active navigation guidance now describes explicit optional local crate context, and profile documentation distinguishes the current full catalog from historical thirty-tool measurements. Core guidance and historical numeric evidence remain unchanged.

Next: publish queued commits after inspection CI passes and verify their exact head. Keep subsequent work driven by observed workflow gaps; no additional model run is authorized by the earlier single-run approval. Reliability checks remain application-level error recovery, not crash-proof transactions or an adversarial filesystem sandbox.

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

Implemented through explicitly incomplete rust-analyzer source snapshots, optionally with declared local crate dependencies:

- definitions;
- references;
- hierarchical document symbols;
- implementations;
- hover/types;
- native primary diagnostics (explicitly incomplete, not compiler validation);
- read-only code-action discovery with bounded text proposals and unsupported-action classification;
- guarded plain-text action previews/apply through the existing journal;
- native rename proposals and guarded text previews/application with explicit correctness limitations.

Still to add/evaluate:

- broader action/rename correctness boundaries and project context.

## Later

Build/test/runtime adapters, sandboxing, package-management support, and plugin architecture are tertiary.
