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

## OpenCode read-redirect plugin

OpenCode v2 supports local plugins that replace a built-in tool executor.
`scripts/opencode_read_redirect_plugin.js` wraps native `read`, calls
`ffsake read <workspace> <path>` and returns the FFSAKE text in the host's
declared `file` output shape. The same plugin rejects file-reading shell
commands in `execute.before`. Native `Edit`/`Write` and native search tools stay
available.

The plugin id is derived from the workspace path because OpenCode caches plugin
modules by id; a shared id would keep the first workspace's root closure. The
setup script and benchmark harness render the plugin with an absolute binary
path and workspace id. `scripts/opencode_read_redirect_smoke.py` loads the
rendered plugin in Node with a stub host and verifies read redirection, ranged
reads, shell blocking, ordinary-shell allowance, outside-workspace denial and
zero model calls.


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

`continue` now infers the stopped thread when `thread` is omitted after configuration; an explicit thread still takes precedence, and a stop without a thread ID still rejects the request. This uses the same stop-thread inference already used by `inspect` and adds no execution, evaluation, resume or adapter path. Model-free checks pass: 156 Rust tests, Clippy with warnings denied, formatting, the 16-case DAP protocol smoke (including an end-to-end continue-without-thread case), the wait smoke and MCP smoke.

A first bounded adoption check consumed five paid attempts but is invalid as feature evidence. The private runner resolved the debugpy virtual-environment symlink to the system Python before writing the adapter policy. Every launch then failed with `ModuleNotFoundError: No module named 'debugpy'`; no debug session reached a breakpoint or a `continue`. The five attempts were still model calls, so they are retained and marked `benchmark_setup_error` in the [public index](agent-benchmark-runs/20261104-inspection/continue-thread-setup-failure.json). The sixth permitted attempt was not used. These calls and timings must not be compared with the retained runs.

| Attempt | Correct | Artifact | Calls | Total input (uncached) | Output | Seconds | Cost |
| ---: | :---: | :---: | ---: | ---: | ---: | ---: | ---: |
| 1 | No | No | 7 | 142,142 (14) | 1,106 | 12.6 | $0.0957 |
| 2 | No | No | 7 | 142,124 (14) | 1,054 | 14.3 | $0.0886 |
| 3 | No | No | 6 | 111,989 (10) | 930 | 11.3 | $0.0824 |
| 4 | No | No | 8 | 171,972 (14) | 1,335 | 15.1 | $0.0940 |
| 5 | No | Yes | 10 | 204,925 (16) | 1,496 | 18.5 | $0.1024 |
| Median | 0/5 | 1/5 | 7 | 142,142 (14) | 1,106 | 14.3 | $0.0940 |

There were zero `continue requires thread after configuration` errors, but that is not evidence: no attempt reached a post-configuration stop or issued a continue. Attempt 4 attempted a breakpoint before launch and received `missing field 'session'`; attempt 5 edited without a working debugger. No attempt produced runtime inspection evidence. The lower call counts reflect early abandonment after adapter failure, not reduced interaction overhead. Total reported cost was $0.4631 for 773,152 total input and 5,921 output tokens.

The runner now converts the adapter to an absolute path without resolving the virtualenv symlink. A regression test covers that distinction. A model-free preflight creates the exact generated debug policy, launches that adapter, stops at line 18, inspects `adjustments=[50.0]`, and verifies cleanup before any paid attempt. The batch aborts immediately on a launch failure.

After the preflight passed, five additional paid attempts ran. All five passed artifact and workflow checks and each observed `adjustments=[50.0]` at runtime. Every attempt completed a stop, inspection, edit, program run, and debugger cleanup.

| Attempt | Correct | Calls | Total input (uncached) | Output | Seconds | Cost | Runtime evidence |
| ---: | :---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | Yes | 14 | 347,021 (24) | 1,755 | 23.7 | $0.1428 | `[50.0]` |
| 2 | Yes | 14 | 312,646 (24) | 1,883 | 24.2 | $0.1345 | `[50.0]` |
| 3 | Yes | 13 | 283,508 (20) | 1,748 | 23.0 | $0.1298 | `[50.0]` |
| 4 | Yes | 14 | 345,888 (26) | 1,726 | 23.9 | $0.1368 | `[50.0]` |
| 5 | Yes | 18 | 498,410 (34) | 2,506 | 30.7 | $0.1715 | `[50.0]` |
| Median | 5/5 | 14 | 345,888 (24) | 1,755 | 23.9 | $0.1368 | |

There were zero `continue requires thread after configuration` errors. However, the inference was not exercised in these five traces: the only continue without a thread was the initial configuration call, and every post-stop continue carried thread 1. The feature therefore cannot explain the call or time difference. Attempt 5 had one queued-result error after `auto_wait=false` and two Bash read-block rejections; attempts 1–4 each had one Bash read-block rejection. No debugger action failed in attempts 1–4.

The corrected batch used the five additional attempts; the prior sixth remains unused as a reserve. Descriptively, the median was 14 calls and 23.9 s versus the retained changed-payroll median of 17 calls and 28.5 s, with 345,888 versus 460,672 total input. The model host also changed from `2.1.317` to `2.1.318`, the runs are not counterbalanced against a contemporaneous old arm, and the new inference was not used. This is not evidence that thread inference reduced interaction overhead. The [corrected index](agent-benchmark-runs/20261104-inspection/continue-thread-corrected.json) preserves per-run metrics; the [failed setup batch](agent-benchmark-runs/20261104-inspection/continue-thread-setup-failure.json) remains recorded separately. Public indexes contain per-run metrics and content-free event summaries only; raw transcripts, tool arguments, outputs, paths and session IDs remain in private local storage.

### Workspace-context candidate (2026-11-04)

All five corrected changed-payroll runs called `ffsake_workspace_info`, then `ffsake_list`, then `ffsake_debug` with `action=list`, then read `payroll.py`. The first call now returns up to 100 top-level entries with an explicit truncation flag, the debug configuration summary, and optional reads of up to 32 known files/ranges in the same response. The tool description and server guidance tell agents to use that context before separate list, debug-list or read calls.

This does not remove any tool or permission: `ffsake_list`, `ffsake_debug` and `ffsake_read_files` remain available for subdirectories, full debug control and larger reads. Model-free checks pass: 157 Rust tests, Clippy with warnings denied, formatting, MCP/worktree/debug protocol smokes, and a direct workspace-info read returning the exact file text without adapter arguments. It could remove three model turns per run if adopted; that adoption is not measured because no paid budget is approved. The next candidate is a combined debug start that applies initial breakpoints and reaches the first stop in the launch call.

### Combined debug start (2026-11-04)

The corrected traces still used three turns to reach the first stop: launch, breakpoints, and continue. `ffsake_debug_launch` now accepts up to 16 `{path, lines}` initial breakpoints and `resume:true`; the call applies those breakpoints after the adapter reaches configuring, sends the initial continue, waits for the first result, and returns the stopped inspection with the launch and session fields. Existing launch behavior is unchanged when the optional fields are absent; paths still go through normal authorization, one session is enforced, and terminate/cleanup remains the caller's responsibility.

Model-free checks pass: 157 Rust tests, Clippy, formatting, MCP/worktree smokes, the 17-case DAP protocol smoke, and a real debugpy preflight that launches the exact generated policy with initial breakpoints, reaches line 18, observes `adjustments=[50.0]`, and cleans up. The combined path removes two model turns per debug session if adopted; no paid run has been taken for this candidate because no budget is approved. The next candidate is a compact debug response that omits repeated session metadata while preserving every action and inspection value.

### Compact debug response metadata (2026-11-04)

Every debug snapshot repeated the adapter's full boolean capability map, including stopped and terminated responses after the launch had already established it. Snapshots now include `capabilities` while starting/configuring or failed; stopped, running and terminated snapshots omit it. `ffsake_debug` action `list` still reports the current session's capabilities, and a combined launch inserts the map into its returned stopped snapshot so the first call remains self-contained. No debug action or capability is removed.

The 17-case DAP protocol smoke checks that a combined start returns capabilities and a cleaned response omits them. This removes about 680 bytes per stopped/terminated debug response in the observed adapter; exact token impact is not measured without a paid run. This candidate follows workspace context and combined start, which are both model-free validated but unmeasured for adoption.

