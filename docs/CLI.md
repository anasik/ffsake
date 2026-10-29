# Resident agent interface

Build with `cargo build --bin ffsake`, then start `target/debug/ffsake serve /absolute/workspace` once. Keep its stdin/stdout pipes open across tool requests. The explicit root is trusted startup configuration; request paths remain workspace-relative. EOF terminates the process. No TCP listener or implicit shell/job execution is included. Optional semantic requests use installed language servers. Named project tasks require host approval; an explicit startup policy restricts execution to its fixed jobs.

Requests and responses are newline-delimited JSON. Each request produces one flushed response, in order: `{"ok":true,"result":...}` or `{"ok":false,"error":"..."}`. Errors do not terminate the session. Startup failures exit nonzero. Requests exceeding 1 MiB are drained and rejected. Diagnostics use stderr. Use JSON serialization, not shell string interpolation.

## Search → review → apply → undo

Send each line after reading the preceding response; substitute returned handles/transaction IDs:

```json
{"op":"search","pattern":"needle","limit":1000}
{"op":"preview_matches","handle":1,"replacement":"replacement","expected_matches":400}
{"op":"apply","handle":2}
{"op":"undo","transaction":"RETURNED_ID"}
```

Search returns a resident MatchSet handle, match/file counts, and FFF's `next_file_offset`. Set `details:true` to include path-qualified byte ranges. It does not echo file contents or hashes. Preview returns a separate handle plus existing per-file unified diffs. Apply consumes that preview on success and returns a persistent journal transaction ID and changed-file count. Failed apply retains the preview for inspection/retry; stale input requires searching/previewing again. Replacement text is literal, including for regex search.

Search defaults to literal, case-sensitive mode, 100 results, offset 0. Set `regex:true` for regex. Limits are 1–1000. A MatchSet covers the returned page only: expected_matches applies to that page, not every possible repository match. Continue with the returned file offset according to FFF pagination; never interpret a page as proof of repository-wide completeness. Target filtering happens after FFF paging and can produce empty scoped pages. After CLI apply/undo/redo, the next search automatically waits for an index refresh. `refresh` explicitly waits for a full FFF rescan when external changes or watcher latency matter; it does not update existing handles.

## Other requests

| Request | Behavior |
| --- | --- |
| `{"op":"read","path":"src/lib.rs","start":1,"end":40}` | Ranged read, line numbers are one-based |
| `{"op":"read_files","files":[{"path":"src/lib.rs","start":1,"end":40},{"path":"AGENTS.md"}]}` | Ordered bounded reads with explicit `ok`/`missing` status |
| `{"op":"list","path":"."}` | Safe directory listing |
| `{"op":"target","target":{"include":[{"Glob":"src/**/*.rs"}],"exclude":[]}}` | Resolve sticky search scope; explicit search target overrides it |
| `{"op":"redo","transaction":"ID"}` | Journal redo with state verification |
| `{"op":"release","handle":1}` | Release MatchSet or preview storage |
| `{"op":"refresh"}` | Wait for index refresh |

A selector has `include` and `exclude` arrays. Elements are `"All"`, `{"Exact":"path"}`, `{"Glob":"pattern"}`, or `{"Regex":"pattern"}`. Empty includes select nothing. Set an All target to reset sticky scope. Exact directories select descendants.

For mutations beyond MatchSet replacement, `preview` accepts the existing `Transform` data structure, without a second scripting language. For example:

```json
{"op":"preview","transform":{"target":{"include":[{"Exact":"src/lib.rs"}],"exclude":[]},"operations":[{"ReplaceIdentifier":{"from":"Old","to":"New","expected_matches":2}}],"max_files":1}}
```

The same representation exposes range/position/line mutations, match-relative insert/delete, hash assertions, and file deletion from `transform.rs`. Enum names and fields follow that Rust API exactly. Client-authored generic transforms are still validated by the existing runtime. Selections can be expressed as path-qualified ApplyRanges; higher-level selection planning remains in the library.

All preview plans freeze selected file membership and assert source hashes again when applied. Existing workspace confinement, internal namespace protection, range validation, max-file checks, and journal commit/undo guards remain in force. The CLI validates transaction IDs before calling the journal. Handles are process-local, monotonic, and limited to 128 live MatchSets/previews combined; release unused handles. Journal transaction IDs survive restarts. The client owning the process is trusted to mutate the selected workspace; this is not a multi-user authorization service.

## Batched reads and edits

`read_files` accepts 1–32 explicit `{path, start?, end?}` entries. Each returned `files` entry has the requested `path` and either `status: "ok"` with a `read` object (the single-read result), or `status: "missing"`. Read source files are limited to 256 KiB each, path strings to 4096 UTF-8 bytes, and total returned text to 1 MiB before JSON escaping. Limits reject rather than truncate. Ranges use positive one-based line numbers; reversed ranges reject. Unsafe paths, symlinks, directories, non-UTF-8 contents and limits fail the whole call. Reads are sequential, not a snapshot of simultaneous filesystem state. Single-file `read` retains its previous behavior.

Use `preview_edit` for a known existing UTF-8 file:

```json
{"op":"preview_edit","path":"src/lib.rs","old_text":"OldName","new_text":"NewName","expected_matches":2}
```

For several independent changes use a single mixed preview, then apply its returned handle:

```json
{"op":"preview_files","operations":[{"op":"edit","path":"src/lib.rs","old_text":"OldName","new_text":"NewName","expected_matches":2},{"op":"create","path":"docs/MIGRATION.md","content":"Use NewName.\n"}]}
```

The 1–100 operations are `create(path, content)`, `copy(from, to)`, `move(from, to)`, `delete(path)`, and `edit(path, old_text, new_text, expected_matches)`. All refer to the initial workspace state. Overlapping paths and dependencies reject; destinations must be absent. Edits require a nonempty old string and a positive exact count. One apply produces one undoable journal transaction. The individual `preview_create`, `preview_copy`, `preview_move`, and `preview_delete` requests remain supported.

MCP tools use the same names prefixed with `ffsake_`, omitting the `op` argument. Generic `preview` is available in resident JSONL; MCP exposes the focused preview tools. Tool errors leave the session usable.

## Case-preserving textual rename

```json
{"op":"preview_rename","target":{"include":[{"Glob":"src/**/*.rs"}],"exclude":[]},"from":"user","to":"client","expected_matches":8,"max_files":20}
```

