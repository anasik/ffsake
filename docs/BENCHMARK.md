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


Codex benchmark workspaces explicitly enable the ten known FFSAKE MCP tools and set `approval_mode = "approve"` for each of those tools only. This avoids the CLI's noninteractive `approval_policy = "never"` rejecting the calls before server execution. The disposable project config does not change global approval or sandbox settings, and this approval configuration is not a claim that native Codex tools are disabled. Approval-blocked attempts are recorded and invalidate performance comparisons.

For targeted diagnosis after a baseline is already known to work, use `--modes ffsake` (or `--modes baseline`). The default remains both modes. Rebuild `ffsake-mcp` after changes to the server. Selector schemas now expose `"All"`, `{"Exact":"path"}`, `{"Glob":"pattern"}` and `{"Regex":"pattern"}`. An empty include list deliberately selects no files; omission of the search target uses the current target or the whole workspace.

## Successful Codex micro pilot: workflow overhead

The supplied `suite-20261021-221551-mjgx150r.json` trace passed exact verification of all six fixture files: 11 replacements, seven successful FFSAKE calls, no observed shell calls, and 25.962 seconds elapsed. The calls were list, search, preview, apply, refresh, search-old, search-new. This is one diagnostic run and does not establish a general performance result or host-enforced isolation.

Directory listing was not needed to discover the matching files in this fixture. Explicit refresh was redundant because the next search after an FFSAKE mutation refreshes automatically. Both verification searches remain useful; their range details were unnecessary because only counts were needed. Tool descriptions and benchmark guidance now explain this shorter five-call route, and MCP smoke tests verify old/new counts immediately after apply without explicit refresh. The runtime already cleared the dirty flag on explicit refresh; no runtime refresh behavior was changed. Whether agents follow the shorter route and improve elapsed time remains unmeasured.

## OpenCode working-directory regression

The supplied `suite-20261022-120609-bem3pk6l.json` pilot did not operate on the disposable fixture: its baseline trace read the FFSAKE checkout's source, journals and earlier reports, and FFSAKE mode had no recognized MCP calls. These runs cannot establish agent performance on the fixture.

[OpenCode v1.18.31's run command](https://github.com/anomalyco/opencode/blob/v1.18.31/packages/opencode/src/cli/cmd/run.ts) prefers `process.env.PWD` when `--dir` is omitted. Changing only the subprocess working directory left the parent checkout in that environment variable. Agent processes now receive a matching PWD, and OpenCode also receives an explicit absolute --dir. Reports record the fixture path and invalidate recognized OpenCode file calls with explicit paths outside it.

Before any OpenCode model call, preflight uses its debug file-read and resolved-config commands in disposable git fixtures. It checks a unique file token, ordered permission settings and the FFSAKE MCP configuration for both modes. It does not persist resolved config, which may contain credentials. Run `python3 scripts/agent_benchmark_suite.py --agents opencode --preflight-only` to check an installed CLI without model calls. This validates configuration loading, not OS-enforced isolation or successful MCP connection.

On POSIX, agent launchers run in their own process group, which is killed on timeout or interruption so normal launcher/MCP/shell descendants cannot continue. Windows currently terminates the direct process only. Zero-model regression tests exercise inherited PWD mismatch, explicit directory selection, configuration probes, rule ordering, outside-workspace path reporting and descendant timeout cleanup.