Projection from the corrected traces: using workspace context/read removes three turns, and combined start replaces launch+breakpoints+first continue with one call, a net two-turn removal. The five corrected runs would move from 14, 14, 13, 14 and 18 calls to 9, 9, 8, 9 and 13, a median of 9 versus 14 (35.7% fewer). Output payload would also shed the omitted capabilities. This is a trace-derived call projection, not a measured time or token result; the 30% target remains undemonstrated until a paid budget is approved.

### Compact automatic inspection (2026-11-04)

The corrected traces returned four stack frames and all scopes on every automatic stop; the model used the selected frame and locals. Automatic inspection now defaults to two frames and the first local scope. Explicit `inspect`, `stack` and `scopes` remain available for five frames, ten frames and broader scope/variable inspection.

Model-free response measurements using the exact debugpy policy dropped the combined launch response from 3,369 to 2,592 bytes and the second-stop response from 2,418 to 1,634 bytes, saving about 1,561 bytes per observed run across both refinements. The 17-case DAP smoke, wait smoke, exact-adapter preflight (`adjustments=[50.0]`) and all prior smokes pass. No paid run has measured this candidate; no time or token claim is made.

### Efficiency A/B measurement (2026-11-05)

A five-repetition, counterbalanced old-versus-changed comparison was run on both debugger fixtures with current Claude Code. The frozen old binary was `e57a33cc28ac414cf142437bbce53005280c2ae665700e4e1a343f3eec2af814` from `6899b6a`; the changed binary was `454a33945bb9b6869458b09d63f6a54e35caf112a3c8777adc4dbfdc4212efc0` from `61e96d8`. Thirty paid attempts were made (native, old and changed for each fixture). The old shipping cell had one invalid tool-profile attempt; its correctness is retained and its timing excluded. Total reported cost was $3.4137.

| Fixture / mode | Correct | Valid | Calls | Seconds | Total input | Output | Cost/run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Payroll native | 5/5 | 5/5 | 6 | 13.8 | 156,521 | 1,108 | $0.0802 |
| Payroll old | 4/5 | 5/5 | 13 | 25.6 | 342,105 | 1,922 | $0.1399 |
| Payroll changed | 5/5 | 5/5 | 11 | 20.5 | 334,274 | 1,563 | $0.1240 |
| Shipping native | 5/5 | 5/5 | 7 | 13.7 | 156,012 | 1,068 | $0.0791 |
| Shipping old | 5/5 | 4/5 | 12.5 | 18.2 | 305,127 | 1,494 | $0.1256 |
| Shipping changed | 5/5 | 5/5 | 10 | 18.1 | 274,671 | 1,193 | $0.1139 |

Changed versus old: payroll elapsed **−19.9%**, input **−2.3%**, calls **−15.4%**; shipping elapsed **−0.8%**, input **−10.0%**, calls **−20.0%**. Changed correctness was 5/5 on both fixtures, so there was no correctness regression. The 30% elapsed and input target is **not met on either fixture**. Native remained faster on both: payroll 13.8 s / 156,521 input and shipping 13.7 s / 156,012.

The 14→9 call projection was not realised: changed payroll median was 11 calls and shipping 10. Traces still contain extra searches, duplicate launches, and explicit breakpoint/continue turns in several repetitions. The verifier now recognizes a combined launch with `resume` and returned inspection as launch/resume/automatic inspection; that correction raises payroll changed from 4/5 to 5/5 and shipping changed from 2/5 to 5/5 without a rerun. The [sanitized A/B index](agent-benchmark-runs/20261105-efficiency/ab.json) preserves per-run metrics.

### Single-line breakpoint input (2026-11-05)

The A/B traces exposed a concrete retry: payroll changed attempt 1 sent `lines:17` and shipping changed attempt 4 sent `lines:20`; both received `invalid type: integer, expected a sequence` and retried with an array. `breakpoints.lines` now accepts either one line number or an array, preserving the same line bounds and path authorization. The protocol smoke exercises the scalar form and the Rust parser test covers both forms. This removes one avoidable turn when an agent uses the scalar shape; it has not been paid-measured.

### Debug-source context rejected (2026-11-05)

A second five-repetition A/B added automatic debug-program source to `ffsake_workspace_info`. It reduced input descriptively but regressed correctness and did not improve elapsed time: payroll changed was 3/5 correct with median 25.7 s and 10 calls versus old 23.1 s and 14 calls; shipping was 4/5 correct with 19.0 s and 8 calls versus old 17.6 s and 13 calls. Artifacts were 5/5 on both, but the agents chose pre-mutation breakpoints and failed to observe the required runtime value. The candidate was reverted at `da5a07c`; the [sanitized index](agent-benchmark-runs/20261105-efficiency/ab-debug-source.json) preserves the negative result. No target progress is claimed.

### One-shot debug probe (2026-11-05)

The changed A/B traces repeatedly launched a debug session, inspected a stop, then issued a separate terminate. A dedicated `ffsake_debug_probe` tool now applies initial breakpoints, reaches the first stop, steps over up to two lines, stopping early when inspected values change, and returns line-numbered source plus the resulting inspections, and cleans up the owned launch in one call. `ffsake_debug_launch` also accepts `terminate_after_stop:true` for callers using the persistent API. The normal multi-stop session path remains available.

The 18-case DAP protocol smoke tests combined start and one-shot probe, including no extra explicit terminate and cleanup. The exact debugpy preflight uses `ffsake_debug_probe` at the pre-mutation line, verifies the stepped inspection contains `adjustments=[50.0]` and `cleaned=true`, and the benchmark guidance names the probe for one-shot diagnosis. No paid run has measured this candidate; no time or token claim is made.

### One-shot probe A/B (2026-11-05)

A 20-attempt native-versus-probe comparison ran on both fixtures after `ffsake_debug_probe` began applying initial breakpoints, stepping once, returning both inspections and cleaning up in one call. Changed correctness was 5/5 on both fixtures. Payroll changed measured **16.9 s / 8 calls / 206,079 input** versus native **16.5 s / 6 calls / 155,556**; shipping changed measured **13.3 s / 7 calls / 199,355** versus native **14.2 s / 7 calls / 155,817**.

Against the retained frozen-old medians from the earlier A/B, payroll changed is **−34.0% elapsed, −39.8% input, −38.5% calls** and shipping is **−27.0% elapsed, −34.7% input, −44.0% calls**. Thus payroll meets the 30% target on both elapsed and input, and shipping meets it on input but is 3 points short on elapsed; shipping changed is nevertheless faster than native in this sample. The old baseline is retained rather than rerun because the old binary predates the probe tool and cannot be preflighted under the current catalog. These comparisons are descriptive, not a contemporaneous counterbalanced old arm.

The exact-adapter preflight uses the pre-mutation line, verifies the stepped `adjustments=[50.0]` inspection and cleanup. The [sanitized probe index](agent-benchmark-runs/20261105-efficiency/probe-step.json) preserves per-run metrics. Near-native elapsed and call counts are demonstrated; input remains about 28–32% above native.

### Near-native probe result (2026-11-05)

A 20-attempt native-versus-probe comparison used line-numbered debug-program source and the stepped one-shot probe. All 10 changed attempts were correct. Payroll changed measured **16.9 s / 7 calls / 174,341 input** versus native **17.2 s / 7 calls / 181,501**; shipping changed measured **12.1 s / 6 calls / 173,905** versus native **13.6 s / 7 calls / 156,389**.

Against the retained frozen-old medians, payroll is **−33.8% elapsed and −49.0% input**; shipping is **−33.5% elapsed and −43.0% input**. Both fixtures therefore exceed the 30% elapsed and input targets, with 5/5 changed correctness. Payroll is slightly faster and lower-input than native; shipping is faster with more calls than native and 11% more input. The old baseline is retained from the prior A/B because the old binary predates the probe catalog and cannot be preflighted under the current tool set; the native arm is fresh and counterbalanced with changed.

The [sanitized probe index](agent-benchmark-runs/20261105-efficiency/probe-lines.json) preserves per-run metrics. This closes the performance target while retaining a 5/5 correctness result.

### Unfamiliar debugger generalization (2026-11-05)