`ffsake_preview_rename` exposes the same arguments through MCP. Both names must be ASCII identifiers of 1–256 bytes containing a letter and differing beyond casing. Matches use ASCII identifier boundaries and ignore ASCII case: `user USER User` becomes `client CLIENT Client`. Lowercase, uppercase and initial-capital styles take precedence; an exact mixed-case source such as `LegacyConfig` maps to the supplied `RuntimeConfig`. Other mixed spellings such as `lEGacyConfig` reject the whole preview rather than guess. Underscores/digits retain the replacement's structure. Case-only renames use exact editing instead.

The match count is positive and exact across the explicit target; `max_files` is required in 1–100 and checked before loading target contents. Apply uses the normal frozen membership and source hashes, and the result is one undoable transaction. The transform form is `ReplaceIdentifierCasePreserving { from, to, expected_matches }`. This is text editing, including comments/strings, with ASCII boundary semantics; it is not Unicode-identifier or language-aware rename.

## History and filesystem guarantees

Existing-file replacements preserve current permissions. On Unix, new journal entries capture ordinary rwx bits for recreated files; copy/move preserves source rwx bits and rejects source permission changes after preview. Older manifests lack metadata they never captured. Ownership, ACLs, xattrs and special mode bits are not restored.

Undo/redo verifies current workspace state and both saved snapshot sides before writing, then rechecks snapshot hashes when used. Missing/corrupt or symlinked journal data rejects replay. Ordinary late write or final manifest failures roll back earlier members; a failed rollback is explicitly reported with affected paths. Remove the external obstruction before retrying a successfully rolled-back operation. Do not treat an incomplete rollback as success.

Temporary files are reserved exclusively: a pre-existing file, link or directory at the temporary name rejects the write and remains untouched. These guards do not provide power-loss atomicity or an OS sandbox against hostile concurrent filesystem changes.

## Reproducible validation

```sh
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all -- --check
cargo build --bin ffsake
python3 scripts/benchmark.py target/debug/ffsake
cargo build --locked --bin ffsake-mcp
python3 scripts/mcp_smoke.py target/debug/ffsake-mcp
python3 scripts/repository_smoke.py target/debug/ffsake-mcp
```

The benchmark runs the actual JSONL executable, checks protocol recovery and safety failures, then runs ten search → preview → apply → undo cycles on 200 files, 20 changed files, and 400 UTF-8 byte-range matches. Every changed byte and restored file is checked. It compares against four shell requests using grep plus Python/difflib and explicit undo snapshots. Both retain a review boundary before apply. Startup is reported separately; filesystem validation overhead is included in both timings. The shell baseline is a concrete implementation, not a universal measure of shell performance or model token usage. One shell script could batch phases if no agent review is needed; one piped CLI stream could do the same. No general round-trip advantage is claimed from this fixture alone.

## Rust syntax selection

```json
{"op":"select_rust_identifiers","paths":["src/lib.rs"],"identifier":"account","limit":100}
{"op":"preview_matches","handle":1,"replacement":"customer","expected_matches":4}
```

`ffsake_select_rust_identifiers` provides the same selection through MCP. Tree-sitter selects exact identifier, type-name, field-name and shorthand-field spellings, returning a normal MatchSet handle plus canonical paths, byte ranges and one-based lines. Comments, string/character literals, lifetimes, attributes, macro definitions and opaque macro token trees are excluded. Macro invocation names outside their token trees remain eligible. Different bindings with the same spelling are all selected: this does not resolve symbols or expand macros. Raw and Unicode spellings are supported exactly; no Unicode normalization or raw/non-raw equivalence is applied. Input names follow Rust 2024 keyword restrictions, even in older-edition files.

Limits: 1–32 explicit distinct `.rs` files, 256 KiB per file, 1–1000 matches and a shared five-second parse/walk budget. Symlinks, missing files, invalid UTF-8, parser errors and exceeded limits reject the whole request, without creating a partial selection. Tree-sitter parsing does not establish compiler validity.

Selection preserves original bytes, including CRLF, and hashes every input file. `preview_matches` enforces the exact expected count and rejects stale files; apply uses the existing frozen preview and journal. For a Rust syntax-selection handle, `preview_matches` requires a valid Rust 2024 replacement identifier and reparses every staged file within a five-second budget; invalid syntax or files larger than 256 KiB reject without writes. Ordinary indexed-search handles retain literal replacement behavior. These checks do not detect name collisions, resolve bindings, expand macros, or establish compiler validity. Compile/test externally after applying.

## Read-only Rust references

```json
{"op":"rust_references","files":["src/lib.rs","src/model.rs"],"crate_root":"src/lib.rs","path":"src/model.rs","position":{"line":12,"column":8},"edition":"2024","limit":100}
```

MCP exposes `ffsake_rust_references` with the same arguments. The optional backend must be installed on PATH (`rustup component add rust-analyzer`). `position.line` is one-based and `position.column` counts zero-based Unicode scalar values, matching existing FFSAKE coordinates. The client negotiates UTF-16 internally and strictly converts returned positions to original byte ranges; invalid positions, split surrogate pairs and the interior of CRLF reject rather than clamp.

Each request copies 1–64 explicit distinct UTF-8 `.rs` files, at most 256 KiB each and 8 MiB aggregate, into a fresh temporary workspace and supplies an explicit crate graph. `crate_root` and `path` must appear in `files` exactly; `edition` must be 2015, 2018, 2021 or 2024. It waits for a healthy, quiescent server, queries references including the declaration, validates all locations against the supplied snapshot, then rechecks every original source hash. Missing/unsafe/symlinked inputs, stale originals, unhealthy analysis, invalid ranges and exceeded limits return an error without partial results. The call has a shared 30-second budget, 8 KiB headers, 1 MiB per inbound LSP message and 8 MiB total inbound data. The child is terminated after each query, including failure.

Results contain `ranges`, `matches`, `file_hashes`, `analysis_mode: "explicit_crate_snapshot"`, and `complete: false`. They are read-only navigation data, with no MatchSet or preview handle. Cargo dependencies, standard-library sources, configured features, build scripts and procedural macros are omitted; references can therefore be missing even when the server is healthy. Locally defined declarative macros are analyzed. Empty results do not prove there are no references. The snapshot prevents modifications to original files, and returned ranges are confined to explicit source files; this is not an OS sandbox for the external server.

`ffsake_rust_definition` (resident `rust_definition`) accepts the same arguments and returns definition locations under the same snapshot, timeout, scope and hash contract. Results include `query: "definition"`; references include `query: "references"`. Definition navigation may return a zero-width file anchor, for example byte 0 of an empty module. Reference ranges remain nonempty. Definitions are still incomplete without dependency/sysroot/feature context and return no mutation handle.

