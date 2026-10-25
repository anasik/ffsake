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