Candidate `3e9a9c8cc89d65bb090da590caea4bea1b9aea504e9d293c0ef5173b0889fcca` from `2b78219` was frozen before scoring. Three previously unused fixtures were added: `debug_inventory` (larger multi-module boundary bug), `debug_intervals` (adjacent interval boundary), and `debug_cache` (missing-key exception). Each has a program/test verifier and a runtime-marker workflow check; prompts contain neither fixes nor expected answers. Model-free setup preflights passed before any scored attempt.

Thirty paid attempts ran, five native and five FFSAKE per fixture, counterbalanced. All were valid and none timed out. Artifact correctness was **5/5 in both arms on every fixture**. Harness completion, which also requires observed debugger runtime evidence, was mixed.

| Fixture / arm | Artifact | Harness correct | Calls | Seconds | Total input | Cached / uncached | Output | Cost/run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Inventory native | 5/5 | 2/5 | 6 | 13.9 | 154,935 | 141,494 / 12 | 1,053 | $0.0834 |
| Inventory FFSAKE | 5/5 | 0/5 | 9 | 17.5 | 232,570 | 214,984 / 18 | 1,240 | $0.1044 |
| Intervals native | 5/5 | 1/5 | 7 | 13.8 | 154,743 | 139,841 / 12 | 1,104 | $0.0841 |
| Intervals FFSAKE | 5/5 | 5/5 | 8 | 16.2 | 203,987 | 185,880 / 16 | 1,100 | $0.1057 |
| Cache native | 5/5 | 3/5 | 8 | 18.5 | 185,347 | 170,962 / 14 | 1,327 | $0.0868 |
| Cache FFSAKE | 5/5 | 2/5 | 10 | 16.6 | 208,402 | 189,171 / 16 | 1,297 | $0.1049 |

Overall FFSAKE used **7/15** harness-correct runs versus native **6/15**, but artifact completion was **15/15** in both arms. FFSAKE had lower harness completion on inventory (0/5 vs 2/5) and cache (2/5 vs 3/5), and higher on intervals (5/5 vs 1/5). Every FFSAKE run used at least one native Bash/Read/Edit fallback, as permitted by the task prompt.

The overall median paired ratios were **elapsed 1.067** and **input 1.282**; the input target was ≤1.10 and elapsed target ≤1.0. Total cost was $1.3017 for native and $1.5822 for FFSAKE. The [sanitized per-run index](agent-benchmark-runs/20261105-generalization/debug.json) contains per-run metrics and content-free event shapes.

**Verdict: partially met.** Artifact correctness generalized across all three unfamiliar fixtures with no regression, and safeguards remained intact. The efficiency and harness-completion criteria did not: FFSAKE was slower and used more input overall, and failed to produce observed debugger runtime evidence on more inventory and cache runs than native. No candidate, fixture or verifier changes were made during scoring, and no attempts were rerun.

### Final held-out generalization (2026-11-05)

The pilot's exact-name runtime markers caused false completion failures on valid dict/nested inspections, so the pilot was preserved and not pooled. Three new held-out tasks were frozen with independent program/test verifiers: `edit_batch` (three-file percentage-discount edit), `debug_ledger` (multi-iteration loop boundary), and `debug_nested` (nested settings merge). Candidate, prompts and verifiers were unchanged during the final 30 attempts.

| Fixture / arm | Artifact | Harness correct | Calls | Seconds | Total input | Cached / uncached | Output | Cost/run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Edit batch native | 5/5 | 5/5 | 10 | 15.8 | 154,403 | 140,567 / 12 | 1,582 | $0.0853 |
| Edit batch FFSAKE | 5/5 | 5/5 | 11 | 16.5 | 172,915 | 155,382 / 14 | 1,389 | $0.0979 |
| Ledger native | 5/5 | 5/5 | 7 | 12.9 | 154,137 | 141,096 / 12 | 1,027 | $0.0791 |
| Ledger FFSAKE | 5/5 | 5/5 | 13 | 23.1 | 349,547 | 327,193 / 24 | 1,728 | $0.1377 |
| Nested native | 5/5 | 2/5 | 7 | 13.7 | 180,824 | 167,613 / 14 | 991 | $0.0793 |
| Nested FFSAKE | 5/5 | 0/5 | 8 | 13.0 | 172,850 | 155,846 / 14 | 1,122 | $0.0954 |

Artifact completion was **15/15 in both arms**. Harness completion was **FFSAKE 10/15 vs native 12/15**; FFSAKE was lower on nested settings (0/5 vs 2/5). All 30 attempts were valid, with no timeouts. All 15 FFSAKE runs used at least one native Bash/Read/Edit fallback. Total cost was native $1.2203 and FFSAKE $1.6468.

Overall median paired ratios were **elapsed 1.268** and **input 1.127**, above the ≤1.0 and ≤1.10 criteria. The [final sanitized index](agent-benchmark-runs/20261105-generalization/final.json) preserves per-run metrics. The pilot remains at [debug.json](agent-benchmark-runs/20261105-generalization/debug.json) and is not pooled.

**Verdict: not met.** Artifact correctness generalized with no regression, and safeguards remained intact, but FFSAKE failed the harness-completion and efficiency criteria. The 60-attempt allowance is exhausted; no probe or retry was run.

### Multi-stop probe and task-summary candidate (2026-11-05)

The final held-out ledger traces showed FFSAKE using a one-shot probe and then a separate `debug_launch`/`continue`/`terminate` sequence to observe a second loop iteration. `ffsake_debug_probe` now accepts `stops` (1-4, default 1): after the value-sensitive step-over it continues to further breakpoint stops and returns each bounded inspection as `stop_inspection_2`...`stop_inspection_4` before cleanup. `ffsake_workspace_info` now also returns a compact task summary (`total`, `truncated`, up to eight label/group/command/args/authorized entries), removing the common separate `ffsake_tasks` discovery turn.

Model-free measurement on the retained `debug_ledger` fixture: the previous probe plus launch/continue/terminate path returned **8,409 bytes across 4 tool calls**; one `debug_probe` with `stops=2` returned **5,062 bytes in 1 call** with the first stop `index=0` and the second `index=1`. Workspace context with the task summary is **1,127 bytes** and replaces a separate 143-byte `ffsake_tasks` response plus its model turn. The DAP protocol smoke now includes a multi-stop case and asserts one extra `continue`, two collected stops and cleanup; the exact-debugpy preflight uses `stops=2` on the ledger fixture and `stops=4` on the one-shot cache fixture, where the program exits before the requested stop count and the probe still returns cleanly.

Projecting those two changes onto the retained final traces (remove `ffsake_tasks`, collapse debug workaround turns into one probe) gives median calls of **10 vs 10** for `edit_batch`, **9 vs 7** for `debug_ledger`, and **7 vs 7** for `debug_nested`. This is a projection, not a measured result. The remaining ledger difference is one native-source read and an extra verification command, and overall input overhead still includes the fixed MCP catalog. No paid MCP run has measured this candidate.

### Default CLI on the generalization bottlenecks (2026-11-09)

The two fixtures that dominated the 2026-11-05 held-out regression were rerun with the current default integration: CLI plus the Claude read-redirect hook, permission profiles with a three-entry allowlist, and native edits. Five pairs per fixture, 20 valid attempts, **$0.1254**.

| Fixture | Artifact | Harness correct | Calls (native/CLI) | Median seconds (native/CLI) | Median input (native/CLI) | Paired median | Denials (native/CLI) |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Ledger | 5/5 both | 4/5 both | 11 / **6** | 17.3 / **16.2** | 212,683 / **169,335** | **0.988 / 0.684** | 0-2 / 0-1 |
| Nested | 5/5 both | **5/5 vs 2/5** | 8 / **6** | 17.9 / **15.3** | 215,628 / **170,030** | **0.869 / 0.797** | 0-3 / **0** |

Pooled medians were **0.915 elapsed / 0.741 input**, with lower input and elapsed on both fixtures and lower cost. The previous ledger overhead (13 calls and 349,547 input in the held-out MCP arm) did not recur: the CLI probe produced the second iteration in one call. The nested workflow result also reversed from 0/5 to 5/5. Both ledger harness failures, one per arm, were missed runtime observations; the native arm failed the same check. These fixtures were used to design the candidate, so this is a bottleneck regression check, not a new held-out generalization claim. It exercises the CLI probe (`--stops`) rather than the MCP `ffsake_workspace_info` task summary, which remains unmeasured in the MCP arm.

