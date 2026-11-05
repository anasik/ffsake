# FFSAKE efficiency barrier and redesign

Status: evidence-backed analysis after the 2026-11-05 final held-out comparison.
The current build is **not demonstrably competitive**, and the measured gap is
not explained by server latency or tool-response size. It is explained by
interaction count.

## Measured gap

Final held-out run: 30 paid attempts, five per arm on `edit_batch`,
`debug_ledger` and `debug_nested`; candidate `3e9a9c8`; raw evidence retained
privately, sanitized index in
[`agent-benchmark-runs/20261105-generalization/final.json`](agent-benchmark-runs/20261105-generalization/final.json).

| Measure | Native | FFSAKE | Ratio |
| --- | ---: | ---: | ---: |
| Tool calls (15 runs) | 113 | 157 | 1.39 |
| Total input tokens | 2,525,818 | 3,468,156 | 1.37 |
| Elapsed seconds | 210.9 | 264.7 | 1.25 |
| Input tokens per call | 22,353 | 22,090 | 0.99 |
| Seconds per call | 1.87 | 1.69 | 0.90 |
| Artifact completion | 15/15 | 15/15 | 1.00 |
| Harness completion | 12/15 | 10/15 | 0.83 |

Per-call input and per-call elapsed time were **equal or better** for FFSAKE.
The entire measured loss comes from 44 extra turns.

## Where the extra turns go

FFSAKE tool use across the same 15 runs:

| Turn type | FFSAKE | Native |
| --- | ---: | ---: |
| Bash | 41 | 77 |
| Edit | 25 | 25 |
| Read | 19 | 11 |
| `ToolSearch` | 17 | 0 |
| `workspace_info` | 15 | 0 |
| `tasks` | 14 | 0 |
| debug probe/actions | 23 | 0 |
| `read_files` / `list` | 3 | 0 |
| **Total** | **157** | **113** |

Native spends most of its turns on Bash, including 11 `pdb` invocations. FFSAKE
replaces those with MCP debugging but pays for discovery: `ToolSearch`,
`workspace_info`, `tasks`, and a multi-step debug sequence.

Two consolidations already implemented remove 27 of the 44 extra turns in a
trace projection:

1. `ffsake_workspace_info` now includes a compact task summary, removing 14
   `tasks` turns.
2. `ffsake_debug_probe` now collects up to four breakpoint stops and steps over
   value-changing lines in one call, removing 13 probe/launch/continue/terminate
   turns.

Projected totals become **130 calls versus 113** (edit 50 vs 44, ledger 45 vs
34, nested 35 vs 35). The remaining 17-turn difference is exactly the
`ToolSearch` count. No other category is larger.

## Fixed catalog cost

The MCP server ships its tool schemas and instructions into the host context.
Measured from `tools/list` and `initialize`:

| Profile | Tools before → after | Context before | After trim | After curation/consolidation |
| --- | ---: | ---: | ---: | ---: |
| core | 39 → 28 | 37,598 | 31,812 | **21,814** |
| editing | 25 → 20 | 20,238 | 17,659 | **15,206** |
| all | 55 → 39 | 67,521 | 58,604 | **37,909** |

The description trim rewrote 52 descriptions and both instruction blocks. The
curation step then removed catalog entries without removing dispatch:

- The five single-operation preview wrappers (`preview_create`, `copy`, `move`,
  `delete`, `edit`) are no longer advertised in any profile because
  `ffsake_preview_files` covers them. The aliases remain callable, so existing
  clients and tests keep working.
- `core` additionally omits archive browsing, sticky target, explicit refresh,
  textual rename and advanced Git branch/fetch/push. Those remain advertised in
  `all` and mostly in `editing`. Core is now 28 tools and **20.9 KB of tools +
  0.9 KB of instructions = 21.8 KB**, against the 20 KB target.

At roughly four bytes per token, core context fell from about 9,400 to 5,450
tokens per request (-42%). Default `all` context fell 16.8% and editing 24.9%.
Per-call input was already equal to native, so catalog size cannot explain the
37% total-input gap by itself. Its plausible role is indirect: a 31 KB,
39-tool catalog may cause the host to defer MCP tools behind `ToolSearch`.
That hypothesis is consistent with the 17 `ToolSearch` turns but has not been
isolated experimentally; the next paid batch must measure it.

## Server latency is not the bottleneck

Model-free timings against the exact candidate on the retained fixtures:

| Operation | Median |
| --- | ---: |
| `workspace_info` | 1.2 ms |
| `read_files` (3 files) | 0.6 ms |
| `search` | 7.7 ms |
| `debug_probe` (`stops=2`, real debugpy) | 970 ms |

Server compute is sub-millisecond to low-millisecond for file operations. The
debug probe time is DAP stepping, not MCP overhead. Removing a turn saves a
model round trip of roughly 1.7 seconds and a full context resend, far more than
any server-side optimization could recover.

## Verifier limitation

`debug_nested` workflow evidence was 0/5 for FFSAKE and 2/5 for native, but
artifacts were 5/5 in both arms. The exact-name marker required a local named
`key` with value `limits`. FFSAKE stopped at the `main.py` call site and
observed `DEFAULTS={'limits': {'burst': 4, 'rate': 2}}` and
`settings={'limits': {'rate': 5}}`; native runs sometimes happened to stop
inside `merge`. The observed runtime state was relevant, but the marker rejected
it. Three of the fifteen harness-completion misses are therefore verifier
limitations, not failed debugging.