## Rust document outlines

```json
{"op":"rust_symbols","files":["src/lib.rs"],"crate_root":"src/lib.rs","path":"src/lib.rs","edition":"2024","limit":100}
```

`ffsake_rust_symbols` returns a hierarchical outline of the query file using the same source-snapshot backend. It accepts the navigation arguments except `position`. Results include `path`, `symbols`, `symbol_count`, hashes and the same incomplete-analysis metadata. Each symbol preserves `name`, numeric LSP `kind`, a readable `kind_name`, optional `detail`, declaration `range`, identifier `selection` and `children`. Ranges contain original `start_byte`/`end_byte` offsets and one-based `start_line`/`end_line`; byte ends are exclusive. Unknown future kind numbers retain their number and use the label `unknown`.

The positive `limit` bounds all descendants, not just top-level entries. Maximum depth is 32; names/details are at most 4096 UTF-8 bytes each. Invalid UTF-16/ranges, selections outside declarations, children outside their parent range, nonhierarchical responses and exceeded limits reject the entire outline. Empty documents return zero symbols. No edit handle is returned, and source is rechecked before the response.

`ffsake_rust_implementation` (resident `rust_implementation`) accepts the same positional arguments as references/definitions and returns `query: "implementation"`. It locates implementations of locally declared traits, types and trait methods within the explicit snapshot. Multiple implementations are returned together, subject to the exact result limit; no results yields an empty range array. Scope/hash/coordinate/server-budget checks and `complete: false` remain identical. Missing implementations in omitted dependencies or feature configurations are not evidence that a symbol has no implementations.

## Rust hover and types

```json
{"op":"rust_hover","files":["src/lib.rs"],"crate_root":"src/lib.rs","path":"src/lib.rs","position":{"line":12,"column":8},"edition":"2024","max_bytes":4096}
```

`ffsake_rust_hover` uses the same positional snapshot scope, with `max_bytes` (1–65,536) replacing the navigation result count. It returns `path`, `hover`, `text_bytes`, hashes and the shared incomplete-analysis metadata. `hover.contents` is an array of normalized parts with `format: "markdown" | "plaintext" | "code"`, original `text` and an optional code `language`. An optional `hover.range` contains original byte offsets and one-based start/end lines. A null hover means the server supplied no information.

The byte budget covers the sum of UTF-8 text bytes across at most 32 parts; each code-language label is separately bounded to 64 bytes. Oversized/malformed content and invalid/reversed ranges reject without truncation. Documentation stays source data: this endpoint does not render HTML, execute snippets, follow links, or treat documentation as instructions. Local inferred types/signatures can be useful despite omitted dependencies, but `complete: false` still applies and no compiler-validity or semantic-rename guarantee is made.

## Rust native diagnostics

```json
{"op":"rust_diagnostics","files":["src/lib.rs"],"crate_root":"src/lib.rs","path":"src/lib.rs","edition":"2024","limit":100}
```

`ffsake_rust_diagnostics` accepts the outline arguments and returns primary native rust-analyzer diagnostics for one file. The server must advertise pull diagnostics. Project loading reaches healthy/quiescent status before documents are opened; opening earlier reproduced silently empty diagnostics in the explicit graph. Each request obtains a fresh full report and rechecks all original hashes under the shared snapshot and 30-second budget.

Results contain `path`, `diagnostics`, `diagnostic_count`, `text_bytes`, hashes, `query: "diagnostics"`, `complete: false`, `compiler_checked: false` and `experimental_diagnostics: true`. Each diagnostic contains an original byte/line `range` (zero-width anchors allowed), numeric LSP `severity` (1 error, 2 warning, 3 information, 4 hint; null if unspecified), string/integer/null `code`, optional `source`, literal `message` and numeric `tags`. `category` is `syntax` for `syntax-error`, otherwise `semantic`; this does not determine whether an error is caused by an omitted dependency.

Native experimental diagnostics are enabled to include local type mismatches. Cargo check/build, dependencies, sysroot, build scripts, procedural macros and configured features remain omitted. Unresolved imports and other semantic reports can therefore be artifacts of incomplete context; an empty result does **not** establish compiler validity. Fix data, code-description links and related-location metadata are not exposed or executed; related reports are not negotiated. No mutation handle is created.

The required `limit` is 1–1000 primary diagnostics. Messages are at most 8192 UTF-8 bytes each, string codes/sources at most 256 each, and their combined text at most 65,536 bytes. Invalid ranges, malformed reports, unexpected related reports and exceeded limits reject the whole response without truncation. Diagnostic text remains source data, never agent instructions.

## Rust code-action discovery

```json
{"op":"rust_code_actions","files":["src/lib.rs"],"crate_root":"src/lib.rs","path":"src/lib.rs","range":{"start":{"line":12,"column":8},"end":{"line":12,"column":15}},"edition":"2024","limit":20}
```

`ffsake_rust_code_actions` discovers native actions for a range using the same explicit snapshot. Both ends use one-based lines and zero-based Unicode scalar columns; the end is exclusive and an empty selection is allowed. `limit` is 1–100 actions. A fresh request supplies an empty diagnostic context; actions based on omitted Cargo/compiler diagnostics may be unavailable. The backend advertises resource-operation formats so it can classify their proposals, but never applies edits or executes commands.

Results include a resident `action_set`, `actions`, `action_count`, `text_bytes`, hashes and `query: "code_actions"` with `complete: false`. Each action has `title`, optional `kind`, `is_preferred`, optional `disabled_reason`, `text_edit_count`, `unsupported_reasons` and `edits`. With no unsupported requirement, edits contain `path`, `start_byte`, `end_byte` and literal `replacement`; byte ends are exclusive. These are read-only proposed changes, not a MatchSet, preview handle, compiler check or semantic correctness guarantee. All original source hashes are rechecked before returning.

Commands, create/rename/delete operations, annotations, snippets, disabled actions, unresolved actions and actions without text edits are classified with explicit reasons and `edits: null` for the **whole** action. No potentially applicable subset is returned. Opaque data, command arguments, diagnostic attachments and resource paths are not exposed. Supported text edits must address supplied files, have snapshot version 1 or an unspecified version, valid UTF-16 ranges and no overlaps; duplicate document entries and ambiguous workspace-edit formats reject the response. Existing same-position insert ordering is preserved.