The MCP candidate was then measured directly on the same two fixtures with five pairs each, noninteractive auto-approval as in the 2026-11-05 held-out run, 20 valid attempts, **$0.1708**:

| Fixture | Artifact | Harness correct | Calls (native/MCP) | Median seconds (native/MCP) | Median input (native/MCP) | Paired median |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Ledger | 5/5 both | **5/5 both** | 10 / 10 | 18.3 / 22.2 | 201,313 / 326,707 | **1.115 / 1.925** |
| Nested | 5/5 both | **1/5 vs 0/5** | 13 / 13 | 22.2 / 24.2 | 234,348 / 470,654 | **1.200 / 2.008** |

Pooled medians were **1.158 elapsed / 1.993 input**, and median cost was about 50% higher. Calls now match native, so the multi-stop probe and the task summary removed the turn gap; the remaining overhead is the MCP catalog and `ToolSearch` on every turn. The candidate therefore fixes the measured turn-count problem but does not make the MCP arm competitive, and it is not the default path. The CLI integration on the same fixtures is faster and uses less input (0.915 / 0.741), so keep CLI as the default and do not restore the MCP catalog for this workflow.

### Current default across all three generalization fixtures (2026-11-09)

`edit_batch`, the remaining 2026-11-05 fixture, was measured with the same default CLI configuration: five pairs, 10 valid attempts, **$0.0550**.

| Fixture | Artifact | Harness correct | Calls (native/CLI) | Median seconds (native/CLI) | Median input (native/CLI) | Paired median |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Edit batch | 5/5 both | 5/5 both | 11 / **10** | 14.1 / 16.6 | 146,778 / **143,059** | **1.237 / 1.162** |

Pooling the 15 valid pairs across `edit_batch`, `debug_ledger` and `debug_nested` gives **0.988 elapsed / 0.935 input** (8 and 11 paired wins respectively), total cost **$0.1805**. The debug fixtures now beat native on both axes; multi-file edit remains slower in elapsed time even with one fewer call, consistent with per-read hook and CLI process startup on a short task. All three fixtures informed the current integration, so this is regression evidence for the default configuration, not a new held-out generalization claim.

### Fresh held-out workflows: recursive debugging and feature editing (2026-11-09)

Two new fixtures test workflow shapes absent from the earlier set. `debug_recursive` has a recursive tree total that omits each inner node's own value, so diagnosis requires observing locals at more than one recursion depth. `edit_feature` asks the agent to implement missing pending-list and summary behavior in an existing tracker package against frozen tests: a feature addition rather than the formula or constant fixes in the earlier edit fixtures. Fixture, verifier and model-free debugpy preflight were frozen before the paid runs. The final batch used ten pairs per fixture, 40 valid attempts, **$0.2376** (debug $0.1301, edit $0.1075).

| Task | Artifact | Harness correct | Calls (native/CLI) | Median seconds (native/CLI) | Median input (native/CLI) | Paired median | Denials (native/CLI) |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Recursive debug | 10/10 both | **9/10 vs 7/10** | 11.5 / **6** | 20.5 / **15.1** | 230,942 / **168,496** | **0.760 / 0.734** | 0-4 / **0** |
| Feature edit | 10/10 both | 10/10 both | 10 / **9.5** | 13.3 / 13.4 | 148,610 / 152,916 | **1.022 / 0.948** | 0 / 0-1 |

Pooled across the 20 pairs: **0.941 elapsed / 0.938 input**. The three native recursive-debug failures and one CLI failure all had correct artifacts but no usable observed runtime state; the CLI passed 9/10 versus native 7/10. Feature editing is at elapsed parity and slightly lower input. These are two fresh workflow shapes with 40 attempts, not a broad generalization claim, but together they meet both efficiency criteria. Earlier five-pair samples of the same fixtures are retained in `debug-recursive-10.json` and `edit-feature-10.json`; the ten-pair `heldout-40.json` supersedes them for headline numbers. The fixtures were frozen locally before the runs, so their provenance hashes identify the exact code and the recorded source revision is the parent commit; the fixtures are committed with this evidence.

### Catalog and interaction-cost analysis (2026-11-05)

The final held-out traces show FFSAKE used 157 tool calls versus native 113, while input per call was **22,090 versus 22,353** and seconds per call **1.69 versus 1.87**. The efficiency gap is therefore turn count, not per-turn payload or server latency. Model-free server timings are sub-millisecond for `workspace_info`/`read_files` and 7.7 ms for search; a real debugpy `stops=2` probe takes about 970 ms of actual stepping.

The implemented consolidations remove 27 of the 44 extra turns in a projection: 14 `ffsake_tasks` turns are replaced by the workspace task summary, and 13 probe/launch/continue/terminate turns collapse into one multi-stop probe. Projected calls are **130 versus 113**; the remaining difference is exactly the 17 `ToolSearch` turns. A conservative description and instruction rewrite reduced core context from 37,598 to 31,812 bytes, and profile curation then reduced it to **21,814 bytes (-42%)** with 28 core tools. Editing context fell from 20,238 to **15,206 bytes** (20 tools), and default `all` from 67,521 to **37,909 bytes** (39 tools) after consolidating twelve Rust snapshot tools into one `ffsake_rust` action tool. The five single-operation preview wrappers and the twelve legacy Rust names are no longer advertised anywhere but remain dispatchable; `core` also omits archive, sticky target, explicit refresh, textual rename and remote Git operations, which remain in `all`. The full analysis and redesign proposal are in [ARCHITECTURAL_BARRIER.md](ARCHITECTURAL_BARRIER.md).

### CLI shim measurement (2026-11-05)

`ffsake` restores the resident JSONL CLI and adds a one-shot path for hosts that cannot hold a process open. One-shot `--request` runs the same guarded `Resident` operation; preview requests print a diff and store a private token under `.ffsake/cli/`, and `--apply` replays the request, rechecks hashes and counts, applies, and deletes the token. `ffsake serve` preserves the original persistent-handle mode.

Model-free comparison: the CLI adds **zero** MCP catalog bytes. Responses are close to MCP (`read_files` 581 vs 560 bytes, `preview_files` 967 vs 919, `apply` 109 vs 66). The `serve` protocol measured **75-85 ms** median per round trip versus **209-217 ms** for an equivalent shell pipeline over ten iterations, with 46-48 ms startup. The [CLI smoke](../scripts/cli_smoke.py) covers guarded reads, preview-without-write, token apply-once, stale rejection, undo/redo, authorization denial, traversal rejection, two-stop debug cleanup and persistent handles.

Projected turns if the agent uses one command per step are **4 versus 10** for `edit_batch`, **5 versus 9** for `debug_ledger`, and **5 versus 7** for `debug_nested` against the curated MCP projection. These are projections: adoption and paid timing are unmeasured. Details are in [CLI_SHIM.md](CLI_SHIM.md) and [ARCHITECTURAL_BARRIER.md](ARCHITECTURAL_BARRIER.md).

### CLI arm harness and unseen tasks (2026-11-05)

`rc_benchmark.py --cli-binary <ffsake>` now runs a counterbalanced three-arm comparison: native, curated core MCP and the no-MCP CLI, five attempts per arm per task. Per-run traces record `toolsearch_calls`, `mcp_calls`, `cli_calls` and `cli_adopted`; CLI debug evidence is recognized from `debug_probe` output (`cli_probe`, `cli_cleanup`) while artifact verification stays independent.

Two fresh unseen tasks are frozen for the validation: `edit_multi` (three-file billing validation/serialization/handler fix) and `debug_expand` (tier-boundary bug requiring at least two loop iterations; a `stops=2` probe observes `index=1`). Both initial verifiers fail and both reference fixes pass.

The dry-run preflight (`--dry-run`) validates the CLI binary, the 20,929-byte/28-tool core catalog, the Claude CLI, a guarded CLI read, the 30-attempt plan and the exact CLI guidance without model calls. That batch has since been measured (see below): the CLI was not adopted for `edit_multi` and did not reduce turns for `debug_expand`.

### Three-arm CLI validation (2026-11-05)