Any future scored run must replace the exact-name marker with a disclosed
general check: a debugger-observed runtime value whose variable name or rendered
value contains the target token. Artifact completion stays independent. This
must be frozen before scoring and reported separately.

## Why this is architectural, not a tuning miss

- FFSAKE's per-turn cost is already at or below native.
- Every MCP tool call is a full agent turn: the host resends the conversation
  and adds the catalog. Cost scales with turns, not with server work.
- The remaining turn gap after consolidation is dominated by host-side tool
  discovery (`ToolSearch`), which the server cannot remove by changing a handler.
- The granular MCP surface also forces separate turns for discovery, execution
  and verification where native shells combine them.

## Concrete redesign

### R1. Catalog diet and consolidation (implemented for core)

- Done: 52 descriptions rewritten and instructions halved; core tools drop from
  39 to 28 and core context from 37,598 to 21,814 bytes (-42%). Default `all`
  context drops 16.8%.
- Done: the five redundant single-operation preview wrappers are hidden from all
  catalogs while remaining dispatchable; `ffsake_preview_files` is the advertised
  path. Core also omits archive, sticky target, explicit refresh, textual rename
  and remote Git operations, which stay in `all`.
- Done: the twelve explicit-snapshot Rust tools are consolidated into one
  `ffsake_rust` action tool. The legacy names stay dispatchable but are hidden
  from every catalog. This removes repeated `crates`/position schemas and cuts
  the default `all` catalog from 56,151 to **37,909 bytes (-44% from the start,
  39 tools)**. `ffsake_rust` covers definition, references, implementation,
  hover, symbols, inspect, workspace/diagnostics, manifest context, rename
  proposal, code actions and identifier selection; preview/apply handles remain
  separate guarded steps.
- Test: if the host stops issuing `ToolSearch` for the 21.8 KB core context, the
  turn projection reaches 113 vs 113 and parity is plausible. If it does not,
  R3 is required.

### R2. Composite guarded operations

- Done: one-call debug probe with multi-stop collection; workspace context with
  task summary.
- Keep review semantics: preview/apply stays two turns because collapsing it
  would weaken reviewed edits. Do not trade safety for turns.
- Candidate next: one-call "read + search + workspace map" is already
  `workspace_info` with `files`; encourage it over separate `read` calls in
  guidance and measure adoption.

### R3. CLI shim for high-frequency operations (implemented)

`ffsake` is restored and extended. It exposes the same `Resident` request
surface through the host's existing Bash tool, with no MCP catalog.

- One-shot: `ffsake WORKSPACE --request JSON` executes any stateless or
  one-shot request (read, read_files, list, search, git, debug_probe, undo,
  redo). Preview requests print a diff and store a private token; `--apply
  TOKEN` replays the request against current files, rechecks hashes/counts,
  applies, and deletes the token. Preview and apply remain separate reviewed
  steps.
- Persistent: `ffsake serve WORKSPACE` preserves the original JSONL resident
  mode and its in-process handles for MatchSets, previews, debug sessions and
  tasks.
- Safeguards: same access policy, stale-state checks, journal undo/redo,
  owned-adapter cleanup and internal-path guards. Tokens live under
  `.ffsake/cli/` with `0700`/`0600`, canonical-root binding, one-hour TTL,
  traversal rejection and a 16 MiB cap.
- Model-free evidence: zero MCP catalog bytes; `read_files` 581 vs 560 bytes,
  `preview_files` 967 vs 919, `apply` 109 vs 66 versus MCP. The `serve` protocol
  measured 85 ms median per round trip versus 209 ms for an equivalent shell
  pipeline, with 46 ms startup.
- Projected turns: `edit_batch` 4 versus 10 MCP, `debug_ledger` 5 versus 9,
  `debug_nested` 5 versus 7, if the agent uses one command per step.
- Limits: persistent debug sessions, task jobs, Git review/mutate handles and
  match-set replacements require `serve` or MCP because handles are
  process-local. The one-shot mode is not a new permission authority.
- Remaining cost: the host must be told the CLI exists. That guidance and
  adoption are what the next paid batch must measure.

### R4. Honest measurement

- Freeze candidate and verifier before scoring.
- Use the generalized runtime-evidence check above; report artifact completion
  separately.
- Evaluate unseen tasks, including one multi-file edit and one debug requiring
  multiple stops or expansion.
- Report per fixture: calls, elapsed, cached/uncached input, output, cost,
  failures, timeouts and native fallback.

## Remaining gap and decision

After the implemented consolidations, projected MCP calls are 130 vs 113, with
all 17 remaining extra turns attributable to `ToolSearch`. Core context is
21.8 KB and default `all` is 37.9 KB. R3 is now implemented: the CLI projects
4-5 turns per task with zero catalog, below native's observed 6-10. The
remaining question is adoption, not capability: can the host guidance make an
agent choose the CLI? That is the next paid measurement, alongside whether the
lean core catalog removes `ToolSearch`.