Across the response, at most 1000 text edits and 65,536 UTF-8 text bytes are accepted. Titles/disabled reasons are bounded to 4096 bytes and kinds/command identifiers to 256 bytes. Resulting source buffers must stay within 256 KiB. Limits or malformed supported edits reject without partial results. Proposed text remains source data; FFSAKE does not follow instructions embedded in it. The shared process/protocol/snapshot boundaries remain in force.

## Guarded Rust action previews

```json
{"op":"preview_rust_action","action_set":1,"action_index":0,"expected_title":"Insert explicit type `u32`","expected_edits":1}
```

`ffsake_preview_rust_action` selects one frozen action from a prior discovery in the same resident process. `action_index` is zero-based; the exact title and positive text-edit count must match. Callers cannot supply replacement text or hashes. The discovery `action_set` cannot be passed directly to `ffsake_apply` or `ffsake_preview_matches`.

Preview rereads every supplied analysis file under the existing bounded/no-symlink source contract, verifies the original hashes, stages the selected edits and parses every resulting file using the Rust grammar under a shared five-second budget. Unsupported actions, missing/stale sources, invalid syntax, overlaps or oversized results reject without writes. The ordinary frozen transform retains **all** analysis hashes, including files with no edits, and apply checks them again. The result contains the standard diff and `handle`, plus `action_title` and `semantic_validated: false`. `ffsake_apply` consumes that preview once and commits all changed files as one journal transaction; existing undo/redo applies.

Action and rename sets share the 128-handle resident limit and a combined 4 MiB serialized-report budget. `ffsake_release` accepts the `action_set` value; released sets cannot create new previews, while already-created previews remain independently valid. No server requery or hidden action resolution occurs during preview/apply.

Parser success does not prove compilation or preserve behavior: incomplete type context, name collisions or action-generated placeholders can still produce bad changes. Review the diff and verify behavior externally. The model-free fixture verifies exact Unicode/CRLF edits, compilation/runtime behavior of one type annotation, stale unedited-file rejection, release/replay boundaries and byte-exact history; this is not a general semantic guarantee.

`python3 scripts/rust_action_smoke.py target/debug/ffsake-mcp` records stronger negative evidence with independent rustc/runtime checks and full byte-history verification. A type annotation passes; promoting a local to a same-spelling constant can shadow an outer constant and compile with changed behavior; promotion can leave a reference inside an omitted standard-library macro unresolved; filling a missing field can generate `todo!()`, producing a compiling program that panics. The expected failures are named and asserted rather than ignored. These observations justify the explicit semantic-validation disclaimer and continued external behavior checks; they do not establish that every action in these categories fails.

Native rename remains an isolated feasibility probe (`python3 scripts/rust_rename_probe.py`), not a production endpoint. `prepareRename`/`rename` distinguished same-spelling bindings, included references in locally declared macros, and produced exact cross-file Unicode/CRLF edits. Independent compilation/runtime fixtures also demonstrated missing references inside omitted standard-library macros and an accepted colliding name that compiled with changed behavior. Renaming a module proposed a file rename; the probe inspected but did not apply that resource operation. Successful protocol responses, source ranges and parsing are therefore insufficient grounds for a semantic-rename guarantee.

## Optional explicit crate dependencies

Every Rust analysis endpoint above accepts an optional `crates` array. Omitting it (or supplying `[]`) preserves the original single-crate behavior. For example:

```json
{"op":"rust_references","files":["app/main.rs","model/lib.rs"],"crate_root":"app/main.rs","path":"model/lib.rs","position":{"line":1,"column":7},"edition":"2024","limit":100,"crates":[{"root":"app/main.rs","dependencies":{"account_model":"model/lib.rs"}},{"root":"model/lib.rs","edition":"2021"}]}
```

Each crate declares a `root`, optional `edition` (defaults to the request edition), and optional `dependencies` mapping Rust dependency names to other declared roots. The graph must contain `crate_root`; every root must occur exactly in `files`, and normalized roots must be unique. Limits are 16 crates, 16 dependency aliases per crate and 64 edges in total. Dependency names use ordinary ASCII Rust identifiers of at most 128 bytes, excluding keywords, `_`, raw spellings and hyphens. Unknown roots, cycles (including self-edges), extra fields, unsupported editions and exceeded bounds reject before the analyzer starts.

The shared file, coordinate, time, protocol and source-hash bounds still apply. Only supplied sources are copied, all are hashed, and every graph member is treated as local; no manifests, dependency downloads or build scripts are run. Results use `analysis_mode: "explicit_project_snapshot"` and echo `project_crates`; `complete: false` remains mandatory. The graph adds only explicitly supplied dependency edges. Sysroot, unspecified dependencies, procedural macros and implicit feature expansion remain omitted, so the documented action/rename limits still apply.

A real-server comparison changed one declaration-only result to three references across two crates and removed unresolved-import diagnostics when the edge was supplied. Changing the dependency's return type then produced a fresh type mismatch and invalidated the cached action and its frozen preview, including when only the dependent application file had edits.

Each explicit crate may also provide `features` and `cfg_flags`, for example `{"root":"model/lib.rs","features":["fast"],"cfg_flags":["local_mode"]}`. Both lists allow at most 32 unique entries of at most 128 ASCII bytes. Feature names allow letters, digits, `_`, `-` and `+`; names are literal, so Cargo activation expressions such as `dep/name` are rejected and no default/transitive feature expansion occurs. Custom flags use ordinary dependency-name syntax. Built-in/platform names (`unix`, `windows`, `target_*`, `test`, `debug_assertions`, `proc_macro`, `panic`, `rust_analyzer`, `miri`) are rejected; the backend's host cfg is not a user-selectable target model.

All Rust results now include `analysis_context` with the crate root, default edition and exact supplied crate specifications. Action sets retain it and previews echo it: issuing another query with different features does not silently change an earlier frozen action. Feature/cfg settings can improve selection within the declared snapshot but do not establish full build context. The integration fixture selects alternate definitions, changes type diagnostics, and verifies independent action sets retain their original active/inactive configuration.

Snapshot initialization explicitly supplies an empty diagnostic-exclusion list and file-exclusion list and disables workspace-discovery commands. A controlled user-global `diagnostics.disabled = ["E0308"]` previously hid a real local type mismatch; the pinned setting now returns it. Existing explicit build-script/procedural-macro/check-on-save settings remain in force. `scripts/rust_lsp_config_smoke.py` first calibrates that the temporary global configuration is read, then verifies diagnostics, source scope and an unexecuted discovery-command sentinel. Invalid global configuration still produces an unhealthy-server error rather than being ignored. The test uses rust-analyzer's test-only config-directory hook; production does not depend on it, edit user configuration or claim complete configuration/OS isolation.