The 30-attempt validation compared native, curated core MCP and the no-MCP CLI on two previously unused tasks: `edit_multi` (three-file billing fix) and `debug_expand` (tier boundary requiring two loop iterations). Five attempts per arm per task, counterbalanced. All 30 attempts were valid, all five artifacts passed in every arm, and workflow evidence passed 5/5 in every arm. Total reported cost was **$2.9466**.

| Task / arm | Correct | Calls | Seconds | Total input | Cached | Output | Cost/run | ToolSearch | CLI adopted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Edit native | 5/5 | 7 | 18.1 | 155,698 | 141,863 | 1,453 | $0.080 | 0 | 0/5 |
| Edit MCP | 5/5 | 8 | 17.8 | 195,919 | 179,679 | 1,492 | $0.100 | 4/5 runs | 0/5 |
| Edit CLI | 5/5 | 7 | 17.3 | 158,212 | 144,580 | 1,402 | $0.090 | 0 | 0/5 |
| Debug native | 5/5 | 7 | 16.1 | 186,880 | 171,009 | 1,063 | $0.080 | 0 | 0/5 |
| Debug MCP | 5/5 | 10 | 22.2 | 274,364 | 253,106 | 1,370 | $0.120 | 5/5 runs | 0/5 |
| Debug CLI | 5/5 | 11 | 24.4 | 248,492 | 233,479 | 1,890 | $0.110 | 0 | **5/5** |

Overall median paired ratios versus native:

| Arm | Elapsed | Input |
| --- | ---: | ---: |
| Curated core MCP | **1.092** | **1.394** |
| CLI | **1.190** | **1.268** |

The CLI was not adopted for `edit_multi` at all: the agent used native Bash heredocs and `Write` despite explicit guidance. On `debug_expand` it was adopted in all five runs, but the agent still mixed in native `Read`, `pdb`, and repeated test commands, ending with more turns and slower elapsed time than native. The MCP arm paid one `ToolSearch` per debug run and additional `workspace_info`/read turns. No arm met the competitiveness criteria; native remained fastest and lowest-input overall.

The [sanitized three-arm index](20261105-cli-arm/three-arm.json) preserves per-run metrics. This result supersedes the model-free CLI projections for decision purposes: zero catalog cost did not produce turn parity because tool choice and verification behavior dominate. The remaining gap is host/model behavior, not server latency, catalog size or a missing FFSAKE capability.

### Forced-guarded host policy (2026-11-06)

To test whether native tool availability was the missing lever, the MCP arm disabled native `Read`, `Edit`, `Write`, `Glob` and `Grep` and instructed the agent to use FFSAKE for repository reads/edits while reserving Bash for programs and tests. Ten attempts on `edit_multi` (5 native, 5 forced MCP), counterbalanced, all valid and all artifacts correct. Cost **$1.0036**.

| Arm | Correct | Calls | Seconds | Total input | ToolSearch |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native | 5/5 | 7 | 16.8 | 135,707 | 0 |
| Forced MCP | 5/5 | 7 | 18.9 | 197,998 | 5/5 runs |

Median paired ratios: **1.118 elapsed, 1.454 input**. The forced arm did use the intended path (`workspace_info`, `read_files`, `preview_files`, `apply`) and matched native's call count. It still paid about **9,000 more input tokens per turn** from the 20.9 KB catalog plus larger guarded read/preview payloads. This is the cleanest evidence that per-turn context cost, not only turn count, is a barrier.

A five-attempt forced-CLI follow-up disabled the same native tools and pointed the agent at `ffsake` (cost **$0.4720**). Only **2/5 artifacts passed**: two runs made no edit at all, two passed by using Bash heredocs despite guidance, and one used the CLI plus a shell write. Forcing the CLI path degraded completion instead of producing a usable zero-catalog workflow.

Both experiments are preserved in the [forced-guard index](20261106-forced-guard/guarded-mcp.json) and [forced-CLI index](20261106-forced-guard/forced-cli.json). Together with the three-arm run, the measured conclusion is: native tools remain necessary for reliable completion; FFSAKE is not competitive on either elapsed time or input on these tasks; and the remaining gap is per-turn context plus host/model tool choice, not a missing server capability.

### Cheapest-model validation (2026-11-06)

All paid tests in this section used **Claude Haiku 5.5** (`--model haiku`, effort low), the cheapest available Claude model, rather than Sonnet.

**Three-arm run (30 attempts, $0.2147).** On `debug_expand` all arms were 5/5 correct. MCP used 12 calls / 24.2 s / 381,896 input versus native 10 / 20.4 s / 175,165; median paired ratios **1.185 elapsed, 2.060 input**. The CLI was adopted 5/5 but used 10 calls / 22.2 s / 241,472 input (**1.034 / 1.208**). On `edit_multi`, four of five MCP runs and one baseline/CLI run returned a clarifying question without any tool call; those attempts are marked invalid and were not pooled.

**Edit rerun with a noninteractive instruction (15 attempts, $0.1121).** Adding “Do not ask the user questions” to the task prompt made all arms act. Native 5/5 at 15 calls / 20.5 s / 173,996 input; MCP 4/5 at 13 / 21.9 s / 242,313 (**1.002 / 1.393**); CLI 5/5 at 14 / 19.8 s / 173,938 (**0.910 / 1.052**) but the CLI was not adopted in any run, so that arm is effectively native plus guidance.

**Compact catalog (20 attempts, $0.1786).** A new `compact` profile advertises four multiplexed tools plus the three access tools: **3,827 bytes / 7 tools** versus 20,929 bytes / 28 tools for core. It reduced fixed context but increased interaction:
- `edit_multi`: baseline 5/5 at 14 calls / 19.1 s / 203,379 input; compact 4/5 at 13 / 21.6 s / 244,221 (**1.128 / 1.421**).
- `debug_expand`: baseline 5/5 at 11 calls / 18.5 s / 201,903 input; compact 5/5 at 22 / 40.2 s / 682,634 (**1.929 / 2.616**).

The generic `op` interface caused repeated `query`/`debug_op` calls. Cheaper models did not make FFSAKE competitive, and a smaller catalog alone did not either. Native remained fastest and lowest-input in every fair comparison.

Evidence: [three-arm](20261106-haiku/three-arm.json), [edit rerun](20261106-haiku/edit-noninteractive.json), [compact](20261106-haiku/compact.json). Total paid spend across all studies is now **$4.9276**; no further runs are authorized without a new budget.

### Hybrid compact-read plus granular probe (2026-11-06)

The compact profile's generic `debug_op` caused a call explosion. A hybrid profile kept compact `query`/`change` tools but advertised the granular one-call `ffsake_debug_probe` and `ffsake_debug` instead: **5,979 bytes / 7 tools**, versus 20,929 bytes / 28 for core and 3,827 / 7 for compact. Ten Haiku attempts on `debug_expand`, five native and five hybrid, all valid and all 5/5 correct. Cost **$0.0846**.

| Arm | Correct | Calls | Seconds | Total input | ToolSearch |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native | 5/5 | 11 | 21.2 | 206,668 | 0 |
| Hybrid | 5/5 | 14 | 24.1 | 328,706 | 4/5 runs |

Median paired ratios: **1.139 elapsed, 1.592 input**. The hybrid avoided the compact profile's 22-call blow-up (14 calls) and improved on full core's 2.060x input, but still could not match native. It remained slower and used 59% more input.

This is the best guarded variant measured: full core 1.185/2.060, compact 1.929/2.616, hybrid 1.139/1.592. Native remained the fastest and lowest-input option. Evidence: [hybrid index](20261106-haiku/hybrid-debug.json). Total paid spend is now **$5.0122** across 120 attempts.

### Single-command CLI surface (2026-11-06)

The final candidate added native-like CLI subcommands on the cheapest model: `ffsake read "$PWD" a.py b.py` (line-numbered), `ffsake edit "$PWD" path --old ... --new ...`, `ffsake apply "$PWD" token`, and one-call `ffsake probe "$PWD" --config main --breakpoint path:line --stops 2`. Thirty Haiku attempts, five per arm on `edit_multi` and `debug_expand`, cost **$0.2502**.

