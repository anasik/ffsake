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


Codex benchmark workspaces explicitly enable the sixteen known FFSAKE MCP tools and set `approval_mode = "approve"` for each of those tools only. This avoids the CLI's noninteractive `approval_policy = "never"` rejecting the calls before server execution. The disposable project config does not change global approval or sandbox settings, and this approval configuration is not a claim that native Codex tools are disabled. Approval-blocked attempts are recorded and invalidate performance comparisons.

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