The source-loading regression calibrates module paths, builtin `include!` and `include_str!` with explicitly supplied relative files. Omitted files and absolute/escaping targets expose no sentinel contents or external definitions in the tested cases, even when the original workspace file is also supplied under a relative name. Paths are interpreted in the disposable snapshot; absolute original paths are not remapped. This tests observable analysis results, not OS-level read isolation.


## Read-only native rename proposals

```json
{"op":"rust_rename_proposal","files":["src/lib.rs"],"crate_root":"src/lib.rs","path":"src/lib.rs","position":{"line":12,"column":8},"edition":"2024","new_name":"customer","limit":100}
```

`ffsake_rust_rename_proposal` uses the same explicit source/context snapshot as navigation and requires advertised prepare-rename support. `new_name` must be one exact Rust 2024 identifier, including supported Unicode/raw spellings, at most 256 UTF-8 bytes; reserved names and injected syntax reject before server startup. Earlier crate editions still use this conservative name validation. `limit` bounds proposed text edits from 1 to 1000.

The response includes `prepared` (original `start_byte`, `end_byte`, `old_text`), `new_name`, and `proposal`. A null preparation means rename is unavailable and no rename request is sent; the server may also return a null proposal. A supported proposal contains original-byte `edits`, `text_edit_count` and empty `unsupported_reasons`. Resource operations, annotations or snippets withhold **all** edits. The code-action workspace-edit validator supplies the same scope, versions, overlaps, 65,536-byte text budget and staged-size bounds. Invalid responses or limit overflow reject without partial output. Source hashes are rechecked after both LSP requests, which share the existing 30-second backend budget.

Discovery caches a frozen report and returns `rename_set`; this is not directly applyable and creates no journal entry. It returns exact `analysis_context`, `file_hashes`, `complete: false`, `semantic_validated: false` and `compiler_checked: false`. Native acceptance does not establish collision safety or macro completeness: the real MCP regression independently reproduces both compilation failure from an omitted standard macro and a colliding rename that compiles but changes behavior. Separate positive fixtures preserve distinct bindings, declared macro uses and cross-file Unicode/CRLF. The analysis workspace remains unchanged in every case.


## Guarded native rename previews

```json
{"op":"preview_rust_rename","rename_set":1,"expected_path":"src/lib.rs","expected_start_byte":174,"expected_old_text":"account","expected_new_name":"customer","expected_edits":3}
```

`ffsake_preview_rust_rename` selects a resident-owned frozen discovery using its exact path, original starting byte, original text, requested name and positive edit count. It accepts no caller edits or hashes. Null/unsupported proposals, released sets and identity/count mismatches reject. It rechecks **all** original source hashes, including unedited analysis files, stages the frozen text edits and reparses every source within the existing five-second syntax budget. It returns a standard preview handle for `ffsake_apply`, followed by journal undo/redo; apply again checks every analysis input and is one-shot.

The preview echoes the original `analysis_context`, `prepared`, `path` and `new_name`, with `complete: false`, `semantic_validated: false` and `compiler_checked: false`. Another discovery cannot change an earlier frozen proposal. Both action and rename sets share the 4 MiB cache and 128-handle limits. Release a `rename_set` with `ffsake_release`; existing previews remain independent, while new previews from that set fail. A rename set cannot be used as an action set or an apply handle.

The real-server regression checks exact preview identity, stale unedited input at preview/apply, release/replay rejection, and byte-exact multi-file Unicode/CRLF history. It also applies and reverses the known compiler/runtime negative examples in disposable fixtures: parser validation and journal recovery do not establish semantic equivalence.

The full-repository archive regression also runs external Cargo tests: its baseline passes, while a native type rename leaves references inside omitted macros and produces explicit E0422/E0425 errors. The regression requires those specific errors and exact undo/redo restoration. Compiler execution belongs to the test harness; the analysis/preview endpoints still do not execute builds or claim compiler validation.

Explicit macro dependencies can recover references without ambient discovery: a real helper-crate regression compares omitted versus supplied dependency edges for identity, repeated-expression and hygienic-local macros. Omission leaves the argument reference unchanged and causes E0425 after rename; declaring the helper source yields two source edits and compiler/runtime success. Repeated expansion sites remain one edit per original argument, and an unrelated macro-local binding is unchanged. These cases do not supply or emulate the standard library and do not establish general macro completeness.


## Batched workspace diagnostics

```json
{"op":"rust_workspace_diagnostics","files":["src/lib.rs","src/model.rs"],"crate_root":"src/lib.rs","edition":"2024","limit":100}
```

`ffsake_rust_workspace_diagnostics` collects native primary reports in one fresh analyzer session. It accepts the existing `files`, `crate_root`, `edition`, optional `crates` and diagnostic `limit`; it has no `path` or `position`. Optional `paths` selects 1–32 distinct exact members of `files`; omission selects all files. All supplied files remain analysis context and are hash-rechecked, including files outside the report selection.

`reports` contains one canonical-path report per selected file, sorted by path and including clean files. Each has `diagnostics`, `diagnostic_count` and `text_bytes`; the response adds aggregate `file_count`, `diagnostic_count` and `text_bytes`. The limit of 1–1000 diagnostics and 65,536 text bytes applies to the **whole batch**, alongside existing per-item/source/protocol bounds and a single shared 30-second analyzer budget. Invalid reports, exceeded limits or changed originals reject the entire call without partial output. A large dependency-incomplete project can exceed the diagnostic cap; select a smaller `paths` group while preserving the full source context.

The batch retains `analysis_context`, all source hashes, `complete: false`, `compiler_checked: false` and `experimental_diagnostics: true`. It runs no builds, returns no fixes or mutation handle, and does not prove that a clean project compiles. Real-server checks compare individual and batch reports, including Unicode/CRLF and fresh clean results; controlled-server checks reject a malformed second report and a changed unqueried input.


## MCP tool profiles

`ffsake-mcp WORKSPACE` keeps the full catalog. `ffsake-mcp WORKSPACE --profile all` is equivalent. For language-independent file maintenance, launch `ffsake-mcp WORKSPACE --profile core` (MCP arguments: `["WORKSPACE", "--profile", "core"]`). The profile is fixed for that server process.