| Task / arm | Correct | Calls | Seconds | Input | CLI adopted |
| --- | ---: | ---: | ---: | ---: | ---: |
| Edit native | 5/5 | 15 | 19.8 | 174,582 | 0/5 |
| Edit MCP | 4/5 | 13 | 20.1 | 309,303 | 0/5 |
| Edit CLI | 5/5 | 13 | 24.1 | 267,738 | **5/5** |
| Debug native | 4/5 | 14 | 21.5 | 241,586 | 0/5 |
| Debug MCP | 4/5 | 10 | 20.4 | 275,861 | 0/5 |
| Debug CLI | 5/5 | 14 | 27.1 | 399,560 | **5/5** |

Median paired ratios versus native: edit CLI **1.214 elapsed / 1.520 input**; debug CLI **1.435 / 2.108**. MCP was 1.120/1.531 and 1.250/1.809. The CLI was now adopted in every run and completed 5/5 on both tasks, but it remained slower and higher-input than native.

**Verifier correction, no rerun.** The first pass reported CLI debug as 0/5 correct because the workflow-evidence detector recognized only `--request` probes, not the new `probe` subcommand. Re-evaluating the retained traces with `probe` recognized raised CLI debug to 5/5 artifact and 5/5 evidence. No model calls were made for this correction.

Evidence: [single-command CLI index](20261106-haiku/cli-subcommands.json). Total paid spend is **$5.2624** across 150 attempts; ~$0.74 of the $6 ceiling remains.

### Forced native-like CLI (2026-11-06)

Final candidate test: disable native `Read`, `Edit`, `Write`, `Glob` and `Grep` and run only the native-like CLI subcommands. Ten Haiku attempts (five per task), all valid, all **5/5 correct**, cost **$0.0841**. Native comparisons use the retained Haiku baselines from the immediately preceding run, so they are matched on model, fixtures and task prompts but not contemporaneous.

| Task / arm | Correct | Calls | Seconds | Input | Paired ratios |
| --- | ---: | ---: | ---: | ---: | --- |
| Edit native | 5/5 | 14 | 19.8 | 174,582 | — |
| Edit forced CLI | 5/5 | 12 | 24.4 | 284,664 | 1.403 elapsed / 1.544 input |
| Debug native | 4/5 | 14 | 20.7 | 235,201 | — |
| Debug forced CLI | 5/5 | 9 | 20.7 | 280,159 | **0.998** elapsed / **1.189** input |

Forcing the CLI solved adoption and correctness and cut calls by 19-29%. On debug it reached **elapsed parity** (0.998) with 9 calls versus 14, but still used 19% more input. On edits it was slower and 54% higher-input. The remaining input gap comes from CLI process/output payload and model turns, not catalog size.

Evidence: [forced CLI index](20261106-haiku/forced-cli-subcommands.json). Total paid spend is **$5.3465** across 160 attempts; ~$0.65 remains.

### Payload-trimmed forced CLI (2026-11-06)

The CLI was trimmed to reduce payload: `probe` returns only stop reason, line and `values` name/value pairs; `read` accepts `path:START-END` ranges and preserves original line numbers. Twenty Haiku attempts (10 debug, 10 edit), **$0.1568**.

**Debug target met.** Five native and five forced-CLI attempts on `debug_expand`:

| Arm | Correct | Calls | Seconds | Input |
| --- | ---: | ---: | ---: | ---: |
| Native | 4/5 | 14 | 24.8 | 295,226 |
| Forced CLI | 5/5 | 8 | 19.3 | 248,998 |

Median paired ratios: **0.838 elapsed / 0.923 input** — both targets (≤1.0, ≤1.10), with 5/5 correctness versus native 4/5. Two of five pairs were above 1.10 input (1.258 and 1.284); the median meets the target.

**Edit still fails.** Five native and five forced-CLI attempts on `edit_multi`, all 5/5 correct:

| Arm | Correct | Calls | Seconds | Input |
| --- | ---: | ---: | ---: | ---: |
| Native | 5/5 | 15 | 19.6 | 203,992 |
| Forced CLI | 5/5 | 10 | 23.2 | 251,426 |

Median paired ratios: **1.176 elapsed / 1.445 input**. Fewer calls but larger per-turn payload (reviewed preview diffs and command output).

**Verifier correction, no rerun:** the compact probe reports `values` instead of a `variables` array, so the first pass scored CLI debug 0/5. Re-evaluating the retained traces with `values` recognized raised it to **5/5 artifact and 5/5 evidence**. No model calls were made for the correction.

Evidence: [debug](20261106-haiku/payload-debug.json), [edit](20261106-haiku/payload-edit.json). Total paid spend is **$5.5033** across 180 attempts; ~$0.50 remains.

### Edit payload trim rerun (2026-11-06)

The edit path was trimmed again: compact preview output (`diff` plus token, no metadata), compact apply output, ranged reads, and a shorter CLI guidance block. Ten Haiku attempts (five native, five forced CLI), all valid, all 5/5 correct, cost **$0.0799**.

| Arm | Correct | Calls | Seconds | Input |
| --- | ---: | ---: | ---: | ---: |
| Native | 5/5 | 15 | 19.8 | 172,287 |
| Forced CLI | 5/5 | 10 | 27.6 | 317,382 |

Median paired ratios: **1.436 elapsed / 1.872 input** — worse than the previous edit run's 1.176/1.445. The trim did not reduce the edit arm's input; the model spent more per turn despite fewer calls. Native remained faster and lower-input. The edit target is not met.

Evidence: [compact edit index](20261106-haiku/payload-edit-compact.json). Total paid spend is **$5.5832** across 190 attempts; ~$0.42 remains.

### Single-call batch edit rerun (2026-11-06)

Added `ffsake edit "$PWD" --edit path --old ... --new ... --edit path2 ...`, which previews all files in one call and returns a compact diff plus one token, followed by one `apply`. Ten Haiku attempts, **$0.0794**, all valid, both arms 5/5.

| Arm | Correct | Calls | Seconds | Input |
| --- | ---: | ---: | ---: | ---: |
| Native | 5/5 | 15 | 19.3 | 171,787 |
| Forced CLI batch | 5/5 | 9 | 23.5 | 284,008 |

Median paired ratios: **1.249 elapsed / 2.023 input** — worse than both previous edit runs (1.436/1.872 and 1.176/1.445). Fewer calls did not reduce input; one run used 17 turns and 534,402 input. Batching the preview did not fix the edit path.

Evidence: [batch edit index](20261106-haiku/payload-edit-batch.json). Total paid spend is **$5.6626** across 200 attempts; ~$0.34 remains.

### Line-addressed edit rerun (2026-11-06)

Range-capped `read` (`path:8-12`) and line-addressed single-file `edit --lines 8-10 --old ... --new ...` were tested. Ten Haiku attempts, **$0.0856**, all valid, both arms 5/5.

| Arm | Correct | Calls | Seconds | Input |
| --- | ---: | ---: | ---: | ---: |
| Native | 5/5 | 14 | 17.2 | 146,135 |
| Forced CLI lines | 5/5 | 15 | 34.2 | 383,281 |

Median paired ratios: **1.955 elapsed / 2.623 input** — the worst edit result. The model performed repeated range reads and edit attempts (12-24 calls), so narrowing the read/edit scope increased turns and tokens rather than reducing them. Native remained faster and lower-input.

Evidence: [line-addressed index](20261106-haiku/payload-edit-lines.json). Total paid spend is **$5.7482** across 210 attempts; ~$0.25 remains.

### Split workflow: native edits, CLI reads/probe (2026-11-06)

Final candidate kept native Edit/Write for changes and guided the CLI only for ranged reads and one-call probe. Ten Haiku attempts (five per task), all valid, all 5/5, cost **$0.0736**. Native baselines are retained from the immediately preceding matched Haiku batches, not contemporaneous.

| Task / arm | Correct | Calls | Seconds | Input | Paired ratios |
| --- | ---: | ---: | ---: | ---: | --- |
| Edit native (retained) | 5/5 | 14 | 17.2 | 146,135 | — |
| Edit split | 5/5 | 15 | 18.9 | 203,775 | 1.064 elapsed / 1.195 input |
| Debug native (retained) | 4/5 | 14 | 24.8 | 295,226 | — |
| Debug split | 5/5 | 13 | 22.6 | 295,875 | **0.985** elapsed / **0.888** input |

