# Resident CLI milestone validation

Measured locally on 2026-10-21, Linux x86_64, Rust 1.99.1, unoptimized debug binary, pinned FFF revision `7353971198321b5330824c29eb4e49635c072245`. Base repository: `5008fc0866343dd82f8a03cdd3191b44beb4a160`. Cargo.lock records resolved dependencies.

## Measured workflow

200 UTF-8 text files; 20 contain 400 literal matches total. Ten iterations run the actual resident executable with interactive stdin/stdout pipes. Each iteration searches through FFF, retains a MatchSet, previews a replacement, applies the stored transform, and undoes the journal transaction. The fixture is byte-checked after preview, after mutation, and after undo. Repeated runs exercise index refresh after undo.

| Measurement | Resident CLI | Shell baseline |
| --- | ---: | ---: |
| Tool request/response phases per iteration | 4 | 4 |
| Median elapsed per workflow | 27.165 ms | 83.179 ms |
| One-time resident startup + ranged-read probe | 22.074 ms | Not applicable |

The resident workflow was approximately 3.06× faster in this fixture. There is **no measured round-trip reduction** against this deliberately batched shell baseline. Both preserve a review opportunity between preview and apply. The baseline uses grep for discovery, then Python/difflib subprocesses for staged previews, guarded apply, and guarded snapshot restoration. It does not invoke ffsake. Its snapshots do not offer ffsake's persistent journal protocol or workspace confinement.

Times include pipe/subprocess overhead and fixture verification, exclude compilation, and include resident index refresh on subsequent searches. Iterations alternate resident then shell on the same restored fixture. This is a small synthetic local benchmark, not a claim about large repositories, network tool latency, universal shell performance, or LLM token savings. A single script can batch either interface if the review boundary is removed. Raw samples are in [benchmark-results.json](benchmark-results.json).

## Verification

- `cargo test --workspace`: all 40 existing unit tests passed.
- `cargo clippy --workspace --all-targets -- -D warnings`: passed.
- `cargo fmt --all -- --check`: passed.
- `cargo build --bin ffsake`: passed.
- `python3 scripts/benchmark.py target/debug/ffsake`: passed, including the workflow and CLI safety checks.

CLI checks cover malformed/oversized input recovery, unknown fields, traversal/absolute/internal paths, external symlink rejection, unknown/released/consumed handles, match-count guards, read-only previews, stale MatchSets and previews, max-files preconditions, whole-transform failure when one source changes, undo conflicts, and redo. Existing library tests additionally cover workspace-root mutation rejection, overlapping edits, UTF-8 coordinates, scoped selectors, transaction grouping, and cross-file selections.

The runtime reuses the existing mutation engine. It adds source-hash and exact-membership freezing for generic previews and transaction-ID validation at the CLI boundary. FFF watcher lag after runtime mutations is handled by refreshing before the next search. External modifications may still require an explicit refresh for search completeness; source-hash guards remain required for safe mutation.

No LSP, build executor, broader IDE functionality, or second orchestration language was added. The CLI is a single-client resident stdio process; it does not add daemon discovery or a socket client. A coding-agent tool must keep the process pipes alive to retain its index and handles.

## Agent suite preflight and tool policy

Before any paid run, inspect the locally installed CLIs without sending prompts:

```sh
git pull --ff-only
python3 scripts/agent_benchmark_suite.py --preflight-only
```

This checks executable availability, versions and required help flags. It saves a JSON diagnostic report under docs/agent-benchmark-runs even on failure. Normal suite invocation also runs this check before starting agents. Preflight does not verify authentication, model availability, MCP startup or runtime permission enforcement.

Claude uses an explicit MCP configuration in both modes, with strict MCP configuration loading. Baseline has no configured MCP server; FFSAKE mode disables all built-in tools using an empty --tools value. OpenCode uses a deny-by-default permission map: FFSAKE mode allows only ffsake_* tools, while baseline allows a named set of native repository tools. Neither mode allows delegation or other inherited MCP tools through that map. OpenCode no longer receives the broad skip-permissions flag.