The core profile exposes 37 tools: read/search/target, archive browsing, Git inspection and reviewed operations, task execution, debugging, access, generic edit/file-preview, apply/history and release/refresh. Rust syntax selection, language-server analysis and semantic previews are omitted from `tools/list` and rejected at dispatch before parsing their arguments or starting an analyzer. Core tools can still edit Rust files as ordinary text. Existing workspace, hash, preview and journal protections are unchanged; this is tool availability within this MCP server, not host-level isolation or a restriction on other agent tools.

The profile regression compares the full/default catalogs, checks every disabled tool, proves a valid blocked Rust request never starts an analyzer, rejects invalid launch arguments, and verifies core edit/apply/undo/redo with Unicode/CRLF and executable permissions. At the initial thirty-tool profile catalog, serialized tool definitions occupy 34,866 UTF-8 bytes for `all` versus 11,691 bytes for `core`; these are catalog sizes, not token costs or agent-speed measurements. Existing agent configurations continue using `all` unless their launch arguments explicitly select `core`.


Native rename proposals may contain zero-width insertions and replacement text larger than an identifier. For Rust field shorthand, renaming the field can expand `account` to `customer: account`, while renaming the local can expand it to `account: customer`. Preview the complete diff; treating every proposed edit as a plain spelling replacement loses binding information. The dedicated real-server regression covers field, local and pattern-binding renames with exact expected output, Unicode/CRLF, external compiler/runtime checks and byte-exact history. These successful fixtures do not remove the existing incomplete-analysis or semantic-validation limitations.


Import aliases have a distinct native boundary: renaming an imported definition updates its declaration/import while preserving the local `as` alias and call sites. Renaming the alias itself is rejected by the tested rust-analyzer during preparation; MCP returns an error with no proposal handle or journal state. Raw LSP and real MCP regressions cover both outcomes, with external compiler/runtime checks for the supported definition rename. Native support may change with analyzer versions; this is not a textual fallback.


## Combined Rust inspection

```json
{"op":"rust_inspect","files":["src/lib.rs","src/model.rs"],"crate_root":"src/lib.rs","path":"src/model.rs","position":{"line":10,"column":4},"edition":"2024","limit":100}
```

`ffsake_rust_inspect` returns definition, references (including the declaration), and hover for one source position using one fresh analyzer session. It accepts the same explicit source/context fields as navigation, including optional `crates`, and is available in the full profile. At the introduction of combined inspection, core had eighteen tools and the full catalog had thirty-one; current catalog counts are listed below.

`definition` and `references` each contain normalized `ranges` and `matches`; `hover` contains the existing `path`, `hover` and `text_bytes` report. `location_count` totals returned locations. `limit` (1–1000) is shared across both native location responses before deduplication, and hover has a fixed 8192-byte UTF-8 content cap. For just one result type, use its standalone endpoint.

The whole request shares the existing thirty-second, source-size, frame and path limits. All input hashes are rechecked after all three responses, including unqueried context files. Malformed data, excess locations/text, timeout or changed source returns an error without partial results. Inspection creates no mutation handle or journal state. It returns exact context, `complete: false` and `compiler_checked: false`; hover documentation remains untrusted source text.


Native backend rejection errors identify the failed LSP method and signed error code. A valid message of at most 1024 UTF-8 bytes is quoted with control characters escaped; oversized or malformed messages use fixed fallback text, and arbitrary backend `data` is never forwarded. For example, an unsupported alias rename now explains that limitation instead of only saying the request was rejected. Error text remains untrusted diagnostic data. Rejection still returns no partial result or mutation capability and shuts down the analyzer.


Current core catalog: 22 tools; full catalog: 38. `ffsake_archive` is available in both. JSONL uses `op: "archive"` with the same `path`, optional `entry`, `offset`, `limit`, `start`, `end`, and `expected_archive_hash` fields. Search responses now expose `coverage`; exact-root targets use bounded direct search even for hidden/ignored directories. See README for bounds, exclusions, examples, and the distinction between whole-transaction undo and a fresh selective inverse edit. Existing historical catalog measurements above describe their recorded versions.


## Shared language intelligence

`ffsake_semantic` (`op: "semantic"` in JSONL) takes one `request` object:

```json
{"files":["budget.hpp","budget.cpp","main.cpp"],"path":"budget.cpp","position":{"line":2,"column":21},"query":"rename","new_name":"joinBudget","context":{"language":"cpp","standard":"c++17"},"limit":100}
```

Queries: `definition`, `references`, `hover`, `symbols`, `diagnostics`, `inspect`, `rename`. Positions use one-based lines and zero-based Unicode scalar columns. All queries require `position`; use line 1/column 0 for document-wide queries. `limit` is 1..1000 (hover interprets it as a UTF-8 text-byte cap). Rust context uses `language: "rust"`, `crate_root`, `edition`, and optional `crates`, routing to existing guarded analysis. Old Rust endpoints remain compatible; new integrations should prefer the shared entry point.

C++ uses installed `clangd`, with c++17/20/23, 1..64 explicit `.cpp/.cc/.cxx/.h/.hpp/.hh` sources, 256 KiB per file and 8 MiB aggregate, snapshot-root includes, and optional `defines` (at most 32 identifier keys with ASCII token values, each at most 128 bytes). It creates a temporary compilation database, opens every source, and waits for clangd file-status readiness. It disables user `.clangd` configuration, background indexing and clang-tidy, and does not enable compiler query-driver execution. It does not import other build flags, dependency discovery or generated files; installed toolchain system headers may be visible. No build is executed. These context limitations are returned, and `complete=false`/`compiler_checked=false` remain explicit.

Both adapters share bounded JSON-RPC transport (30-second deadline, 8 KiB headers, 1 MiB frames, 8 MiB total incoming data), UTF-16/range validation, workspace-edit classification and source-hash guards. Definitions/references outside the supplied source snapshot reject. Rename proposals with resources or unsupported edit requirements expose no partial edits.

`ffsake_preview_semantic_rename` accepts the same exact identity/count fields as the existing Rust rename preview. It previews only resident-owned proposals and rechecks all analysis sources, including unedited context; apply remains separately guarded and journaled. Rust retains parser validation. C++ does not run a Rust parser or claim syntax validation; review and external build/tests are required. The core profile excludes both semantic tools and remains usable without either language server.