**Debug holds the target** on both criteria with 5/5 versus native 4/5. **Edit does not:** the CLI reads were not adopted for `edit_multi` (0/5), so the arm is native edits plus guidance overhead, and input was 19.5% higher.

Evidence: [split workflow index](20261106-haiku/split-workflow.json). Total paid spend is **$5.8218** across 220 attempts; ~$0.18 remains.

### Forced CLI reads, native edits (2026-11-06)

Native `Read`, `Glob` and `Grep` were disabled in the CLI arm while `Edit`/`Write` stayed available, forcing the CLI range-read path with native edits. Ten Haiku attempts, **$0.0814**, ten valid.

| Arm | Correct | Calls | Seconds | Input | Paired ratios |
| --- | ---: | ---: | ---: | ---: | --- |
| Native | 5/5 | 15 | 19.3 | 203,245 | — |
| Forced CLI reads | 4/5 | 11 | 26.3 | 269,953 | **1.426 elapsed / 1.444 input** |

The CLI read path was adopted in all five runs (3-6 CLI calls), and calls fell 19%, but input was still 44% higher and one CLI attempt failed. Native edits plus CLI reads did not restore parity. The edit target remains unmet.

Evidence: [force-reads index](20261106-haiku/force-reads.json). Total paid spend is **$5.9032** across 230 attempts; ~$0.10 remains.

### No-guidance forced read rerun (2026-11-06)

Native `Read`/`Glob`/`Grep` were disabled with native edits kept and **no CLI guidance text**. Ten Haiku attempts, **$0.0869**, ten valid.

| Arm | Correct | Calls | Seconds | Input | Paired ratios |
| --- | ---: | ---: | ---: | ---: | --- |
| Native | 5/5 | 15 | 20.3 | 207,512 | — |
| No-guidance CLI reads | **1/5** | 14 | 29.2 | 352,731 | 1.428 elapsed / 1.506 input |

Without guidance the model could not discover the CLI read path; it guessed file contents and four of five runs failed. CLI guidance is therefore required for correctness, and the guided version still measured 1.426/1.444. The edit path has no configuration that meets the target: guidance adds overhead and native reads avoid the CLI entirely.

Evidence: [no-guidance index](20261106-haiku/no-guidance.json). Total paid spend is **$5.9901** across 240 attempts; ~$0.01 remains.

### Task-scoped guidance and CLI-first integration (2026-11-06)

The CLI-first integration writes one guidance block and needs one host permission rule (`Bash(ffsake:*)`) instead of an MCP server entry, a 20.9 KB catalog and up to 28 per-tool approvals. Twenty Haiku attempts measured task-scoped guidance (CLI guidance only for debug tasks), cost **$0.1393**.

| Task / arm | Correct | Calls | Seconds | Input | Paired ratios |
| --- | ---: | ---: | ---: | ---: | --- |
| Edit native | 5/5 | 13 | 17.4 | 171,717 | — |
| Edit CLI-config | 5/5 | 15 | 18.7 | 178,015 | 1.164 elapsed / 1.004 input |
| Debug native | 5/5 | 11 | 20.0 | 203,168 | — |
| Debug CLI guidance | 5/5 | 12 | 21.1 | 232,701 | 1.052 elapsed / 1.141 input |

Edit input was at parity (1.004) when no CLI guidance was added, but elapsed was noisy (1.164) and the CLI was unused. Debug used the CLI in every run, but this batch measured 1.052/1.141. Across three debug batches the median is about **0.985 elapsed / 0.923 input**, so the debug advantage is real but variable; edits remain native-bound.

The CLI-first setup is model-free checked by `scripts/cli_setup_smoke.py`: no MCP config is created, tool approvals are zero, one permission rule is printed, guidance is idempotent, and the CLI smoke still covers access, preview/apply, history and cleanup.

Evidence: [task-guidance index](20261106-haiku/task-guidance.json). Total paid spend is **$6.1294** across 260 attempts; the earlier $6 ceiling is superseded by the current objective.

### Fresh-task permission-controlled comparison (2026-11-06)

Two previously unused fixtures were added: `edit_units` (three unit-conversion modules) and `debug_cursor` (pagination requiring at least two page iterations). The comparison used host permission profiles instead of skip-all: native allowed `Bash,Read,Edit,Write,Glob,Grep`; CLI allowed `Bash,Edit,Write` with native reads intended to be unavailable. Twenty Haiku attempts, **$0.1129**.

| Task / arm | Correct | Artifact | Calls | Seconds | Input | Permission denials |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Edit units native | 4/5 | 4/5 | 14 | 17.0 | 68,078 | 0–1 |
| Edit units CLI | **1/5** | 1/5 | 20 | 30.8 | 154,589 | 3–6 |
| Debug cursor native | 4/5 | 5/5 | 12 | 19.0 | 99,673 | 0–2 |
| Debug cursor CLI | **0/5** | 0/5 | 18 | 32.8 | 171,166 | 2–6 |

Median paired ratios: edit **1.797 elapsed / 2.292 input**; debug **1.621 / 1.717**. The CLI arm had more permission denials despite fewer configured scopes, and native Read still appeared in traces even with `--tools`/`--allowedTools`/`--disallowedTools` combinations. Host-level enforcement was not reliable, and the CLI path was slower and less correct on both fresh tasks.

Evidence: [fresh permission index](20261106-permission/fresh.json). Total paid spend is **$6.5751** across 320 attempts.

### Host hook policy: first-class read enforcement attempt (2026-11-06)

A Claude Code `PreToolUse` hook was implemented (`scripts/claude_pre_tool_policy.py`) and installed with `--settings` for the CLI arm. It denies native `Read`/`Glob`/`Grep` and file-reading shell commands, allows native `Edit`/`Write`, and allows `ffsake`, program and test commands. The hook was model-free checked by `scripts/claude_hook_smoke.py`. Twenty Haiku attempts on the fresh `edit_units` and `debug_cursor` tasks, **$0.1637**, all valid and all 5/5 correct.

| Task / arm | Correct | Calls | Seconds | Input | Permission denials |
| --- | ---: | ---: | ---: | ---: | ---: |
| Edit native | 5/5 | 13 | 18.0 | 174,685 | 0–1 |
| Edit hooked CLI | 5/5 | 19 | 25.5 | 327,241 | 8–11 |
| Debug native | 5/5 | 10 | 17.9 | 172,998 | 0–2 |
| Debug hooked CLI | 5/5 | 17 | 29.7 | 399,732 | 7–11 |

Median paired ratios: edit **1.590 elapsed / 1.894 input**; debug **1.250 / 1.669**. The hook enforcement worked—Read outputs were `PreToolUse:Read hook error`—but the agent retried the denied read 7–11 times before using the CLI. Native `--tools`/`--allowedTools` combinations did not remove Read; the hook was the only working enforcement.

This closes the host-enforcement question with a negative result: a deny-only hook adds permission friction and retries. A host plugin or hook that replaces native Read with a first-class scoped read tool (or rewrites the call) is required to avoid retries. Without that, native remains faster and lower-input on fresh work even when correctness is preserved.

Evidence: [hook policy index](20261106-permission/hook-policy.json). Total paid spend is about **$6.78** across roughly 346 attempts.

### Read-redirect hook: first fresh-task validation (2026-11-06)

> Superseded by “Final integration: batched commands and mandatory probe” below; this run is retained as the first redirect measurement.

Native `Read` calls are now rewritten through FFSAKE by a Claude Code `PreToolUse`
hook. The hook runs `ffsake read`, writes the returned text under `.ffsake/hook-reads`,
and returns `updatedInput.file_path` for the cache file, so the model keeps using
Read but the content comes from the workspace policy. There is no denial and no
retry loop. File-reading shell commands are still denied. Native Edit/Write remain
available for changes, and `ffsake probe` supplies one-call multi-stop debugging.

Ten Haiku attempts per task on the fresh `edit_units` and `debug_cursor` fixtures,
**$0.2608**, all 40 attempts valid. Native and redirect arms ran contemporaneously.

| Task / arm | Correct | Calls (med) | Seconds (med) | Input (med) | Cost (med) | Denials |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Edit native | 10/10 | 12.5 | 16.5 | 156,682 | $0.00614 | 0–1 |
| Edit redirect | 10/10 | 13.0 | 17.1 | 157,229 | $0.00620 | 0–2 |
| Debug native | **7/10** | 10.5 | 20.2 | 201,868 | $0.00694 | 0–4 |
| Debug redirect | **10/10** | 10.0 | 21.0 | 215,081 | **$0.00655** | 0–1 |