These settings follow the [Claude CLI reference](https://code.claude.com/docs/en/cli-reference) and [OpenCode permissions reference](https://docs.opencode.ai/docs/permissions/). Configuration regression tests do not prove enforcement in every installed agent version. Actual agent binaries are unavailable in the authoring environment, so runtime isolation remains explicitly unverified. Codex still has native tools and inherited configuration; it must not be presented as an enforced FFSAKE-only benchmark. See the [Codex configuration reference](https://developers.openai.com/codex/config-reference/).

Per-run trace audits detect recognized calls that violate the requested tool policy. Invalid comparisons and runs without recognized tool calls are excluded from performance medians. Protected-file changes and added/deleted fixture files fail correctness separately from behavioral verification.


Codex benchmark workspaces explicitly enable the seventeen known FFSAKE MCP tools and set `approval_mode = "approve"` for each of those tools only. This avoids the CLI's noninteractive `approval_policy = "never"` rejecting the calls before server execution. The disposable project config does not change global approval or sandbox settings, and this approval configuration is not a claim that native Codex tools are disabled. Approval-blocked attempts are recorded and invalidate performance comparisons.

For targeted diagnosis after a baseline is already known to work, use `--modes ffsake` (or `--modes baseline`). The default remains both modes. Rebuild `ffsake-mcp` after changes to the server. Selector schemas now expose `"All"`, `{"Exact":"path"}`, `{"Glob":"pattern"}` and `{"Regex":"pattern"}`. An empty include list deliberately selects no files; omission of the search target uses the current target or the whole workspace.

## Successful Codex micro pilot: workflow overhead

The supplied `suite-20261021-221551-mjgx150r.json` trace passed exact verification of all six fixture files: 11 replacements, seven successful FFSAKE calls, no observed shell calls, and 25.962 seconds elapsed. The calls were list, search, preview, apply, refresh, search-old, search-new. This is one diagnostic run and does not establish a general performance result or host-enforced isolation.

Directory listing was not needed to discover the matching files in this fixture. Explicit refresh was redundant because the next search after an FFSAKE mutation refreshes automatically. Both verification searches remain useful; their range details were unnecessary because only counts were needed. Tool descriptions and benchmark guidance now explain this shorter five-call route, and MCP smoke tests verify old/new counts immediately after apply without explicit refresh. The runtime already cleared the dirty flag on explicit refresh; no runtime refresh behavior was changed. Whether agents follow the shorter route and improve elapsed time remains unmeasured.

## OpenCode working-directory regression

The supplied `suite-20261022-120609-bem3pk6l.json` pilot did not operate on the disposable fixture: its baseline trace read the FFSAKE checkout's source, journals and earlier reports, and FFSAKE mode had no recognized MCP calls. These runs cannot establish agent performance on the fixture.

[OpenCode v1.18.31's run command](https://github.com/anomalyco/opencode/blob/v1.18.31/packages/opencode/src/cli/cmd/run.ts) prefers `process.env.PWD` when `--dir` is omitted. Changing only the subprocess working directory left the parent checkout in that environment variable. Agent processes now receive a matching PWD, and OpenCode also receives an explicit absolute --dir. Reports record the fixture path and invalidate recognized OpenCode file calls with explicit paths outside it.

OpenCode baseline explicitly disables the FFSAKE MCP server so an enabled server inherited from global settings cannot contaminate the comparison. FFSAKE mode overrides its command with the current fixture path.

Before any OpenCode model call, preflight uses its debug file-read and resolved-config commands in disposable git fixtures. It checks a unique file token, ordered permission settings and the FFSAKE MCP configuration for both modes. It does not persist resolved config, which may contain credentials. Run `python3 scripts/agent_benchmark_suite.py --agents opencode --preflight-only` to check an installed CLI without model calls. This validates configuration loading, not OS-enforced isolation or successful MCP connection.

On POSIX, agent launchers run in their own process group, which is killed on timeout or interruption so normal launcher/MCP/shell descendants cannot continue. Windows currently terminates the direct process only. Zero-model regression tests exercise inherited PWD mismatch, explicit directory selection, configuration probes, rule ordering, outside-workspace path reporting and descendant timeout cleanup.



OpenCode provider API errors are recorded separately from tool failures and invalidate the run. A structured non-retryable provider error stops the suite after saving the trace, instead of repeating requests that the provider has rejected. Configuration preflight makes no model calls and cannot verify inference access. If the default provider rejects requests, use an authenticated provider/model with `--opencode-model provider/model`; use the same model for both modes.


## Claude/Codex suite, 2026-10-22

The supplied `suite-20261022-141931-9h8ywgdi.json` completed all 36 runs (three repetitions per agent/task/mode). All passed external correctness checks and trace policy checks. Every FFSAKE run used only FFSAKE tools; this does not establish host-enforced isolation.

| Agent | Task | Baseline median | FFSAKE median | Median tool calls, baseline → FFSAKE |
| --- | --- | ---: | ---: | ---: |
| Claude | micro | 20.36 s | 12.03 s | 15 → 4 |
| Claude | medium | 13.03 s | 53.98 s | 3 → 28 |
| Claude | app | 13.14 s | 52.00 s | 3 → 31 |
| Codex | micro | 34.33 s | 32.94 s | 3 → 5 |
| Codex | medium | 36.07 s | 76.65 s | 5 → 17 |
| Codex | app | 51.75 s | 120.55 s | 4 → 35 |

Traces show an interface limitation: indexed search is line-based, while baseline editors replace contiguous multiline code blocks. FFSAKE agents instead searched, previewed and applied individual function bodies. Some prepared several previews from the same file version; after the first apply, remaining plans became stale and required new searches/previews. The hash rejection is intentional.

`ffsake_preview_edit(path, old_text, new_text, expected_matches)` now exposes literal multiline replacement for one existing file, reusing the resident transform preview, frozen file hashes, count checks, atomic journal apply, undo and redo. It writes nothing until `ffsake_apply`. This permits read → block preview → apply without first acquiring a search handle. Group related changes into one block, or apply each preview before preparing the next edit to that file. No stale-plan checks were relaxed. The post-change agent performance remains unmeasured.


## File operations through MCP

Four file-operation tools now return preview handles: `ffsake_preview_create(path, content)`, `ffsake_preview_copy(from, to)`, `ffsake_preview_move(from, to)` (also rename), and `ffsake_preview_delete(path)`. Use `ffsake_apply(handle)` to commit and the existing transaction IDs for undo/redo. Create accepts UTF-8 text; copy, move and delete retain arbitrary file bytes. Destinations must be absent; overwrites and directory operations are rejected. Missing parent directories are created on apply; undo removes created files but may leave empty parent directories.

Plans retain the previewed source bytes and expected destination absence. Apply rechecks those states, and the journal verifies state before applying or undoing. Journal path resolution now rejects symlink components, including symlinks introduced after preview or before history operations, and reserves `.ffsake` paths. This is application-level confinement, not an OS sandbox or protection against concurrent adversarial filesystem races.

The app benchmark still uses its original scaffold so existing results remain comparable; it is not a benchmark of building a project from an empty directory.


## Repeated medium/app comparison, 2026-10-23

The supplied `suite-20261023-183724-a1zftuln.json` completed 24/24 correct runs, with three repetitions per agent/task/mode, no timeouts and no observed policy violations. All FFSAKE calls were FFSAKE tools. The accompanying local MCP smoke output reports 80 rejected boundary operations and passing file-operation/history checks.

| Agent | Task | Baseline median | FFSAKE median | Time change | Median calls, baseline → FFSAKE |
| --- | --- | ---: | ---: | ---: | ---: |
| Claude | medium | 15.91 s | 16.50 s | +3.7% | 3 → 7 |
| Claude | app | 13.76 s | 15.28 s | +11.1% | 3 → 3 |
| Codex | medium | 37.21 s | 42.44 s | +14.1% | 5 → 8 |
| Codex | app | 45.57 s | 44.06 s | −3.3% | 4 → 6 |

Claude's reported median cost decreased from $0.076004 to $0.054941 for medium (27.7%) and from $0.082130 to $0.050534 for app (38.5%). Codex cost is unavailable. These are small samples, not evidence of universal speed or cost advantages, and observed tool use does not prove host-enforced isolation.

Traces confirm use of one multiline block preview/apply rather than per-function rebuilding. Claude app uses three calls (read, preview, apply); medium adds discovery and README reads. Codex adds search/read verification. No stale-plan recovery is needed in these runs.

## From-scratch project task

The opt-in `project` task begins with only a requirements README and a git repository. It requires three new Python package files implementing a persistent issue-tracker CLI. Both modes receive the same requirements. The verifier checks 15 behaviors using separate CLI processes, including persistence, combined filters, sequential IDs, Unicode/quoted/newline titles, missing database parents and unknown-ID handling without data corruption. It also requires the specified files and rejects additional files or changes to the requirements.

This exercises file creation through agent tools. Existing scaffold tasks and default task selection remain unchanged. It does not yet measure dependency installation, a web application, directory operations or builds through FFSAKE; the host runs correctness checks.

Run one diagnostic repetition for both agents and modes (four model runs):

```bash
python3 scripts/agent_benchmark_suite.py --agents claude,codex --tasks project --repetitions 1 --timeout 900
```


## Batch file-operation previews

`ffsake_preview_files` accepts an `operations` array of 1–100 create/copy/move/delete/edit objects. It returns one preview handle for one `ffsake_apply` and one journal transaction, so three file creations can use two tool calls instead of six. All operations refer to the initial workspace state. Normalized aliases, overlapping paths and dependencies between operations are rejected; two independent copies may read the same source. This is a set of independent file changes, not a sequential scripting language.

Example arguments:

```json
{"operations":[
  {"op":"create","path":"pkg/__init__.py","content":""},
  {"op":"create","path":"pkg/main.py","content":"print('hello')\n"}
]}
```

Preview does not create destination files or parent directories. Apply rechecks source snapshots and expected destination absence before committing. A stale member rejects the entire batch; journal rollback restores earlier files if a later write fails. Undo/redo operates on the batch transaction. Individual file-operation tools remain supported. Post-change real-agent performance is not yet measured.


Batches also accept literal multiline edits to existing UTF-8 files:

```json
{"operations":[
  {"op":"edit","path":"src/config.py","old_text":"LegacyConfig","new_text":"RuntimeConfig","expected_matches":2},
  {"op":"create","path":"docs/migration.md","content":"Use RuntimeConfig.\n"}
]}
```

Each edit requires a nonempty `old_text` and a positive exact `expected_matches` count. Replacement is literal, including dollar signs and whitespace; `new_text` may be empty to clear matching text. No-op edits, invalid UTF-8, missing files and count mismatches are rejected. An edit may not share a path with another edit, copy source, move or delete in the same batch; use one contiguous block per file. Every member is prepared against the initial state, and the whole mixed batch shares one journal transaction. Tests verify that a stale edit prevents preceding creations from being written and that a later write failure rolls back an earlier edit and creation.


## Existing-project maintenance task

The opt-in `maintenance` task starts with a working Python dispatch CLI and migrates its configuration schema. It requires edits to code, JSON and README; a byte-preserving documentation move and sample copy; a retired-script deletion; and new migration notes. Both modes receive the same task requirements. FFSAKE may use one mixed batch for independent paths, but the verifier accepts any correct implementation using the mode's permitted tools.

The host verifier runs the CLI in separate processes against missing, empty, new-key and retired-key configurations, checks defaults and strict integer/range validation, and checks the exact resulting file set and preservation of unrelated bytes. Required deletions are explicitly allowlisted per task; other deletions still fail. The reference implementation and deliberately broken migrations are exercised without model calls in the Python regression suite.

Run one diagnostic repetition first (four model runs):

```bash
python3 scripts/agent_benchmark_suite.py --agents claude,codex --tasks maintenance --repetitions 1 --timeout 900
```

Inspect correctness, invalid-comparison reasons and tool names in the saved trace before increasing repetitions. `ffsake_preview_files` calls show batch-tool adoption; their presence alone does not prove that every mutation used one batch. Compare time, total calls and reported cost against the baseline. No agent results for this task have been collected yet. Existing default tasks and historical results remain unchanged.

## Maintenance diagnostic, 2026-10-24

The supplied `suite-20261024-153809-w2at71po.json` contains one repetition per agent/mode:

| Agent | Mode | Correct | Elapsed | Calls | Reported cost |
| --- | --- | --- | ---: | ---: | ---: |
| Claude | baseline | 25/26; extra bytecode files | 18.24 s | 8 | $0.0935825 |
| Claude | FFSAKE | 26/26 | 16.59 s | 6 | $0.0775811 |
| Codex | baseline | 26/26 | 44.64 s | 4 | unavailable |
| Codex | FFSAKE | 26/26 | 34.71 s | 23 | unavailable |

Both FFSAKE agents submitted all seven requested operations in one `ffsake_preview_files` call and committed with one apply. Claude's other four calls were three reads and one search. Codex's other 21 calls were 19 reads and two searches, including exploratory instruction-file lookups and post-apply verification. Three Codex calls returned errors while probing missing paths; the mutation batch succeeded without recovery. The paired Codex result was 22.2% faster in this single run despite more calls; repetition is needed before treating this as a performance result.

Claude's baseline implementation passed every behavior/content check. Its own Python CLI verification created three `dispatch/__pycache__/*.pyc` files, causing the exact-file-set check and unexpected-file audit to fail. This is not evidence of a faulty implementation or a correctness advantage for FFSAKE. The original report remains unchanged.

The harness now sets `PYTHONDONTWRITEBYTECODE=1` for every spawned agent and verifier process, inherited by ordinary Python child commands in either mode. A regression test launches a nested Python import without `-B` and checks that no cache appears and the host environment is unchanged. Unexpected files, including deliberately created bytecode files, still fail verification; caches are not excluded from the audit. Agents that explicitly clear the environment or force bytecode generation can still create artifacts. Repeat the comparison with this environment fix before making broader claims.


## Repeated maintenance comparison and bounded reads, 2026-10-24

The post-bytecode-fix report `suite-20261024-154808-cmfz2q51.json` completed all 12 runs: every run passed 26/26 checks and comparison audits, with no timeouts or provider errors. This existing report was analyzed rather than rerun.

| Agent | Baseline median | FFSAKE median | Median calls baseline → FFSAKE | Median cost baseline → FFSAKE |
| --- | ---: | ---: | ---: | ---: |
| Claude | 19.333 s | 13.555 s | 9 → 7 | $0.0990739 → $0.0575753 |
| Codex | 38.514 s | 32.872 s | 3 → 24 | unavailable |

These are three repetitions per mode on a small synthetic task, not universal performance results. Observed tool-policy compliance does not establish host-enforced isolation. Codex cost remains unavailable.

`ffsake_read_files` (resident `read_files`) now accepts `files: [{path, start?, end?}, ...]`. It returns ordered `files` entries with the requested path and `status: "ok"` plus the existing `read` result, or `status: "missing"` for absent paths. Use it for explicitly selected source files, instruction-file probes and verification of changed/deleted paths. Single-file `ffsake_read` remains compatible.

Limits are 1–32 entries, 4096 UTF-8 path bytes, 256 KiB source bytes per file and 1 MiB returned text across the batch; JSON escaping adds bounded encoding overhead. Limits reject instead of truncating, even when a range selects only part of an oversized source. Invalid ranges, non-UTF-8 files, directories, internal/traversal/absolute paths and all symlinks reject the entire call without returning partial results. Missing paths alone are successful data. Reads occur sequentially and are not an atomic snapshot; mutation hash guards remain necessary. The seventeen-tool allowlist and shared agent guidance include this tool.


Targeted post-change Codex diagnostic `suite-20261024-155821-5xt6g6r5.json`: 26/26 checks passed, valid comparison audit, four FFSAKE calls and no tool errors: read_files → preview_files (seven operations) → apply → read_files. The first call read eight existing files and probed six instruction-file paths; the final call verified eleven paths, including both required deletions. Elapsed time was 28.637 s; this single diagnostic demonstrates interface adoption, not a repeated performance improvement. No new baseline runs were needed. Codex cost and host-enforced isolation remain unavailable/unverified.

Local validation: 48 Rust tests, 57 Python tests, Clippy with warnings denied, formatting, both binary builds, executable benchmark and MCP smoke passed. Smoke includes 130 rejected operations, with new checks for ordered mixed existing/missing reads, Unicode ranges, single-file equivalence, exact source/output limits, invalid ranges/schema, invalid UTF-8 and symlink/internal/workspace boundaries.


## Repository workflow and permission-history follow-up, 2026-10-24

A model-free disposable archive of `b7b02a5` contained 33 tracked files and 459,711 bytes. Two `ffsake_read_files` calls read the snapshot; one preview/apply performed six independent edit/move/copy/delete/create operations. Whole-tree byte comparisons passed after preview, apply, undo, redo and undo again. This tests integration on real repository contents, not agent performance, and supplies no timing comparison or isolation claim.

Marking the archived Claude launcher executable (0755), then deleting and undoing it, reproduced restoration as 0644. After the fix, the same probe restores 0755. `scripts/repository_smoke.py` makes these assertions against a disposable committed HEAD snapshot, prints its revision and the tested binary's SHA-256, and now runs in CI. Snapshot file counts will grow with the repository.

New Unix journal entries store optional `before_unix_mode` rwx bits for before-side files. Undo/rollback applies them only when recreating an absent file; replacement of an existing file still preserves its current permissions. Legacy manifests deserialize with no recorded mode and retain their prior behavior. Record-after-the-fact cannot recover lost before-side metadata. Setuid/setgid/sticky bits, ownership, ACLs and xattrs are not restored. Copy/move destination modes and newly-created-file modes on redo remain separate gaps. Regression coverage includes repeated reopened history, legacy manifests, and preserving user-changed current permissions.


The follow-up closes the Unix copy/move and new-file redo mode gaps. Copy/move previews capture source rwx bits, include destination mode in preview data, and reject a changed source mode before any batch writes. The journal records optional `after_unix_mode` from the actual atomic-write temporary file before rename, so recreated files keep that mode on redo. Existing-file replacement still preserves current permissions, and legacy manifests without the fields retain compatibility. Regression checks cover both copy and move over repeated reopened undo/redo, default-mode creation, and stale permission rejection of a mixed batch. No general chmod tool or metadata scripting interface was added.


## Journal history failure recovery

A deterministic two-file regression blocked the second atomic-write temporary path during undo. Before the fix, the first file reverted while the second remained applied. Undo/redo now restore earlier members in reverse order when a later write or final manifest update fails. A successful recovery leaves the persisted transaction state unchanged and permits retry after the obstruction is removed. Commit also rolls back workspace changes if its final manifest write fails.

Recovery preserves current ordinary Unix permissions when it must recreate a file removed earlier in the failed operation. If recovery itself fails, the returned error explicitly says `rollback incomplete` and includes affected paths; it does not claim atomic success. Tests include late write failures in both directions, manifest-write failure, retry, mode preservation and missing recovery data. This does not provide power-loss/crash atomicity or protection against hostile concurrent filesystem changes.


Persisted snapshot integrity is now checked before undo/redo changes any workspace file. Both sides are preflighted so recovery data is available; every snapshot is checked again against its stored content hash when used. Missing/corrupt snapshots, incorrect transaction identity or snapshot keys, non-file snapshots, and symlinked manifest/snapshot components reject replay. Regression tests exercise corrupt/missing before and after snapshots in both directions, unchanged workspace state on rejection, repair-and-retry, and journal symlink aliases. These hashes detect accidental corruption; they are not signatures against a writer who can alter both manifests and snapshots. The application-level confinement and concurrency limitations still apply.


Atomic-write boundary follow-up: a planted temporary symlink redirected writes in the old helper. The shared replacement helper now uses exclusive creation, preserves collisions without writing through them, applies destination permissions before file content, and removes only owned temporary files after failure. Library and MCP regressions verify untouched sentinels and destination state, collision preservation, safe retry, and direct-write permission retention. Resident documentation now describes the shipped batch-read, mixed-edit and history contracts in `docs/CLI.md`.


## Case-preserving rename primitive

The roadmap's case-preserving textual rename now has one guarded transform (`ReplaceIdentifierCasePreserving`) and one bounded MCP/resident preview (`ffsake_preview_rename` / `preview_rename`). It matches whole ASCII identifiers case-insensitively and preserves lower/upper/initial-capital styles; exact mixed-case source spelling uses the supplied destination, while ambiguous mixed casing fails. An explicit target, positive exact match count and 1–100 file limit are mandatory through MCP. Frozen hashes, preview purity, stale rejection and journal history are unchanged. The actual MCP catalog is checked against the shared eighteen-tool allowlist in smoke coverage. Tests validate casing, boundaries, count mismatch, invalid/ambiguous names, stale previews and undo/redo. Real-agent adoption and performance of this new primitive have not been measured.


## Rust syntax selection and compiler-backed limits

Tree-sitter selection is bounded to explicit Rust files and excludes comments, literals and opaque macro/attribute text. A syntax handle retains that origin: replacement previews validate Rust 2024 identifier spelling and reparse staged files. Ordinary indexed-search handles still accept literal replacement text. Real-source selection succeeded for lib.rs, syntax.rs and resident.rs; the repository snapshot workflow verified 20 selected Workspace occurrences across lib.rs/resident.rs with exact range edits and byte-exact undo/redo. This is integration evidence, not symbol resolution or a timing claim.

`python3 scripts/rust_syntax_smoke.py target/debug/ffsake-mcp` uses temporary standalone Rust files and external `rustc --edition=2024` compilation/execution. The checks passed locally on 2026-10-24 with rustc 1.99.1:

| Fixture | Selected occurrences | Applied result |
| --- | ---: | --- |
| Ordinary local binding | 2 | Compiles and passes runtime assertion |
| Two unrelated bindings with the same spelling | 4 | Both renamed; compiles and passes |
| Reference inside an opaque macro argument | 1 | Declaration renamed, macro reference remains; compiler reports E0425 |
| Replacement collides with an existing binding | 2 | Compiles, but shadowing changes the value and the runtime assertion fails |

All four baselines compile/run; preview leaves bytes unchanged; undo restores the exact source and passing baseline; redo reproduces both bytes and the expected compiler/runtime outcome. Compiler/runtime failures in the last two rows are required evidence of the documented limitations, not skipped checks. The script runs in CI without models or network. Runtime execution remains an external verification step. These results justify investigating symbol-aware references and collision handling before semantic rename, rather than treating parser success as behavioral correctness.


## Read-only Rust LSP feasibility

`python3 scripts/rust_lsp_probe.py` passed with rust-analyzer 1.99.1. It supplies an explicit dependency-free Rust project graph, disabling Cargo build scripts, procedural macros and check-on-save. Two unrelated same-spelling bindings each returned exactly their own declaration and reference; the second reference was inside a locally defined declarative macro. UTF-16 offsets mapped exactly to original UTF-8 bytes after an emoji, with CRLF and a percent-encoded Unicode/space workspace URI. The probe waits for healthy, quiescent server status, bounds time and response sizes, rejects unsupported server requests, and confines returned file ranges. Its source/Cargo/build-script fixture remains byte-identical with no extra files.

An initial probe with Cargo autoreload disabled reported a quiescent warning about an unloaded workspace change. Leaving autoreload at its default while providing the explicit graph produced healthy readiness. The regression requires healthy status rather than accepting that warning. This fixed-fixture evaluation runs in CI with the rust-analyzer toolchain component. It does not expose an LSP tool, execute semantic rename, prove full Cargo/dependency/sysroot analysis, or establish an OS sandbox.

Configuration and protocol follow the official [rust-analyzer settings](https://rust-analyzer.github.io/book/configuration.html), [explicit project format](https://rust-analyzer.github.io/book/non_cargo_based_projects.html), and [server-status extension](https://rust-analyzer.github.io/book/contributing/lsp-extensions.html#server-status).


The production `ffsake_rust_references` endpoint now uses a fresh temporary snapshot of explicitly supplied Rust files and an explicit crate root/edition. Its real-server MCP checks distinguish local bindings, include declarative-macro references, resolve a cross-file function reference, preserve Unicode/CRLF byte ranges and observe fresh sources on subsequent queries. All original files are hash-rechecked before results are returned. Results expose no edit handle and always report incomplete analysis because dependency/sysroot/feature context is omitted.

The protocol boundary enforces header/frame/total-data and wall-clock limits, refuses client-side execution/edit requests, confines locations to supplied snapshot files, and terminates the server after each request. Controlled server tests cover oversized framing, source changes during analysis, a stalled server, child cleanup and continued resident operation. Local validation includes 81 Rust and 60 Python tests plus the executable/MCP/repository/compiler/LSP checks. No agent timing comparison or semantic rename claim is made.


Definition navigation reuses the same snapshot transport and guards. MCP tests resolve local/macro binding definitions and a cross-file function declaration without editing source. An empty-module probe initially failed because a zero-width definition anchor was treated as an invalid reference; definition results now allow that valid file anchor while reference ranges remain nonempty. Regression coverage includes the real server result and location normalization. No dependency-aware completeness or semantic rename claim is added.


A document-symbol probe against the then-current LSP module returned 15 top-level entries and 40 total symbols, including structs/fields, implementations/methods, enums and aliases. The bounded outline endpoint now preserves that hierarchy and converts declaration/selection spans to original bytes and one-based lines. Symbol count includes descendants, with depth 32 and 4096-byte name/detail limits; malformed ranges, containment failures and overflow reject without truncating. Fixture coverage includes nested module/struct/impl nodes, Unicode/CRLF, empty documents and exact selection text. A later endpoint check on the expanded module returned 16 top-level entries / 42 symbols. Counts describe those source versions, not a fixed repository benchmark or performance claim.


Implementation navigation is verified against a locally declared trait with implementations in two source files, its trait method, a concrete implemented type and an unimplemented type. The server returned the two concrete type anchors and the two method-name ranges; the unimplemented fixture returned no ranges. Count overflow rejects the whole response. This extends the same explicit source-snapshot contract and does not establish dependency/feature completeness.


Hover/type integration covers a locally inferred `u32` binding and a documented `u32` → `u64` function signature. Returned hover ranges preserve original bytes after emoji and CRLF; text budgets count UTF-8 bytes across normalized content parts. The empty-file case returns a null hover, while oversized content and invalid limits/schema reject. Unit checks cover markdown/plaintext/code formats, optional ranges and malformed content. Documentation remains untrusted source data, and omitted dependencies/features still prevent completeness or compiler-validity claims.

## Full repository Rust analysis — model-free check

`scripts/repository_lsp_smoke.py` archives the committed revision into a disposable workspace and supplies all tracked Rust sources as three explicit local crates (library and two binaries). At `e2fe458`, this covered 23 Rust files / 372,466 bytes. References to `Utf16Position` produced 19 ranges across six files; definition, outline (53 symbols) and local constant hover checks passed. A native explicit-type action was previewed, applied and undone/redone with byte-exact whole-snapshot checks and frozen analysis-context metadata.

The local sample took 0.469–0.826 seconds for each of its five analysis requests, 0.196 seconds for the parser-checked preview and 0.017 seconds for apply. These are single model-free observations, not a repeated agent comparison or a general performance claim. Dependencies outside the declared local graph and sysroot remain omitted, `complete=false` is asserted, and the actual repository checkout is never edited by this probe. CI now repeats the correctness/history checks; timings and source counts may vary with the committed revision.


The `--verify-rename` mode adds independent `cargo test --locked --offline --workspace` checks in the disposable archive. At `835c6f1` (24 Rust files, 390,163 bytes), the baseline passed 98 Rust tests. A native `Utf16Position` → `Utf16Coordinate` proposal changed 22 occurrences across seven files and passed guarded preview/apply. Cargo then reported five expected E0422/E0425 errors for old references left inside macros in `coordinates.rs`. Undo/redo preserved exact whole-archive source bytes. CI asserts this specific observed failure class; unrelated compiler errors or an unexpected outcome fail the regression. This is evidence of incomplete macro context, not successful semantic refactoring, a performance comparison or a compiler check performed by the MCP endpoint.


Selected diagnostics at `59c0db9` retained all 24 Rust files as context and requested reports for `coordinates.rs` and `edits.rs`. Batch and individual queries returned identical 65 diagnostics / 3,579 text bytes. Five local iterations alternated request order: median batch time was 0.480 s for one request, versus 0.954 s combined for two individual requests. `--measure-diagnostics` reproduces the five-iteration comparison; ordinary CI checks one iteration without a speed threshold. This measures fresh-analyzer request overhead in this explicit, dependency-incomplete snapshot, not agent speed, diagnostic accuracy or general performance. The many native diagnostics do not contradict the independently passing Cargo baseline, which has different dependency context.

At its introduction, the opt-in MCP core profile retained eighteen generic file tools and rejected twelve Rust-specific tools. That serialized tool-definition audit measured 34,866 UTF-8 bytes for all thirty tools versus 11,691 bytes for core (23,175 bytes removed). The full catalog remains the default. This is a context-size observation only; no token count, billed-cost reduction or agent-speed benefit has been measured.


## Optional core profile in the shared agent harness

`--tool-profile core` selects the eighteen generic file tools for FFSAKE runs; omitted/default `all` selects the full catalog and its matching guidance. Baseline launches and task requirements are unchanged. Profile-aware launch configuration and guidance apply consistently to supported runners; OpenCode coverage here is mocked configuration testing, not a restart of paused model experiments.

```sh
python3 scripts/agent_benchmark_suite.py --agents claude,codex --tool-profile core --preflight-only
python3 scripts/agent_benchmark_suite.py --agents codex --tasks maintenance --modes ffsake --tool-profile core --repetitions 1 --timeout 900
```

Core preflight launches only the local MCP server in a disposable workspace and checks its exact catalog before any model call. The first command passed locally with eighteen tools / 11,691 serialized catalog bytes and zero model calls. It checks CLI/configuration readiness, not authentication, future model success or host isolation. The second command runs a targeted adoption diagnostic; the single-run evidence below does not establish comparative performance.

Schema version 3 introduced the requested profile metadata; current reports retain it, tag each FFSAKE result and keep aggregate groups separate by profile. Baseline rows have a null profile; untagged legacy FFSAKE rows are grouped as `all`. An observed call outside the selected core allowlist invalidates comparison even if the task verifier passes. Native Codex tools remain available, so trace auditing still does not establish enforced filesystem or shell isolation. The core server uses an explicit allowlist so future tool additions do not silently expand this profile.


The approved core-profile Codex maintenance diagnostic at `a807c4f` passed 26/26 checks in 32.869 s, with a valid trace and no unexpected file changes. Its four successful calls were read_files → preview_files → apply → read_files; no shell, out-of-workspace path, disabled-profile call, approval failure or provider error was observed. Local report: `suite-20261024-215852-6lo7_0vx.json`. This confirms adoption for one synthetic task. It is not a paired timing comparison, a cost measurement or proof of host-enforced isolation. No further model runs were performed.


New reports also include provenance: the source revision, SHA-256 hashes of both benchmark scripts and the MCP executable, plus each run's exact submitted task/guidance prompt hash and initial task-file manifest. The manifest is sorted and independent of the disposable root; it records relative paths, file/symlink kind, permission bits and content hashes before runner configuration or edits. Symlinks are hashed by target spelling without reading their targets. Harness control directories remain outside the task-file manifest, as in the verifier.

A source revision alone does not identify local modifications; the script hashes distinguish those inputs. Binary/source equivalence remains explicitly unverified. These identities do not include inherited agent configuration, provider-side prompts or model weights, and do not establish reproducible timing. Missing local files or Git metadata yield null identity fields. No older report is backfilled or rewritten.


## Combined symbol inspection — model-free comparison

The inspection-capable binary (`36c18e5de9b6326d0be625a633fc0596d1666a0bec162546c9b3ff48bf0f8b0c`) analyzed the committed `2656a9b` source archive with all 24 Rust files as explicit context. Combined definition/references/hover matched all three standalone reports, with 23 returned locations and 296 hover-text bytes. Five alternating samples measured a median 0.506 s for one combined request versus 1.516 s for three separate requests. `scripts/repository_lsp_smoke.py --measure-inspection` (after the binary argument) repeats this comparison; CI checks one iteration for equivalence without a timing threshold.

These are local analyzer/session overhead observations in an incomplete explicit snapshot, not agent-speed, model-cost or compiler-validity claims. The report records source revision and binary SHA independently. All thirty-one tools serialize to 37,100 UTF-8 bytes in this version, while the eighteen-tool core catalog remains 11,691 bytes. Catalog size is not a measured token bill.


Medium and application verifiers execute current source bytes directly instead of using Python's bytecode-loading path. This prevents verifier-created `__pycache__` artifacts and stale-cache reuse after edits that retain file size and timestamp. Regression coverage preserves an existing stale cache unchanged while verifying the new source, and confirms every initial shared-suite fixture fails its completion verifier without changing task files. No local legacy-script edits or historical benchmark reports are rewritten.


New suite results also audit task files after verification. `verification.verification_file_changes` lists any creation, deletion or byte change caused while executing checks, including changes to implementation files that the agent was originally allowed to edit. Any such change makes the result incorrect, even if behavioral checks returned success. `changed_files` and `unexpected_file_changes` retain their pre-verification meaning, so verifier-time side effects are not misattributed to agent edits. The initial fixtures and reference implementations remain unchanged by their normal checks; historical reports without this field have not been retroactively audited.


Schema version 4 preserves completed agent output when an operating-system error prevents either post-run file audit. Such results have `correct: false`, `comparison_valid: false` and the reason `file_audit_failed`, so they cannot contribute performance medians. `verification.file_audit_errors` records the failed phase and error; `file_audit_complete` is false. Unknown change lists are null rather than empty. A failed pre-verification snapshot skips behavioral checks; a failed post-verification snapshot retains the behavioral result details but cannot establish correctness. Successful audits retain ordinary change lists. Input-fixture failures before any agent starts still stop normally.


Matrix selectors (`--agents`, `--tasks`, `--modes`) must be nonempty and contain distinct values; use `--repetitions` for intentional repeated samples. Invalid selections stop before CLI preflight or model calls. The harness's local MCP-binary prerequisite applies only when FFSAKE mode is selected. Baseline-only matrices skip that prerequisite and the core-catalog preflight, while retaining agent CLI/configuration checks and trace auditing.


## Directory layout acceptance task, 2026-10-25

The opt-in `layout` task reorganizes an existing runnable Python package, copies a template tree containing binary bytes and an executable file, removes a populated retired tree, creates an empty output directory, and edits the package invocation in a CRLF README. Both modes receive identical behavior requirements; a correct solution need not use any particular sequence or tool. Default task selection is unchanged.

The verifier checks the exact file/byte mapping, exact directories (including empty ones), absence of symlinks, ordinary Unix modes when applicable, and relocated CLI output. Filesystem checks run after the CLI so runtime-created directories or mode changes cannot evade them. Regression tests reject binary changes, missing/extra empty directories, altered permissions, broken runtime and sentinel changes.

`python3 scripts/layout_smoke.py target/debug/ffsake-mcp` passed using the core profile with one five-operation preview, apply, undo and redo. The original file and directory set returned after undo, and the verifier passed after apply and redo. CI runs this model-free smoke. No agent run, timing comparison, discoverability claim or new isolation claim has been made for this task.


Directory-aware fixture provenance now records a sorted path/mode list for task directories, including empty ones, alongside existing file identities. `fixture_manifest_version: 2` identifies the combined file/directory fingerprint; old reports are unchanged and lack this new directory evidence. Directory checks share the same harness-control exclusions as file checks, so Codex's generated `.codex` configuration is not mistaken for task output. An unexpected ordinary project directory still fails the layout verifier. Regressions reproduce and fix both the previous empty-directory fingerprint collision and false rejection of generated runner configuration.


## Real C++ LSP acceptance (2026-10-25)

On pinned TinyXML2 11.0.0 (`9148bdf719e997d1f474be6bcc7943881046dba1`), independent Claude Code runs renamed the public `XMLDocument::NewElement` API across three source files, preserving comments/literals/CRLF and correcting a disabled example omitted by clangd. Both exact-byte audits and all 517 upstream tests passed. Core text tools: 13 calls, one unsupported-regex error, 50.417 s, $0.3360204 reported cost. Full-profile semantic assistance: 12 calls, zero errors, 43.510 s, $0.4383350 reported cost; 28 native edits plus one independently identified inactive-source correction, two transactions versus three. No native repository tools or host coaching were used.

One pair, fixed order, different catalog sizes and an explicit semantic treatment prompt do not establish general performance. Correctness was equal; observed speed/call improvements came with higher reported cost. Native proposals missed inactive code, so source review remained necessary. Full report/raw traces/reproduction: `docs/demo-runs/20261025-lsp-acceptance/REPORT.md` (local evidence retained).

Lifecycle probe on the same 249,761 source bytes: five equal-result samples measured median 0.134842 s cold versus 0.000877 s warm, retained startup 0.132311 s. Only unchanged documents were measured. The actual agent used one semantic request, so on-demand remains the default; persistent invalidation/resource correctness is not established by this probe. Reproduce with `python3 scripts/lsp_lifecycle_probe.py PINNED_TINYXML2_CHECKOUT`.

## Release-candidate Claude comparison, 2026-10-30

This matrix exercised the five release-candidate workflows: a two-module percentage-discount fix (`cross`), existing-project maintenance (`maintenance`), discovery and live observation of a named build task followed by repair (`build`), reviewed Git staging and commit while preserving unrelated work (`git`), and debugger-assisted diagnosis of a payroll failure (`debug`). It is a small paired study, not a general performance claim.

### Scope and method

- Claude Code `2.1.314`, host model alias `sonnet`, reported model `claude-sonnet-5-5`, `--effort low`, 40-turn cap. Runs were on macOS ARM64 with Python `3.14.6`.
- Five tasks x two arms x five matched repetitions = 50 runs. Each cell has five runs. Order was counterbalanced by task and repetition.
- The task prompt was identical in both arms and native tools remained available in both. The baseline arm had no FFSAKE MCP server. The FFSAKE arm advertised the `core` profile (38 tools in this revision, 31,468 catalog bytes) and appended host guidance naming FFSAKE for repository reading, search, previews, edits, Git, tasks and debugging without removing native tools.
- Each run used a fresh full Git clone with `.git` inside the disposable workspace. A separate one-repetition pilot was used to fix harness and fixture defects; it is not pooled with this matrix.
- Behavior verifiers ran after the agent. A post-run file audit rejected changes outside each task's allowlist. `build`, `debug` and `git` also required trace-visible workflow evidence: task run plus output polling, debugger state inspection plus resume, and review before commit, respectively.
- Permissions were noninteractive for disposable fixtures. This does not test host approval flows, authorization persistence or host-enforced sandboxing.

An earlier six-repetition attempt stopped on a Claude provider spend-limit error (`429`, `usage_limit_reached`); it is not pooled. Codex `0.163.0` was authenticated but returned a workspace-out-of-credits error, so no Codex measurement was possible. OpenCode `1.19.5` had credentials available, but repository instructions pause OpenCode benchmarking and provider/approval parity was not established. This report is therefore Claude-only.

### Results

| Task | Native correct | FFSAKE correct | Native median cost | FFSAKE median cost | Native median wall | FFSAKE median wall | FFSAKE called |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cross | 4/5 | 5/5 | $0.081 | $0.086 | 13.6 s | 12.7 s | 1/5 |
| maintenance | 5/5 | 5/5 | $0.104 | $0.117 | 21.0 s | 24.1 s | 2/5 |
| build | 5/5 | 5/5 | $0.103 | $0.164 | 31.6 s | 28.5 s | 5/5 |
| git | 5/5 | 5/5 | $0.074 | $0.090 | 10.7 s | 14.6 s | 3/5 |
| debug | 5/5 | 5/5 | $0.091 | $0.191 | 18.2 s | 40.5 s | 5/5 |

`FFSAKE called` counts runs with at least one FFSAKE tool call; it does not mean exclusive use. Medians describe each cell and are not paired estimates.

| Arm | Correct | Reported cost | Wall time | Total input tokens | Output tokens | Tool calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 24/25 | $2.2678 | 471.4 s | 3,964,974 | 38,504 | 188 |
| FFSAKE | 25/25 | $3.3006 | 622.8 s | 8,613,701 | 52,820 | 376 |

Wall time is the sum of harness-measured per-run elapsed time. Reported cost is the provider-reported cost, not an invoice. Input totals include cache reads; uncached input was only 306 tokens for native and 626 for FFSAKE, so most of the input-token difference is repeated cached context from longer sessions. Cache reads were 3,583,724 versus 8,123,360 tokens, and cache creation was 380,944 versus 489,715.

| Task | Paired cost ratio | Paired wall-time ratio |
| --- | ---: | ---: |
| cross | 1.05 (0.91-1.14) | 0.95 |
| maintenance | 1.06 (0.90-1.46) | 1.08 |
| build | 1.60 (1.46-1.74) | 1.02 |
| git | 1.25 (1.11-1.58) | 1.35 |
| debug | 2.14 (1.68-2.54) | 1.85 |

Ratios compare FFSAKE with native on the same task and repetition; values above 1 mean FFSAKE was more expensive or slower. The parenthesized range is the minimum and maximum of the five paired cost ratios.

### Findings

- Correctness was 25/25 for FFSAKE and 24/25 for native. The single native failure passed the behavior test but also edited `README.md`, outside the allowed change set, so the file audit rejected it. This one-run difference is descriptive, not a significance test.
- FFSAKE overhead was concentrated in build, Git and debug. Median paired cost was 1.60x for build, 1.25x for Git and 2.14x for debug; cross and maintenance were near parity but variable. Overall reported cost was 1.45x and wall time 1.32x. The build cell's unpaired median wall time favored FFSAKE slightly, while the paired median was 1.02x, so the build result does not establish a speed improvement.
- FFSAKE tools were called in 16/25 FFSAKE runs. Adoption was 5/5 for build and debug, 3/5 for Git, 2/5 for maintenance and 1/5 for cross. Only 3/25 FFSAKE runs avoided native file or shell tools entirely, all in build. Native fallback remained common even when FFSAKE was used; the task prompts did not require exclusive FFSAKE use, and native tools were intentionally left available.
- The original automated workflow-marker checks reported 30/30 passes for build, debug and Git. A later audit found those checks did not establish every requested detail: for example, output markers plus a polling call do not prove output was observed before completion, and the original Git check could count a staging mutation as a commit. Treat those workflow claims as unverified until the surviving traces are independently checked. The pre-cleanup report recorded no timeouts, provider errors, invalid comparisons, MCP failed calls, human interventions or nonzero exits; retry counts were not reliably measured. The surviving transcripts show at least one native command was blocked for approval, so the earlier claim of no approval blocks was too broad. It reported all 50 file audits completed and one unexpected native `README.md` change.

### Evidence and limitations

The original consolidated harness JSON and logs were written under `/private/tmp/ffsake-onboarding`, which macOS removed before the report was committed. The 50 Claude Code session transcripts survived under `~/.claude/projects` and were copied to local salvage storage. A raw archive was briefly published but removed from the public tree and reachable branch history because it contained personal information. GitHub still served the old commit by ID after the rewrite; cached objects and copies require separate cleanup. `docs/agent-benchmark-runs/rc-claude-20261030-2.reconstructed.json` is derived from the transcripts and records per-run role, prompt, cost, token components, tool calls and the reported correctness outcome. Its cost, token, tool-call, adoption and correctness totals match the reported pre-cleanup aggregate. Per-run harness wall time and exact verifier metadata were not recovered; the aggregate wall time, correctness, workflow-evidence and safety counts above are reported from the pre-cleanup report and cannot currently be independently reverified from the surviving verifier records.

Five repetitions per cell, one model alias, one host and one operating system do not support confidence intervals or general speed/cost claims. The model alias does not identify model weights, and the installed Claude Code version may change. Noninteractive permissions mean the run does not establish approval behavior or OS isolation. Codex and OpenCode are absent, so this is not a cross-agent comparison. The visible native fallback also means FFSAKE adoption is not the same as enforced routing.

Reproduce with a fresh output path using the command in `docs/agent-benchmark-runs/README.md`. A re-run writes complete harness JSON but will use the current Claude Code version and may not match this sample.

## Release-candidate Codex comparison, 2026-11-01

This is a new sample, not pooled with the reconstructed Claude results. Codex CLI `0.163.0` used the requested model name `gpt-6-astra` with reasoning effort `low`, on an Apple M1 Pro MacBook Pro with 16 GiB RAM and Python 3.14. The CLI did not report an immutable model identifier in these traces. The benchmark source revision was `f8b891c0f79377160f7a07919e7555cc62b9f922`; the optimized MCP binary SHA-256 was `1c0916bfa2a2149bad1caaa4ecb612db8cd2c933d6623cc46968f2635e0b6fd6`. The binary was built from the preceding source tree; the intervening commit changed benchmark code and documentation, not FFSAKE source or Cargo inputs.

Five repetitions per arm were run on four synthetic tasks: a cross-file fix, a maintenance change involving creation/move/delete, live test-output inspection and repair, and debugger-assisted diagnosis. Each run used a fresh linked worktree from a frozen fixture. Task prompts, model settings and initial file/Git-state hashes matched within each pair; order alternated. The FFSAKE arm had the core MCP catalog and explicit FFSAKE-first host guidance; native tools remained available. The baseline had no FFSAKE server. The core catalog was 38 tools and 31,468 serialized bytes; bytes are not a measured token cost. Both arms used deliberate noninteractive allowance in disposable fixtures. Approval-prompt friction was not measured.

Cross-file and maintenance are focused editing comparisons; build and debug are whole-workflow comparisons. They should not be pooled into one editing-performance figure.

**Git is not a scored Codex pair:** in pilots, Codex's native workspace sandbox denied writes to the linked worktree's Git index while the MCP server could commit. A full-clone pilot had the same native restriction. An attempted common host-isolation profile prevented Codex itself from initializing. Scoring the unequal setup as an FFSAKE win would be misleading. OpenCode `1.19.5` returned HTTP 401 on its configured provider's token refresh; it has no measured runs.

One separate, model-free setup sample used a new installation prefix and empty Cargo build directory, with the source checkout and existing toolchain/download caches already present: source installation took 58.56 s, Codex project configuration 0.120 s, doctor 0.234 s and repeated installation 0.171 s. The repeat reported an unchanged installation. This is not a fully cold download, an interactive approval measurement, or a measured native-host installation. [Setup evidence](agent-benchmark-runs/20261101-codex/setup.json).

### Outcomes and elapsed time

Each row has five runs per arm. Times are median seconds with the minimum–maximum in parentheses; they include failed and timed-out attempts. “Complete” requires both the external artifact checks and the trace-visible workflow evidence. All 40 final fixture states passed the independent file and behavior checks; the timed-out debugger agent did not finish despite its resulting program passing the external check.

| Task | Native complete | FFSAKE complete | Native time | FFSAKE time |
| --- | ---: | ---: | ---: | ---: |
| Cross-file fix | 5/5 | 5/5 | 22.2 (21.5–27.9) | 35.3 (32.8–40.0) |
| Maintenance | 5/5 | 5/5 | 36.8 (25.9–39.3) | 44.7 (39.2–49.7) |
| Live test and repair | 0/5 | 5/5 | 41.6 (39.6–44.6) | 56.0 (48.2–58.9) |
| Debug and repair | 5/5 | 4/5 | 39.0 (36.4–44.9) | 124.4 (95.2–300.0) |

The native live-test runs all passed the final test, but their captured command results did not establish that the agent observed output *before* completion; that is missing evidence, not a failing repair. All five FFSAKE traces included an incremental output response marked incomplete. One FFSAKE debugger run timed out at 300 seconds; its trace contained runtime-state evidence and the externally rerun program passed. The native debugger used Python `pdb`; FFSAKE used debugpy `1.8.17`, so adapter workflows and call counts differ.

### Tokens and calls

Values are medians with minimum–maximum in parentheses. Input includes cache reads; output and input are separate quantities. Sample size is five per cell except FFSAKE debug token fields, which have **four**: the timed-out run did not report final usage. Codex reported no dollar cost, so cost is unavailable rather than zero. Tool-call counts are host-reported calls; batching and interface differences limit direct comparisons.

| Task / arm | Total input tokens | Cached input tokens | Output tokens | Tool calls |
| --- | ---: | ---: | ---: | ---: |
| Cross native | 72,246 (64,732–89,889) | 65,536 (55,552–77,568) | 449 (429–497) | 4 (4–7) |
| Cross FFSAKE | 210,037 (182,063–239,467) | 192,512 (164,736–216,320) | 572 (570–605) | 15 (15–16) |
| Maintenance native | 93,580 (50,163–111,241) | 86,272 (45,312–104,064) | 1,220 (1,147–1,429) | 4 (2–5) |
| Maintenance FFSAKE | 219,025 (202,169–234,454) | 198,016 (180,224–211,712) | 1,349 (1,302–1,370) | 24 (18–24) |
| Build native | 210,259 (209,990–210,798) | 193,280 (178,688–193,920) | 709 (647–734) | 7 (7–7) |
| Build FFSAKE | 338,220 (333,714–364,200) | 319,744 (308,480–343,936) | 909 (846–950) | 24 (22–25) |
| Debug native | 121,154 (119,291–166,027) | 110,976 (92,416–148,992) | 697 (679–808) | 6 (5–7) |
| Debug FFSAKE | 888,095 (790,174–1,006,429), n=4 | 849,792 (745,984–962,432), n=4 | 1,594 (1,465–1,806), n=4 | 41 (33–41) |

All 20 FFSAKE-arm runs called FFSAKE, and native tools remained available. No unexpected fixture-file changes were recorded. The host reported failed tool/command events in some runs, including expected failing tests; these counts do not identify retries. Retry counts are unavailable, and there were no human interventions during the unattended runs. A single fixture, host and small sample do not establish a general performance or safety advantage. In this sample, FFSAKE used more time and input tokens in each task, and debugger polling was a visible usability cost.

### Safety and evidence

Separate, model-free checks exercised denied access, stale previews, history undo/redo, unrelated staged/unstaged Git preservation, changed task authorization, live logs, cancellation and owned-process cleanup. They test FFSAKE's guards and documented boundaries; authorized task/debug execution is not an OS sandbox. [Safety results](agent-benchmark-runs/20261101-codex/safety.json) and the [redacted per-run event indexes](agent-benchmark-runs/README.md#2026-11-01-codex-measured-matrix) are available. Those indexes deliberately omit prompts, commands, arguments, tool output, paths and session IDs; the full raw transcripts remain local and are required to independently review every evidence decision.

To reproduce after installing the dependencies and building the release binary, use a fresh private output path:

```sh
python3 scripts/rc_benchmark.py --binary target/release/ffsake-mcp \
  --output /private/path/to/new-codex-report.json --agents codex \
  --tasks cross,maintenance,build,debug --repetitions 5 \
  --debug-python /absolute/path/to/debugpy-venv/bin/python
python3 scripts/rc_safety_evidence.py target/release/ffsake-mcp /private/path/to/new-safety.json
```

The driver refuses a scored Codex Git run with the known unequal sandbox setup. A new study should record its own source/binary identities, host settings and model availability rather than pooling results across revisions.

For a separate source-setup timing, run the following from the source checkout. This intentionally uses an isolated host configuration and does not measure interactive approval:

```sh
(
BENCH_DIR="$(mktemp -d)"
mkdir -p "$BENCH_DIR/project"
export CARGO_TARGET_DIR="$BENCH_DIR/target" CODEX_HOME="$BENCH_DIR/codex" XDG_CONFIG_HOME="$BENCH_DIR/xdg"
/usr/bin/time -p python3 scripts/ffsake_install.py --prefix "$BENCH_DIR/install" install --source
/usr/bin/time -p python3 scripts/ffsake_install.py --prefix "$BENCH_DIR/install" configure --host codex --project "$BENCH_DIR/project" --guidance --apply
/usr/bin/time -p python3 scripts/ffsake_install.py --prefix "$BENCH_DIR/install" doctor --host codex --project "$BENCH_DIR/project"
/usr/bin/time -p python3 scripts/ffsake_install.py --prefix "$BENCH_DIR/install" install --source
)
```

## Cross-file interaction optimization, 2026-11-01

We audited all five prior Codex cross-file pairs and then repeated five matched pairs after changing only FFSAKE's tool descriptions and server guidance to suggest existing batch reads and a combined, reviewed multi-file preview. The prompt, fixture, verifier, benchmark driver, host guidance, model request and settings were frozen before the change. Every run had the same prompt SHA-256 `bd2bc4505d341d2330eebcd2e1f88933716069d00533c281f5b13be8fba66e50` and initial fixture manifest `3443a91a40dfc620a441db1da155b0b365e8170bba0dcbe1abf9eededc8f0b48`; initial staged and unstaged diffs were empty. The earlier source revision and binary are identified in the preceding section. The changed source revision was `38bc0c8f18c69325b1736b2e7c27e4d7191f4b38`, binary SHA-256 `5ee31f025784f41055da907742b32f973f2d88f3db5f97db866236787f865c76`. The driver SHA-256 was `de6d5b8ea854815535a18f471ba14154d4daf753b28a3ea2534399e0c1ec1140`; fixture implementation SHA-256 `adb219bcb9d4d272084ba5ec8e9e9b7b9c9fa3ed30a8970d86b124239ef87839`. Codex CLI 0.163.0 requested `gpt-6-astra`, reasoning low; its immutable model version was not reported. Runs used an Apple M1 Pro with 16 GiB RAM, the core profile, FFSAKE-first guidance, native tools available in both arms, and noninteractive allowance. Order alternated within each set of five pairs. The before and after sets were run at different times; model and service latency can vary.

In each old FFSAKE trace, four files were read separately and two files received separate preview/apply cycles. All five also listed the workspace, discovered tasks, checked Git before and after, and listed files; three searched for the relevant term and three made an extra Git status call. The native arm generally used two shell calls to explore, one edit call and one shell call to test and inspect the diff. The task discovery was unnecessary for this fixture; workspace checks still matter for subagents and must not be skipped as a general rule. Estimated compact JSON sizes of captured MCP results were 5,616–6,357 bytes per original FFSAKE run; captured native command outputs were 1,162–2,170 bytes. Those are differently encoded observations, not token counts or a controlled decomposition of model context.

All ten follow-up runs passed independent checks, with no timeouts or unexpected file changes. FFSAKE was used in 5/5 FFSAKE runs; each also used one native command for testing. Both native and FFSAKE arms remained able to use native tools. Every new FFSAKE run batched reads and used one reviewed multi-file preview and apply; the old runs used neither batch. One old native and one new FFSAKE run recorded a tool error; no other run did. Retry counts and cost were not reported. No human intervention occurred, and approval prompt friction was not measured.

Values are median (minimum–maximum), five runs per cell. Total input includes cached reads; uncached input is total minus cached input per run. Cache writes were reported as zero in all runs. Medians of components need not add to the median total.

| Measure | Old native | Old FFSAKE | New native | New FFSAKE |
| --- | ---: | ---: | ---: | ---: |
| Correct | 5/5 | 5/5 | 5/5 | 5/5 |
| Calls | 4 (4–7) | 15 (15–16) | 4 (4–4) | 10 (9–11) |
| Seconds | 22.2 (21.5–27.9) | 35.3 (32.8–40.0) | 23.8 (23.1–28.4) | 43.4 (40.7–49.5) |
| Total input | 72,246 (64,732–89,889) | 210,037 (182,063–239,467) | 72,051 (64,747–72,277) | 213,648 (200,615–216,144) |
| Cached input | 65,536 (55,552–77,568) | 192,512 (164,736–216,320) | 59,264 (56,448–65,408) | 195,840 (184,064–197,632) |
| Uncached input | 6,838 (6,608–12,321) | 17,525 (17,327–29,451) | 12,075 (6,605–13,013) | 18,440 (16,551–23,422) |
| Output | 449 (429–497) | 572 (570–605) | 443 (429–455) | 578 (536–631) |

This guidance removed interactions, but the measured median time and token totals did not improve. The core catalog grew from 31,468 to 31,687 serialized UTF-8 bytes and initialization instructions from 938 to 1,209 bytes; exact catalog tokens were not measured. The results do not establish catalog size as the cause of token use or isolate the cost of repeated conversation context. A combined transaction also makes undo/redo operate on the batch together. Preview review, stale rejection, authorization, and journal guards remain unchanged; the MCP smoke and access checks exercise those boundaries.

### Debugger polling investigation

The prior five debugger pairs are a useful warning, not a clean startup comparison. FFSAKE runs issued 12–15 explicit poll actions each and took a median 124.4 seconds (one 300-second timeout); native took 39.0 seconds. Four completed FFSAKE runs launched the adapter twice, while native used one `pdb` session and a separate program run, so they performed different startup work. Their event streams do not contain per-call timestamps. Both arms observed the same synthetic values (`hours=40`, `base=1000`, `adjustments=[50.0]`), but overall agent time cannot be assigned to adapter startup, polling or model latency from those traces.

A separate, model-free probe used one debugger launch per arm, a breakpoint hit twice and inspection of those values, then cleaned up the process. Across three counterbalanced samples, native `pdb` reached its first prompt in 73–90 ms and completed inspection in 75–91 ms; FFSAKE/debugpy 1.8.17 reached its configuring state in 311–413 ms and completed inspection in 571–650 ms. These startup states are different protocol phases, and the probe is not an agent benchmark. Its DAP path used 48–50 MCP calls, 39–41 polls, with the existing smoke helper polling every 25 ms; those counts reflect that helper's aggressive polling, not an inherent minimum. All samples observed the expected values and cleaned up their owned processes. This supports investigating more efficient waiting, but does not establish an end-to-end agent improvement; no debugger feature or guard was changed.

[Sanitized run indexes and probe results](agent-benchmark-runs/README.md#2026-11-01-cross-file-optimization) omit personal transcripts, prompts, arguments and paths; full evidence and frozen binaries are retained privately. The index alone cannot verify every interpretation. Reproduce on an isolated project, writing raw results only to private storage:

```sh
python3 scripts/rc_benchmark.py --binary target/release/ffsake-mcp \
  --output /private/path/to/cross-result.json --agents codex --tasks cross \
  --repetitions 5 --debug-python /absolute/path/to/debugpy-venv/bin/python
python3 scripts/debug_poll_probe.py target/release/ffsake-mcp \
  --python /absolute/path/to/debugpy-venv/bin/python \
  --output /private/path/to/debug-probe.json --repetitions 3
```

This is one small fixture, model request and host configuration, and separate before/after periods. It does not establish general speed or token efficiency.

## Interaction overhead study, 2026-11-02

The previous guidance-only change reduced calls but did not improve elapsed time or total input. This study treats that as unsuccessful and measures three independent interaction designs before changing the product. On the same synthetic cross-file task, Codex CLI 0.163.0 requested `gpt-6-astra` at low reasoning on an Apple M1 Pro with 16 GiB RAM. The immutable provider model identifier was not available. The prompt, verifier, fixture manifest, empty initial staged/unstaged diffs, model request and host allowances were frozen before the five-repetition study. Each repetition used independent disposable worktrees and cycled the order of all five conditions. Native tools remained available; noninteractive auto-approval means approval friction was not measured. The native condition had no MCP server.

The FFSAKE conditions used the same baseline product and guarded editing operations. **Core** exposed 38 tools. **Editing** used the host's supported tool filter to expose 25, retaining the file and safety operations needed by this task. **Compact** experimentally removed only an identical repeated nested file path from successful batch-read responses; errors and the reviewed diff were preserved. **Context** placed a startup snapshot from the actual workspace identity, task-list and Git-status tools in the host instructions, labelled as potentially stale. It made three extra setup tool calls; their elapsed time was included in the run time and they are counted separately below. These diagnostics are not an enforced isolation or a claim that the 25-tool catalog supports all core workflows.

All 25 attempts passed the external verifier, with no timeouts or unexpected file changes. FFSAKE was used in all 20 FFSAKE-condition runs. None used a native file-edit call; each used one native command for verification. One native and one context run recorded a tool or command error; the other runs recorded none. No human intervention occurred. Values are median (minimum–maximum); five runs per condition. Input includes cached input and is accumulated across model turns, **not the size of unique context**. Cache writes were reported as zero; dollar cost and retry counts were unavailable.

| Condition | Correct | Seconds | Agent calls | Total input | Cached input | Uncached input | Output |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 5/5 | 34.6 (32.3–37.1) | 4 (4–5) | 72,216 (72,061–81,624) | 65,408 (60,032–71,808) | 9,816 (6,642–12,213) | 447 (442–511) |
| Core | 5/5 | 51.1 (47.5–53.8) | 10 (9–11) | 213,173 (181,331–233,734) | 195,456 (159,232–207,104) | 26,630 (17,717–28,317) | 574 (539–600) |
| Editing catalog | 5/5 | 44.7 (43.5–48.1) | 8 (8–8) | 181,971 (179,293–182,114) | 164,480 (147,840–164,992) | 17,222 (16,979–31,453) | 449 (446–474) |
| Compact responses | 5/5 | 54.0 (50.0–69.3) | 9 (9–10) | 213,496 (207,095–214,794) | 190,208 (179,072–196,736) | 18,058 (16,887–34,513) | 555 (540–577) |
| Startup context | 5/5 | 52.3 (48.8–59.3) | 8 (7–9), **plus 3 setup calls** | 214,328 (207,264–232,556) | 196,224 (190,336–205,440) | 18,104 (16,928–27,116) | 541 (522–579) |

The serialized catalog definitions measured 31,687 UTF-8 bytes for core and 18,884 bytes after filtering, not a direct token measurement. Compact batch-read responses saved 101 wire bytes in each applicable run and did not improve the median time. The context condition shifted discovery out of model-issued calls but did not reduce the median time or total input; its setup was included, not free. The narrower catalog was the only diagnostic with lower median time and total input versus core, while still slower and higher-input than native. These small samples do not isolate whether its change in catalog, model behavior, call count or service latency caused the differences.

The benchmark-only `benchmark-timing` build and content-free MCP proxy recorded request/response sequence, wire bytes and monotonic timings. For core, the median per-run sum of measured MCP round trips was 211 ms; the median sum of tool dispatcher durations inside the server was 181 ms. These sums have different boundaries: round trips include initialization and catalog calls; the dispatcher measurement covers `tools/call` handling and excludes final response I/O. Relay overhead, scheduling, queueing and measurement perturbation are included. The many remaining seconds include host/model work, native commands, setup and teardown. The trace cannot split model inference from host processing or reliably attribute those gaps to a catalog or particular tool. Asynchronous debugger waits have no internal dispatcher timing in this instrumentation. Full raw transcripts are retained privately, and the public [sanitized evidence](agent-benchmark-runs/20261102-performance/diagnostic-cross.json) contains no tool arguments, file content, paths or session IDs.

### Debugger waiting

The earlier agent traces had frequent debug polls, so the changed server supports a bounded asynchronous wait without blocking ordinary MCP calls. It preserves the nonblocking poll operation, cancellation, stale handles, access rechecks and owned-process cleanup. In five counterbalanced **model-free** pairs against debugpy 1.8.17, the existing helper's 25 ms polling path used a median 48 MCP calls (39 polls); the wait path used 20 calls (11 waits). Both observed `hours=40`, `base=1000`, `adjustments=[50.0]` and cleaned up their adapter. Median time to the same configuring phase was 315 ms in both; through inspection it was 580 ms with polling and 490 ms with waiting, including adapter and local client work. Total including cleanup was 863 versus 768 ms. Polling frequency is a property of that helper, not a protocol minimum; these are not agent-level token or speed results. [Synthetic probe data](agent-benchmark-runs/20261102-performance/debug-poll-vs-wait.json). A later stalled-reader shutdown regression required a corrected build. Five fresh model-free pairs on that build had median startup 327 versus 324 ms, through-inspection 623 versus 512 ms, total 907 versus 807 ms, and calls 47 versus 20; all observed the same values and cleaned up. These still reflect the helper’s polling schedule, not a model benchmark. [Corrected-build probe](agent-benchmark-runs/20261102-performance/debug-poll-vs-wait-corrected.json).

### Contemporaneous build comparison

After the diagnostics, the server gained an opt-in editing profile exposing the same 25-tool set and an asynchronous debugger wait. The old and changed instrumented builds, native baseline, prompts, fixtures, verifiers, host allowance and model settings were then frozen. The three modes rotated order within each repetition; the changed build used `--profile editing` for the editing tasks and `--profile core` for debugging. Full/core remain available for tasks, Git mutations and debugging. Product configuration and the ordinary build do not enable benchmark timing. [Frozen identities](agent-benchmark-runs/20261102-performance/comparison-freeze.json) identify the exact inputs.

Five runs per cell; median (minimum–maximum). Total input includes cache reads, and cumulative token counts are not unique context. The baseline has native tools only. Both FFSAKE modes allowed native fallback and used the same prompt and external checks.

| Task / mode | Correct | Seconds | Calls | Total input | Cached input | Uncached input | Output |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Cross native | 5/5 | 33.3 (28.7–56.4) | 4 (4–5) | 72,164 (64,798–72,316) | 60,032 (47,744–65,536) | 6,780 (4,894–24,421) | 445 (439–471) |
| Cross old core | 5/5 | 62.5 (56.6–113.0) | 10 (10–11) | 214,112 (208,657–214,333) | 196,224 (178,816–196,480) | 17,888 (17,827–34,485) | 598 (555–649) |
| Cross changed editing | 5/5 | 46.8 (41.6–84.9) | 7 (7–7) | 171,496 (171,469–171,634) | 156,544 (156,288–156,544) | 15,090 (14,952–15,185) | 413 (394–419) |
| Maintenance native | 5/5 | 47.5 (44.4–56.5) | 3 (3–5) | 74,728 (73,752–111,358) | 67,840 (62,080–98,432) | 12,399 (6,680–13,784) | 1,272 (1,186–1,372) |
| Maintenance old core | 5/5 | 64.4 (58.5–70.1) | 12 (11–12) | 192,762 (192,396–193,013) | 172,928 (171,648–173,184) | 19,829 (19,578–21,155) | 1,313 (1,280–1,449) |
| Maintenance changed editing | 5/5 | 59.0 (51.9–73.5) | 10 (9–10) | 153,485 (153,034–180,037) | 136,064 (135,296–149,632) | 18,189 (16,970–33,225) | 1,200 (1,105–1,291) |

All 20 FFSAKE editing runs used FFSAKE and no native file-edit call; each used one native command. Both native editing conditions had one observed tool/command error and completed successfully; no FFSAKE editing run had a recorded tool error. There were no timeouts or human interventions. Median sums of MCP tool-response bytes were 5,676 old versus 4,429 changed for cross, and 11,892 versus 11,321 for maintenance. On cross, the initialization response was 1,371 versus 822 wire bytes and the tool-list response 31,732 versus 18,929. These are protocol bytes, not token counts. Median sums of cross tool-call round trips were 181 ms old and 193 ms changed; their corresponding server dispatcher sums were 178 and 190 ms. Model/host gaps and native commands dominate the remaining elapsed time, but cannot be separated by these traces. The changed catalog, shorter editing-profile initialization guidance and resulting agent call sequences vary together in this final comparison.

Both editing fixtures show lower median calls, total input and elapsed time for the changed profile in this small sample. The ranges overlap, the native baseline still used less elapsed time and total input, and the observed difference is not a general performance guarantee. The editing profile omits task execution, Git mutations, debugging and language intelligence; use the core/full profile or appropriately approved host tools when needed.

The first 45-run comparison also included five debugger runs per mode, all correct with no timeout. The measured changed debugger build in that sample was later found to hang if a client closed input and stopped reading a large wait response; it is **not** the corrected shipping build. Its median was 130.6 s (110.8–145.5), 44 calls (42–53), 573,474 total input (477,186–608,912), 547,584 cached input (434,944–582,912), 26,000 uncached input (25,890–42,242), and 1,781 output (1,666–2,103). Old core was 155.3 s (139.8–207.5), 42 calls (39–43), 849,666 total input (734,457–937,427), 799,744 cached (683,648–910,080), 41,695 uncached (27,347–50,809), and 1,715 output (1,557–1,806). Native was 50.4 s (47.0–53.7), 6 calls (5–7), 117,092 total input (105,844–146,652), 107,008 cached (93,568–131,712), 14,940 uncached (10,084–17,895), and 684 output (646–741). Five usage records were available per cell. The changed build's median calls and output tokens **increased**, despite lower median elapsed time and total input. One old FFSAKE run and three native runs recorded tool/command errors; all still passed the independent verifier. The changed arm recorded none. Both FFSAKE arms used FFSAKE for all five runs and no native shell calls; the native arm used `pdb` and native editing/commands.

All ten FFSAKE debugger traces launched twice; the second launch followed the source edit. The changed code must be loaded in a new process to debug it, so this is not evidence that the same live session could verify the changed program. Adapter startup is not separately timed in these agent traces; the preceding model-free probe is a distinct measurement. The output-reader bug was reproduced against the measured binary, then corrected with bounded shutdown and tested with both a stalled reader and normal cleanup. The editing measurements above use the earlier build: the subsequent change affects the pending-debug-wait shutdown path, which the editing profile does not expose. A fresh debugger comparison was started against the corrected binary; its first attempt received a provider-capacity error before any tool call and is retained as an invalid attempt, not counted as a successful run. [Sanitized earlier debugger results](agent-benchmark-runs/20261102-performance/comparison-debug-old.json) · [changed arm](agent-benchmark-runs/20261102-performance/comparison-debug-changed.json) · [native arm](agent-benchmark-runs/20261102-performance/comparison-debug-baseline.json) · [capacity attempt](agent-benchmark-runs/20261102-performance/corrected-debug-capacity-attempt.json).

The fresh, counterbalanced corrected-build debugger comparison used the same task, verifier and model settings, with five completed runs per arm. Its binary and inputs were frozen separately after the shutdown fix. One earlier attempt in this set failed before any tool call because the provider reported that the selected model was at capacity; it was retained separately and retried without changing models. The five-run cells below exclude that explicitly invalid attempt. All 15 completed attempts passed the external verifier and workflow evidence checks; none timed out.

| Corrected debugger comparison | Correct | Seconds | Calls | Total input | Cached input | Uncached input | Output |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Native | 5/5 | 55.6 (52.8–63.9) | 6 (6–7) | 141,487 (125,213–165,940) | 125,440 (91,520–148,096) | 24,975 (16,047–33,693) | 707 (654–756) |
| Old core | 5/5 | 188.9 (156.9–237.4) | 41 (39–87) | 888,135 (795,579–1,086,398) | 834,816 (763,904–1,028,864) | 49,990 (26,366–57,534) | 1,748 (1,528–2,259) |
| Corrected core | 5/5 | 134.3 (102.1–215.1) | 44 (42–45) | 550,021 (508,596–609,126) | 520,448 (484,224–583,040) | 25,703 (24,372–49,925) | 1,723 (1,671–1,853) |

Both FFSAKE arms used FFSAKE in all five runs, with no native shell or native file-edit call; native used its own debugger and editing tools. One native run recorded a tool/command error; one old-build run recorded three. That old run relaunched after the adapter reported a timeout and the first session had cleaned up, then passed. The corrected arm recorded no tool errors. Both FFSAKE arms typically launched again after the fix to load the edited program. Old runs issued 14–15 polls except the recovered run, which issued 49; corrected runs issued 16–19 waits and zero or one poll. The new wait therefore did not reduce median model-issued calls: 41 became 44. The corrected build had lower median elapsed time and input in this sample, but ranges overlap and native remained substantially lower in both; one fixture and five pairs do not establish a general advantage. Output-token and call counts are shown separately rather than inferred from input. The full agent traces do not isolate model inference, native command duration or individual adapter startup. Median sums of debugger MCP tool-response wire bytes increased from 30,984 to 34,697, and cumulative measured tool round trips from 264 to 338 ms; sums may overlap for concurrent requests. Internal dispatcher timing does not cover the new asynchronous wait path. [Corrected native](agent-benchmark-runs/20261102-performance/corrected-debug-baseline.json) · [old](agent-benchmark-runs/20261102-performance/corrected-debug-old.json) · [corrected](agent-benchmark-runs/20261102-performance/corrected-debug-changed.json) · [frozen inputs](agent-benchmark-runs/20261102-performance/corrected-debug-freeze.json).

### Debugger auto-wait candidate (2026-11-02)

The corrected traces above still issued 41–44 model calls. In the changed arm, 16–19 of those were explicit waits, and nearly every control or inspection action was followed by a separate wait. A wait until `stopped` also returned as soon as the continue response arrived, so a single stop commonly required two waits.

The next candidate makes control and inspection calls return their completed result in the same response after a bounded wait of up to one second. `continue`, step and pause wait for the next stop; `terminate` waits for cleanup. A wait until `stopped` now retains a completed continue result and returns it with the stop instead of ending early. `auto_wait=false` preserves explicit queue/poll/wait behavior, and the explicit asynchronous wait path remains available. The server still performs waits outside the resident lock, so ordinary editing and termination stay responsive; authorization is rechecked when the result is delivered, and cancellation, stale handles, deadlines and owned-process cleanup are unchanged.

Model-free checks passed: the 15-case protocol/authorization/cleanup smoke, the concurrent-read and stalled-reader wait smoke, and a new auto-wait sequence. That sequence used eight MCP calls (launch, access grant, breakpoints, continue, stack, scopes, variables, terminate), zero explicit polls or waits, observed the expected values and cleaned up the adapter. This is a call-count and mechanism result, not an agent-time measurement. The previous corrected traces used 41–44 calls for the same workflow.

Codex paid validation was stopped by provider credits, not by a failed candidate. The first Codex pilot run failed before any tool call with the workspace’s “out of credits” provider error and was retained as invalid. Claude Code `2.1.317` was then used for a five-repetition, counterbalanced old/changed/native comparison on two debugger fixtures: the payroll fixture above and a second shipping-threshold fixture with a different bug. Prompt, verifier, model alias, host and initial worktree state matched within each pair; order rotated. The changed binary was `dc6844a94044b0e8c683e773e8fc221195f3f824022a341e603069339fb0f9a9` (auto-wait plus the default 1000 ms explicit-wait timeout, before the result-readiness fix below); the old binary was `a0211820298c3cee1d3e084bf78d341fbde0aa61bba8b6c9ada7249b2add9ab2` from `4d717ec`.

| Fixture / mode | Correct | Seconds | Calls | Total input | Reported cost/run |
| --- | ---: | ---: | ---: | ---: | ---: |
| Payroll native | 5/5 | 17.6 (15.3–18.3) | 7 (6–7) | 181,920 | $0.0843 |
| Payroll old | 5/5 | 44.5 (39.6–75.3) | 32 (25–38) | 899,808 | $0.2269 |
| Payroll changed | 5/5 | 27.6 (26.9–43.6) | 19 (18–21) | 477,462 | $0.1577 |
| Shipping native | 5/5 | 13.1 (12.4–15.3) | 7 (6–7) | 155,725 | $0.0789 |
| Shipping old | 5/5 | 29.6 (28.0–44.8) | 24 (22–31) | 628,244 | $0.1738 |
| Shipping changed | 4/5 | 29.5 (20.4–41.1) | 18 (12–25) | 477,492 | $0.1554 |

Changed versus old: payroll elapsed −38.0%, calls −40.6%, total input −46.9%; shipping elapsed −0.3%, calls −25.0%, total input −24.0%; the two medians combined are −23.0% elapsed. Payroll improved substantially, but shipping did not, ranges overlap, and native remained faster in both fixtures. The 30% elapsed-time target is therefore **not met on both fixtures**, and these Claude results are not comparable to the earlier Codex target. The corrected runtime-value verification increased payroll native to 5/5 and reduced shipping changed to 4/5; the failed shipping run produced a correct artifact but did not expose the expected runtime value. All other cells passed both the artifact and workflow checks. Costs are provider-reported medians, not invoices, and include cache reads in total input.

One changed shipping run in the first comparison exposed a result-readiness race: an auto-wait for a non-control operation could return before that operation’s result was available, leaving it queued; the next action then reported a previous-result error and required an extra `poll`. The server now supports an internal `WaitUntil::Result` and uses it for non-control, non-terminate auto-waits, so launch/attach, breakpoints, stack, scopes and variables wait for their own result rather than merely for an idle state. The fix build is `24e352305bff26cb8dca397e762c3c733891db55dddd1d68e56818be0f2505cf`.

The fix build was re-benchmarked with five attempts per cell. One changed payroll attempt called a nonexistent `mcp__ffsake__ffsake_tool_placeholder_unused` tool; the call failed, the run recovered and produced a correct artifact, but the harness marked the attempt `comparison_valid=false` for a tool-profile violation. It is excluded only from the valid timing median, not from correctness or all-attempt accounting. Four valid changed payroll runs remain; every other cell has five valid runs. The invalid attempt took 25.7 s; counting it, changed payroll was 5/5 correct at a 31.0 s median, 19 calls, 473,358 total input and $0.154889. The valid-only row below remains 32.6 s to keep the timing basis comparable. Public per-run indexes are [prior-fix evidence](agent-benchmark-runs/20261104-inspection/prior-final.json) and [result-fix evidence](agent-benchmark-runs/20261104-inspection/prior-resultfix.json).

| Fixture / mode | Valid | Correct | Seconds | Calls | Total input | Reported cost/run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Payroll native | 5/5 | 5/5 | 16.2 (13.9–19.2) | 7 (6–9) | 156,865 | $0.0826 |
| Payroll old | 5/5 | 5/5 | 41.1 (38.7–60.1) | 28 (25–35) | 812,938 | $0.2027 |
| Payroll changed | 4/5 | 5/5 | 32.6 (26.8–38.4) | 18.5 (17–21) | 477,644 | $0.1587 |
| Shipping native | 5/5 | 5/5 | 14.0 (12.8–16.2) | 7 (6–7) | 156,012 | $0.0800 |
| Shipping old | 5/5 | 3/5 | 29.3 (27.6–41.0) | 23 (16–32) | 567,424 | $0.1647 |
| Shipping changed | 5/5 | 5/5 | 28.4 (22.2–31.2) | 22 (16–22) | 591,767 | $0.1777 |

Changed versus old on the fix build: payroll elapsed −20.6%, calls −33.9%, total input −41.2%; shipping elapsed −2.8%, calls −4.3%, total input +4.3%; the two medians combined are −13.2% elapsed. The fix removed the earlier extra-poll failure, but it does not reproduce the first comparison’s −23.0% combined reduction, and the 30% elapsed-time target remains **not met on both fixtures**. Native remained faster in both. Ranges overlap, the payroll changed cell has four valid runs, and these Claude results are not comparable to the earlier Codex target. The result-readiness fix and second fixture were pushed at `f7338ac`; exact-head CI run `41661357846` passed. Private reports and traces remain outside Git; no raw transcript or personal path is published here.

### Combined stop inspection (2026-11-04)

The result-fix traces still paid for separate inspection round trips after almost every stop. Across five changed payroll traces, the model requested stack, scopes and variables 8, 6 and 6 times in total; in shipping it requested 6, 4 and 4. The candidate makes a successful control action return a bounded inspection of the next stop in the same response.

`continue`, `next`, `step_in`, `step_out` and `pause` wait up to 900 ms for the next stop, then inspect it. `inspect` returns the same context for the current stop without resuming. Defaults are 5 frames, 4 scopes and 8 variables per scope, with at most 64 variable entries, 512 characters per value and a 32 KiB automatic-inspection budget. Explicit limits are 1–10 frames, 1–8 scopes, 1–20 variables and a scope×variable product ≤64. Frames carry handles; scopes and variables use existing handles; every truncation is explicit (`frames_truncated`, `scopes_truncated`, `variables_truncated`, `value_truncated`, `scopes_error`). If no stop arrives, the control result still succeeds and reports `wait_timed_out`; inspection failure does not fail the control. `inspect=false` opts out per action, and `auto_wait=false` implies `inspect=false`. No evaluation, raw DAP, automatic resume or extra launch was added. Handles still expire on resume, authorization is rechecked when each result is delivered, and stale inspection clears handles and asks for fresh stopped-state handles.

Model-free checks passed: the 155-test Rust suite, `cargo clippy --all-targets -- -D warnings`, `cargo fmt`, the 15-case protocol/authorization/cleanup smoke, the wait/concurrency smoke, and the debugpy compatibility smoke. The auto-wait sequence now uses five MCP calls (launch, access grant, combined breakpoints, continue with inspection, terminate), zero explicit polls or waits, observes the expected stop variables, and cleans up the adapter. It also checks the default limits and an explicit `inspect` request with 1 frame, 1 scope and 2 variables. Opt-out and explicit-async paths remain covered. This is a mechanism and call-count result, not an agent-time claim. Real C#/netcoredbg remains CI-only because no adapter is installed locally.

One candidate was tested; no second candidate was opened. Claude Code `2.1.317` used the `sonnet` host alias on macOS arm64. A three-run pilot was followed by five planned attempts per fixture per mode in alternating order, plus one supplemental old payroll retry, for 31 measured attempts. With the pilot, 34 of the 40-run cap were used. Native had no FFSAKE server; old and changed differed only in the frozen FFSAKE binary. The changed release binary was `6a54214630a41d6bd65450f970b061ed39f982b62c42f13a6a81818e8a7fc34a`; rebuilding commit `936a70a` produced the identical hash. The harness recorded `a40b04d` because the measured tree was uncommitted at run time; `binary_matches_source_verified=false` in the raw report reflects that timing, not a different binary. The old binary was `a0211820298c3cee1d3e084bf78d341fbde0aa61bba8b6c9ada7249b2add9ab2` from `4d717ec`. Prompt, verifier, model alias, host, initial worktree state and allowances matched within each pair.

The old payroll cell required a sixth attempt because attempt 5 failed at the provider with `provider_api_error` before producing a correct artifact; the supplemental run completed five valid old attempts. All six old attempts are retained, the invalid one is excluded only from the valid timing median, and the five valid attempts are pooled. The old payroll median over all six attempts was 39.6 s, which would still be only a 28.0% reduction, below the target.

| Fixture / mode | Attempts (valid) | Correct | Seconds | Calls | Total input (cached / uncached) | Output | Cost/run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Payroll native | 5 (5) | 5/5 | 15.7 (12.7–18.2) | 8 (6–8) | 184,820 (170,851 / 14) | 1,279 | $0.0860 |
| Payroll old | 6 (5) | 3/6 (3/5 valid) | 38.0 (32.7–47.8) | 25 (19–27) | 671,735 (648,656 / 44) | 2,863 | $0.1868 |
| Payroll changed | 5 (5) | 4/5 | 28.5 (26.4–39.9) | 17 (15–20) | 460,672 (435,872 / 32) | 2,176 | $0.1645 |
| Shipping native | 5 (5) | 5/5 | 13.2 (12.5–17.3) | 7 (7–8) | 156,057 (142,509 / 12) | 1,087 | $0.0847 |
| Shipping old | 5 (5) | 5/5 | 32.1 (31.4–40.2) | 23 (22–28) | 697,456 (675,145 / 46) | 2,524 | $0.1819 |
| Shipping changed | 5 (5) | 5/5 | 21.8 (16.9–25.0) | 14 (13–15) | 349,116 (326,504 / 26) | 1,791 | $0.1414 |

Changed versus old on valid attempts: payroll elapsed −25.0%, calls −32.0%, total input −31.4%, cached input −32.8%, uncached input −27.3%, output −24.0%, reported cost −11.9%; shipping elapsed −32.1%, calls −39.1%, total input −49.9%, cached input −51.6%, uncached input −43.5%, output −29.0%, reported cost −22.3%. Combining both fixtures, median elapsed fell from 34.0 s to 25.7 s (−24.4%). Shipping met the 30% target; payroll did not, under either the valid-only median (−25.0%) or the all-attempt sensitivity (−28.0%). Native remained faster in both fixtures. Correctness: payroll changed was 4/5, above old’s 3/5 valid but below native 5/5; shipping was 5/5 in every mode. The payroll changed failure and the two valid old workflow failures produced correct artifacts but lacked verifiable runtime state; the separate old provider failure did not produce a correct artifact. **Verdict: the 30% target is not met on both fixtures.**

The shipping evidence path initially defaulted to pass because `debug_shipping` had not run workflow evidence. Enabling it exposed a fixture-marker mismatch (`subtotal` was requested, but the runtime variable is `amount`). A later audit found the state checks still searched commands and source text, so neither correction was retained as a keyword test. The verifier now requires actual runtime values: FFSAKE evidence is parsed from structured inspection/scopes/variables responses, and native evidence is parsed from debugger response values rather than command or source text. Re-evaluating the same 15 shipping runs still produced 5/5 correct in every mode; no model run was repeated and no check was loosened. The raw shipping report retains its earlier evidence, while the [verified per-run index](agent-benchmark-runs/20261104-inspection/inspection.json) records the runtime-value evidence.

The same verifier change corrected the two earlier public indexes without rerunning models: in the prior-fix index, payroll native moved from 4/5 to 5/5 and shipping changed from 5/5 to 4/5; in the result-fix index, payroll native moved from 3/5 to 5/5 and shipping old from 5/5 to 3/5. The newly passing runs contained the expected value in native debugger output. The newly failing shipping runs performed debug actions but did not record a parsed runtime value. Timings, token totals and costs are unchanged.

### Payroll failure trace audit (2026-11-04)

The latest payroll attempt overall was old attempt 5, but it failed at the provider and was replaced by supplemental old attempt 6; it is not a runtime-inspection data point. The latest run that failed the runtime-evidence check was changed attempt 4. Its artifact was correct, but workflow evidence failed. Runtime inspection itself worked: the first auto-inspection stopped at line 17 inside `total_pay` and returned `hours=45`, `rate=20`, `base=900` and `adjustments=[]`. That stop is before the mutation on the next line. The agent then retried `continue` with a thread; that call reached termination because the only remaining employee did not enter the `hours > 40` branch. The agent did not step over line 17 or set a breakpoint after it, so it never observed `adjustments=[50.0]`. It inferred the shared-default-list bug from source and applied the correct fix. This is an agent workflow limitation, not a runtime-inspection failure or a verifier miss.

The earlier failed old FFSAKE payroll runs have the same limitation. Attempt 1 issued no stack, scopes or variables request. Attempt 4 inspected only the pre-mutation stop (`adjustments=[]`, `hours=45`, `base=900`, `rate=20`) and then continued to termination. Attempt 5 was a provider failure and is excluded from valid timing; the supplemental old attempt 6 passed workflow evidence. No retained failed payroll trace contains a later stop with the expected `[50.0]` value. No product defect was found, so no runtime code was changed. Use the [real-project testing checklist](REAL_PROJECT_TESTING.md) to capture this kind of skipped post-mutation inspection during actual use.

### Next interaction-cost candidate (2026-11-04)

The retained changed payroll traces contain 85 calls across five runs. Four calls, one in four of the five runs, were rejected with `continue requires thread after configuration`; each required an immediate retry. The changed shipping arm had no such error. The old binary's ten queued-result errors are frozen and are not candidates for the changed build. The largest remaining product-level avoidable cost is therefore the thread omission after a stop, not inspection or waiting.

`continue` now infers the stopped thread when `thread` is omitted after configuration; an explicit thread still takes precedence, and a stop without a thread ID still rejects the request. This uses the same stop-thread inference already used by `inspect` and adds no execution, evaluation, resume or adapter path. Model-free checks pass: 156 Rust tests, Clippy with warnings denied, formatting, the 16-case DAP protocol smoke (including an end-to-end continue-without-thread case), the wait smoke and MCP smoke. No paid run has been taken for this candidate, so no timing claim is made.

Proposed measurement budget: at most 6 paid attempts, all changed payroll on the new binary—five repetitions plus one retry for a provider or tool failure. Acceptance is protocol adoption and error removal, not a timing target: no `continue requires thread after configuration` error, median calls below the retained changed-payroll median of 17, and individual artifact/workflow inspection. A paired timing comparison would require 10 attempts (five old and five new) and four more than the current 40-run cap; do not run it without a new budget decision.

Only 6 of the 40 paid runs remain. The changed-build thread-inference candidate is ready for a bounded adoption check, but no paid run will start without approval of the budget above. Public indexes contain per-run metrics and content-free event summaries only; raw transcripts, tool arguments, outputs, paths and session IDs remain in private local storage. CI run 41820074918 passed at `71973a0`.

Reproduce using the recorded source revisions, a private output directory, Codex CLI and an installed debugpy environment. Building with `benchmark-timing` is for measurement; normal binaries omit it. The `--baseline-binary` run uses the current binary's editing profile for the two editing tasks and core for debugging; both binary paths must identify frozen builds. Raw reports and timing paths may contain sensitive session metadata and must stay private.

```sh
python3 scripts/rc_benchmark.py --binary /private/path/to/old-instrumented-mcp \
  --output /private/path/to/diagnostic.json --agents codex --tasks cross \
  --repetitions 5 --diagnostic-matrix --instrument-dir /private/path/to/diagnostic-timings
python3 scripts/rc_benchmark.py --binary /private/path/to/changed-instrumented-mcp \
  --baseline-binary /private/path/to/old-instrumented-mcp \
  --output /private/path/to/comparison.json --agents codex --tasks cross,maintenance,debug \
  --repetitions 5 --debug-python /absolute/path/to/debugpy-venv/bin/python \
  --instrument-dir /private/path/to/comparison-timings
python3 scripts/rc_benchmark.py --binary /private/path/to/changed-mcp \
  --baseline-binary /private/path/to/old-mcp \
  --output /private/path/to/claude-debug-comparison.json --agents claude \
  --tasks debug,debug_shipping --repetitions 5 \
  --debug-python /absolute/path/to/debugpy-venv/bin/python \
  --instrument-dir /private/path/to/claude-comparison-timings
python3 scripts/debug_poll_probe.py /private/path/to/corrected-instrumented-mcp \
  --baseline-binary /private/path/to/old-instrumented-mcp \
  --python /absolute/path/to/debugpy-venv/bin/python \
  --output /private/path/to/debug-probe.json --repetitions 5
```