`python3 scripts/semantic_smoke.py target/debug/ffsake-mcp` checks real-server reuse, three-file C++ rename, same-spelling isolation, external compilation/runtime, stale unedited context at preview/apply and exact history. See [clangd compile commands](https://clangd.llvm.org/design/compile-commands) and [clangd indexing](https://clangd.llvm.org/design/indexing) for why explicit project context matters.


## Git review and host-configured build/test jobs

`ffsake_git` takes `query: "status" | "diff"`. Status returns HEAD plus index/worktree status codes and paths; it ignores submodules, summarizes untracked directories and excludes protected metadata. Diff accepts optional `staged` (default false) and up to 100 exact literal `paths`; omission selects the repository diff. Staged diff compares index to HEAD, unstaged diff compares tracked worktree content to index. Untracked contents are not included. The workspace must be the repository/worktree root. No staging, commit, reset or push is performed.

Git observations are bounded to 15 seconds per request, 256 KiB per captured stream, and 1,000 status entries; overflow rejects without a partial diff. Pager, fsmonitor, external diff and textconv are disabled for `ffsake_git` inspection; inherited Git routing variables are removed, and global/system Git config is not loaded. Repository clean/process filters cause explicit refusal because even status/diff can execute those helpers. Use host Git for such configurations. This is not an adversarial filesystem sandbox or an atomic Git/worktree snapshot.

For selected-file changes, `ffsake_git_review` accepts 1..100 distinct workspace-relative file paths and returns a single-use review handle, the selected staged and unstaged diffs, and hashes; read untracked files separately because their content is not in the diff. Up to 64 handles are retained, and the oldest expires when a new one is created beyond that limit. `ffsake_git_mutate` takes that handle and `action: "stage" | "unstage" | "commit"`; commit additionally requires `message`. Review again after every mutation, and review the staged diff before committing. Git mutations require ordinary host approval and are not editor undo/redo transactions. No push, branch, merge or hunk operations are provided.

Review snapshots HEAD and its symbolic reference, the entire index, selected file bytes and modes, relevant attributes and Git configuration. They are rechecked before mutation; a changed state rejects and requires a fresh review. Git's index flags that can hide a changed worktree are rejected for commit, and selected worktree content must match the index. Commit constructs a new tree from HEAD plus only the selected reviewed index entries, creates a commit with Git plumbing and atomically updates the reviewed branch if its previous object ID still matches. The real index remains untouched, preserving unrelated staged changes. Detached-HEAD commits are not supported. Git index and branch locks do not lock out arbitrary filesystem writers, and external processes can race review and mutation; inspect Git state after errors or timeouts.

Mutation uses the user's Git configuration rather than disabling it. To avoid silently skipping executable behavior, FFSAKE refuses executable commit/index/reference hooks, commit signing, configured fsmonitor and selected clean/process filters; use host Git for those repositories. It also rejects inherited `GIT_*` overrides other than the pager, which is disabled. Git and hooks/configuration are not sandboxed; time/output limits and safety refusals are not an execution sandbox. A failed or timed-out operation can have an uncertain outcome: inspect HEAD, index and working files before retrying.

`ffsake_git_branches` lists local branches with the current branch, commit and upstream. To create a branch from the reviewed HEAD, switch a local branch, fetch a configured remote, or push a named local branch to a named remote branch, call `ffsake_git_operation_review` with an `operation` object and inspect the returned state. Then call `ffsake_git_operation` with the review ID and the identical operation; for a push also supply the returned `source_commit`. The full action, arguments and reviewed source commit are visible to ordinary host approval. Auto/allow settings remain effective. Example push: `{"action":"push","branch":"feature/example","remote":"origin","remote_branch":"feature/example","set_upstream":true}`. The review displays the exact source branch and commit, remote name and destination branch. It does not contact the remote or promise an atomic remote snapshot. Push sends the reviewed immutable commit with a non-force refspec; the remote enforces fast-forward safety. If upstream setup fails after a successful push, the response says so explicitly.

Only configured remotes with one effective URL are supported. Fetch and push with upstream setup require the standard `+refs/heads/*:refs/remotes/<remote>/*` fetch mapping. Fetch explicitly requests non-force branch updates without tags, pruning or submodules; a non-fast-forward remote-tracking update rejects. Mirror, signed push, follow-tags, push options, custom upload/receive-pack, recursive submodule settings, custom fetch mappings and configured pruning are refused where they would exceed this scope. Fetch does not merge or modify working files. Branch switches use Git's overwrite checks with `--no-overwrite-ignore`, preserve nonconflicting local changes and preflight affected paths against workspace authorization and symlink protections. Switches affecting `.gitattributes`, symlinks or submodules, over 1,000 paths or over 64 MiB of working files are unsupported. Switch locks the target branch ref and push locks the source branch ref while rechecking and executing; an abrupt server kill can leave a Git `.lock` file that requires inspection and manual cleanup. Creating a branch from an unborn HEAD is unsupported. No force, mirror, deletion, tags, arbitrary refspecs, pull, merge, rebase, stash, reset or clean is exposed.

Git configuration and configured authentication are not silently replaced. Credential helpers and SSH can execute programs and network operations are not sandboxed. Executable relevant hooks, configured fsmonitor, signed pushes and unsupported settings cause refusal; inherited `GIT_*` environment overrides other than `GIT_PAGER` are unsupported, including `GIT_ASKPASS` and `GIT_SSH_COMMAND`. Normal configured credential helpers and SSH agent configuration are inherited, but may require interaction outside the server; local bare remotes are the tested transport. Network stdout and stderr are withheld on success and failure because they can contain credentials. Each Git subprocess has a 30-second bound; inspect local and remote state after failures or timeouts. Fetch can update some tracking refs before another update fails, and a push may have succeeded even if its response or later upstream setup fails.

Project tasks use the [VS Code process-task](https://code.visualstudio.com/docs/reference/tasks-appendix) `version: "2.0.0"` `.vscode/tasks.json` format. FFSAKE reads JSONC (comments and trailing commas) using `jsonc-parser`, and preserves unrelated comments when updating a task. Supported task fields are `label`, `type: "process"`, `command`, string-array `args`, optional `group: "build" | "test"`, and optional `options.cwd`. Run tasks omit `group`. Only `${workspaceFolder}` and `${workspaceFolder}/relative/path` are supported in `cwd`; `.` or another workspace-relative directory also works. Substitutions in executable or arguments, shell tasks, `env`, problem matchers, dependencies, platform overrides and other properties reject clearly. The root accepts only `version` and `tasks`. This is a documented subset, not complete VS Code compatibility.

`ffsake_tasks` lists existing definitions and whether their current execution fingerprint is authorized. Existing task files are not automatically trusted. To create, update or authorize a task, request host approval for `ffsake_task_register`, for example:

```json
{"definition":{"label":"Test","type":"process","command":"dotnet","args":["test","My Project.sln"],"group":"test","options":{"cwd":"${workspaceFolder}"}},"timeout_seconds":120,"output_bytes":65536}
```

Registration saves the definition and a separate protected approval under `.ffsake/project-tasks/`. The host's ordinary permission policy applies separately to registration and to `ffsake_run_task`, including intentional allow/auto overrides. The setup generators leave both for approval by default; `--task-approval allow` deliberately allows both. Ordinary file editing cannot modify the protected store or authorize a task. No explicit task-policy flag, JSON hand-edit or MCP restart is needed to register or update a task while the server runs.

The project-bound approval fingerprints the canonical executable path and bytes, arguments, authorized working directory, timeout and output limit. It is atomically persisted with file/directory synchronization and rechecked against the current definition and executable before each run, including from other sessions. Formatting, comments and timestamps alone do not change it. An executable/argument/cwd/limit change requires reauthorization. A copied store fails closed in a different canonical project. Deleting a task from the project file stops it being runnable, but the approval for an identical definition remains in the protected store; there is no task-revocation UI yet. A task can be manually reauthorized through the registration tool.

`ffsake_run_task` accepts only `name`; it starts an authorized project task asynchronously and returns `job`. Call `ffsake_task_output` with the job, `stream` (`stdout` or `stderr`), byte `cursor` (initially 0), `max_bytes` (4..65536, default 4096) and optional `wait_ms` (0..1000). Repeat using `next_cursor`; bounded waiting works without MCP notifications. Each stream keeps its own cursor and ordering. `text` preserves UTF-8 across page boundaries; `data_base64` contains the exact returned bytes, including invalid UTF-8. `text_is_lossy` identifies replacement characters. A wait occupies the server request for at most one second; between calls other tools remain available.

The current job retains the most recent 4 MiB per stream in protected anonymous files. `first_cursor`, `cursor_expired`, and `dropped_bytes` disclose overwritten bytes; on expiration resume at the returned `next_cursor`. `complete` means the process capture is finished, `drained` says both pipes reached EOF, and `log_complete` is true only when both draining and retention preserved that stream. Logs expire when another job starts or the server exits; OS cleanup also reclaims anonymous files after a crash. Per server, disk retention is at most 8 MiB. Output is untrusted. A deliberately detached child may keep a pipe open; incomplete draining is reported rather than presented as complete.

`ffsake_task_status` reports `exit_code`, `success`, `timed_out`, `cancelled`, `output_truncated` and `output_drained` without embedding output by default. For compatibility, set `include_output: true` to request the legacy bounded summary. `output_bytes` (1..262144 per stream, default 65536) controls that summary, independently of live-log retention and response size; it remains part of the approval fingerprint. `exit_code` and `signal` reflect the top-level process's actual OS status: a process may handle SIGTERM and exit 0 after cancellation, but `cancelled` remains true and `success` false. `ffsake_task_cancel` sends SIGTERM to the owned process group, then SIGKILL after 500 ms if needed. A short task may finish first, so inspect final status. One project task can run per server instance. Closing the server cancels its owned process group. `timeout_seconds` is 1..300 (default 60).

Tasks pass an executable and argument array to the host process API; FFSAKE does not interpolate a command string. On macOS, the process API was observed to fall back to a shell when an executable text file has no interpreter line, so use executables or scripts with an explicit `#!` interpreter. Tasks inherit the host environment and can run arbitrary project code. They are not sandboxed or journaled. Approval does not freeze source code, dependencies, the inherited environment, or the behavior of the program; review those separately. Ordinary filesystem access grants do not sandbox processes. Existing edit previews still check original hashes. Unix process-group cleanup cannot contain deliberately detached descendants. Registration writes `tasks.json` atomically but is not an editor undo/redo transaction.

When the host supplies `--tasks`, project task registration and project task execution are disabled; only fixed policy jobs can run. Existing policy jobs retain their synchronous result API and cannot use the asynchronous project-task cancellation tool:

```sh
ffsake-mcp WORKSPACE --profile core --tasks /absolute/trusted-tasks.json
ffsake serve WORKSPACE --tasks /absolute/trusted-tasks.json
```

Policy format is illustrated by [tasks.json](examples/tasks.json). It requires version 1 and 1..32 named jobs, with kind `build` or `test`, a fixed argv vector (1..64 arguments, each <=4 KiB), a workspace-relative directory (`.` default), timeout 1..300 seconds (60 default), and captured-output limit 1..262144 bytes per stdout/stderr stream (65536 default). The complete file is <=32 KiB and is loaded once at startup. Repository job files are never discovered or reloaded implicitly. No implicit shell is used; a host may explicitly configure a shell command if that is the intended authorization.

Each run returns `success`, `exit_code`, termination `signal`, `timed_out`, `elapsed_ms`, stdout/stderr and `output_truncated`. Exceeding the capture limit keeps draining output while retaining only bounded bytes; it does not turn a successful process into a failed test. Timeout terminates the launched Unix process group and waits for the child; descendant processes that deliberately escape that group are outside this mechanism. Current job execution requires Unix. The caller's own tool timeout should exceed the configured job timeout.

Configured jobs can execute repository code and change files. They are not sandboxed, rolled back or journaled. Output is untrusted diagnostic data. The next search refreshes after a job, and existing previews still compare their original source hashes. Nonzero exit, signal or timeout is a failed job, not a successful verification. Git review and jobs do not require LSP.

JSONL equivalents are `{"op":"git","query":"status"}`, `{"op":"tasks"}` and `{"op":"run_task","name":"test"}`. `python3 scripts/project_tasks_smoke.py target/debug/ffsake-mcp` tests project-task authorization and lifecycle; `python3 scripts/workflow_smoke.py target/debug/ffsake-mcp` verifies real Git index/worktree preservation, literal paths/protected exclusions/helper refusal, policy-only execution, real compilation/runtime, nonzero exit, output bounds, timeout child cleanup and stale previews after task writes.

## Host authorization and absolute paths

Both resident launchers accept `--access /host-controlled/authorization.json`. The primary project remains the default; additional paths require explicit host-exported directory/file grants. Grants distinguish modes and lifetimes, reload before requests, and are checked again at apply and history. Cross-root transactions reject. MCP roots are a live scope intersection only; they do not authorize read/write access. See [HOST_ACCESS.md](HOST_ACCESS.md) for the manual host path, exact-file setup, session/once rules and unsupported native approval automation.