Median paired ratios: edit **1.075 elapsed / 1.075 input** (practical parity;
only 2/10 pairs favored the redirect arm on either measure). Debug **1.038
elapsed / 1.196 input**, but **10/10 correct versus native 7/10** and **5.6%
lower cost**. The three native debug failures were real workflow failures: the
agent fixed the program without observing the two required page iterations. The
one-call probe makes the required observation part of the normal path.

An earlier 20-attempt run with the same hook measured edit 1.056 / 0.998 and
debug 0.898 / 0.856. The direction is not stable at n=5; the 10-repetition run is
the basis for the claim. Across both runs the supported statement is: **edit
efficiency at parity, debug efficiency at parity-to-slightly-worse on tokens but
better on correctness and cost, with far fewer permission retries than the
deny-only hook (0–2 versus 7–11)**.

Measurement defect fixed: the file audit counted `__pycache__/*.pyc` created by
running Python as repository changes, and counted verifier-created caches as
agent changes. Both audits now ignore bytecode and tool caches; `reevaluate_rc_results.py`
recomputed retained traces without model calls.

Evidence: [final 40-attempt index](20261106-redirect/final-40.json),
[earlier 20-attempt index](20261106-redirect/earlier-20.json). The final run cost **$0.2608**; the current Haiku validation phase totals about **$7.26**.

### Final integration: batched commands and mandatory probe (2026-11-06)

The guidance was tightened after trace review in three ways: combine simple
commands (for example `git ls-files && python3 main.py`) instead of one command
per turn, name only the debug program from the task prompt and use
`<source-file>:<line>` placeholders (no hidden target-file hint), and require at
least one probe before editing. Twenty Haiku attempts per task, **$0.2432**,
all 40 valid.

| Debug task / arm | Correct | Calls (med) | Seconds (med) | Input (med) | Cost (med) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cursor native | 8/10 | 10.0 | 20.7 | 203,481 | $0.00707 |
| Cursor FFSAKE | **10/10** | **5.0** | **15.3** | **140,649** | **$0.00528** |
| Retry native | 9/10 | 9.5 | 15.6 | 170,198 | $0.00605 |
| Retry FFSAKE | **10/10** | **5.5** | **14.6** | **140,386** | **$0.00522** |

Pooled across the 20 debug pairs: **0.806 elapsed, 0.826 input, 0.867 cost**;
FFSAKE won 16/20 elapsed pairs and 17/20 input pairs, and completed **20/20
versus native 17/20**. Native failures were workflow failures: the agent fixed
the program without observing the required runtime iterations.

Edit remains at parity. Thirty pairs across `edit_units`, `edit_settings` and
the multi-file rename `edit_refactor` were 30/30 in both arms with pooled median
**1.038 elapsed / 1.003 input / 1.015 cost**. The edit arm uses no CLI guidance
and native Edit/Write; FFSAKE governs the reads. The refactor shape on its own
measured 1.036 / 1.009 / 1.034 with 10/10 both.

The immediately preceding fair run without the mandatory-probe instruction
(`debug-no-always-probe-40.json`) was 18/20 versus native 16/20 because two
FFSAKE runs skipped the probe and failed workflow evidence. The instruction
fixed that failure mode without adding turns.

Evidence: [final debug index](20261106-redirect/final-debug-40.json),
[without always-probe](20261106-redirect/debug-no-always-probe-40.json),
[edit settings](20261106-redirect/edit-settings-40.json),
[edit refactor](20261106-redirect/edit-refactor-20.json). Final evidence batch
cost **$0.8833**; the current Haiku validation phase totals about **$8.89**.

### Permission-minimal hook integration and second host (2026-11-06)

The permission comparison was rerun **without `--dangerously-skip-permissions`** so host defaults applied. The native arm allowed six tools (`Bash,Read,Edit,Write,Glob,Grep`); the FFSAKE arm allowed three (`Bash,Edit,Write`) and installed the Read-redirect hook. The hook's settings file carries the same three-entry allowlist, so the hook and the host policy compose. Eighty Haiku attempts, **$0.5123**, all valid.

| Task group | Correct | Elapsed (med) | Input (med) | Cost (med) | Denials/run |
| --- | ---: | ---: | ---: | ---: | ---: |
| Edit native (20 pairs) | 20/20 | 17.0–18.3 s | 151,361–184,791 | $0.00610–$0.00676 | 0–2 |
| Edit FFSAKE (20 pairs) | **20/20** | 17.9–19.2 s | 172,179–174,047 | $0.00629–$0.00668 | 0–1 |
| Debug native (20 pairs) | **15/20** | 17.0–21.1 s | 195,909–245,677 | $0.00634–$0.00749 | 0–4 |
| Debug FFSAKE (20 pairs) | **20/20** | 14.9–15.6 s | 140,096–140,769 | $0.00531–$0.00532 | **0** (one 1) |

Pooled medians: edit **1.024 elapsed / 0.946 input / 1.016 cost** (13/20 input wins); debug **0.766 elapsed / 0.655 input / 0.739 cost** (17/20 elapsed, 19/20 input wins). Native debug failures were workflow failures: the agent fixed the program without observing the required runtime iterations. FFSAKE's probe makes that observation part of the path and cleans up.

This is the first configuration that measures all three goal axes together: **half the allowlist entries, roughly four times fewer permission denials, 20/20 versus 15/20 debug correctness, edit efficiency at parity, and debug efficiency materially better.**

A second host was tested with OpenCode 2.2.5 + Claude Haiku 5.5 on the same fixtures. The first five-attempt run had no redirect integration, so its CLI arm was guidance-only (`20261106-opencode/haiku-20.json`):

| Task (guidance only) | Native | FFSAKE CLI | Paired median |
| --- | ---: | ---: | --- |
| Edit units | 5/5 | **5/5** | 0.967 elapsed / 1.272 input |
| Debug cursor | 3/5 | **5/5** | **0.848 elapsed / 0.832 input** |

The OpenCode v2 read-redirect plugin described above was then measured on the same fixtures: ten attempts per task, 40 valid runs (`20261109-opencode-redirect/redirect-40.json`). Native `read` was served by `ffsake read`; native edits, writes and search tools stayed available.

| Task (redirect plugin) | Native | FFSAKE CLI | Paired median | Median calls (native/CLI) | Denials |
| --- | ---: | ---: | --- | ---: | --- |
| Edit units | 10/10 | **10/10** | **1.258 elapsed / 1.132 input** | 7 / 14 | 0 / 0 |
| Debug cursor | 8/10 | **10/10** | **0.914 elapsed / 0.943 input** | 11 / 9.5 | 0 / 0 |

Pooled medians were **1.075 elapsed / 1.043 input**. Debug retained its correctness and efficiency advantage (7/10 elapsed and 6/10 input paired wins, 10/10 versus 8/10 correct; native failures were workflow failures). Edit did not: the redirect made reads a shell-shaped turn in this host, the model used roughly twice the calls, and both elapsed and input were worse. The plugin is therefore a working enforcement and correctness mechanism, not an edit-efficiency win; results are retained as measured rather than replaced by the earlier five-attempt sample. OpenCode reported zero cost for this provider, so only elapsed, input and correctness are comparable. The permission advantage (three entries, fewer denials) was measured on Claude Code.

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
python3 scripts/rc_benchmark.py --binary /private/path/to/ffsake-mcp \
  --output /private/path/to/opencode-redirect.json --agents opencode \
  --tasks edit_units,debug_cursor --repetitions 10 \
  --cli-binary /private/path/to/ffsake --cli-native --cli-debug-guidance \
  --opencode-redirect-reads --opencode-model provider/cheapest-model \
  --debug-python /absolute/path/to/debugpy-venv/bin/python
python3 scripts/debug_poll_probe.py /private/path/to/corrected-instrumented-mcp \
  --baseline-binary /private/path/to/old-instrumented-mcp \
  --python /absolute/path/to/debugpy-venv/bin/python \
  --output /private/path/to/debug-probe.json --repetitions 5
```
