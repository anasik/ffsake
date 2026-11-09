# FFSAKE CLI shim

`ffsake` is the host-visible path for agents that can run shell commands but
should not pay the MCP tool-catalog or tool-discovery cost. It reuses the same
`Resident` request surface, access policy, stale-state checks and journal as the
MCP server.

The install and release scripts already expect `ffsake` and `ffsake-mcp`; this
binary restores the former while adding a one-shot mode for shells that cannot
keep a process open.

## One-shot mode

```sh
# Read several files in one process
ffsake . --request '{"op":"read_files","files":[{"path":"a.py"},{"path":"b.py"}]}'

# Preview edits; prints a diff and a private token; writes nothing
ffsake . --request '{"op":"preview_files","operations":[
  {"op":"edit","path":"a.py","old_text":"a","new_text":"A","expected_matches":1}
]}'

# Apply exactly that reviewed preview; replays the request against current
# files, so a changed file rejects instead of applying stale content
ffsake . --apply <token>

# History and read-only Git
ffsake . --request '{"op":"undo","transaction":"<id>"}'
ffsake . --request '{"op":"git","query":"status"}'

# One-shot or multi-stop debug diagnosis; cleans up the owned launch
ffsake . --debug .ffsake/debug.json --request '{"op":"debug_probe",
  "configuration":"app","breakpoints":[{"path":"main.py","lines":4}],"stops":2}'

# Semantic rename through an installed language server: preview, review, apply
ffsake . --request '{"op":"preview_semantic_rename",
  "request":{"files":["src/lib.rs"],"path":"src/lib.rs",
    "position":{"line":5,"column":7},"query":"rename",
    "context":{"language":"rust","crate_root":"src/lib.rs","edition":"2021"},
    "new_name":"sum_all","limit":100},
  "expected_path":"src/lib.rs","expected_start_byte":85,
  "expected_old_text":"total","expected_new_name":"sum_all","expected_edits":1}'
ffsake . --apply <token>
```

`preview_semantic_rename` runs discovery and preview in one process, stores the
discovery request and its analysis-source hashes in the token, and rewrites the
reviewed proposal on apply. Apply repeats discovery, rejects changed analysis
sources or a different proposal, and then commits one journal transaction.
`preview_rust_rename` still needs a resident `rename_set`; use `ffsake serve` or
MCP for that form. Same-spelling locals and parameters are not renamed.

Output is one JSON object: `{"ok":true,"result":...}` or
`{"ok":false,"error":"..."}`. Each command is one host tool call and does not
require an MCP handshake.

## Preview tokens

`preview_edit`, `preview_files`, `preview_create`, `preview_copy`,
`preview_move`, `preview_delete` and `preview_rename` return a token under
`.ffsake/cli/`.

- The directory is created `0700`; tokens are `0600`.
- Token names are bounded hex/dash strings; traversal is rejected.
- A token is bound to the canonical workspace root and expires after one hour.
- `--apply` replays the stored request, which rechecks file hashes and match
  counts before applying, then deletes the token.
- A failed apply does not weaken the stale-state guard; preview again.

This preserves the reviewed-edit flow: preview and apply remain two separate
host turns, and no command applies unreviewed content.

## Persistent JSONL mode

`ffsake serve WORKSPACE [--access FILE] [--debug FILE] [--tasks FILE]` keeps one
process and its handles alive for hosts that can hold stdin/stdout. It accepts
one newline-delimited request per line and returns one flushed response per
request, as documented in [CLI.md](CLI.md). This retains MatchSet, preview,
debug-session and task handles across requests. EOF exits.

## Guarantees and limits

- Authorization uses the same `--access` export or persistent project grants;
  denied paths fail without exposing content.
- Preview/apply/history reject changed contents and permissions.
- Debug probes terminate the owned adapter process group; persistent debug
  sessions remain available in `serve` and MCP modes.
- Git review/mutate handles, task jobs and long-lived debug sessions are
  process-local; use `serve` or MCP when handles must cross host turns.
- `preview_matches` still needs the MatchSet from the same process; use `serve`
  or the MCP server for match-set replacements.
- The CLI is not a new permission authority; it does not bypass access checks.
- No TCP listener, daemon, shell interpolation or implicit execution is added.

## Model-free measurements

On the retained edit fixture, one-shot CLI responses are close to MCP:
`read_files` 581 vs 560 bytes, `preview_files` 967 vs 919, `apply` 109 vs 66.
The CLI adds **zero** MCP catalog bytes. The `serve` protocol measured 85 ms
median per round trip versus 209 ms for an equivalent shell pipeline over 10
iterations, with 46 ms startup.

Projected turns if an agent uses one command per step:

| Task | Native observed | MCP projected | CLI projected |
| --- | ---: | ---: | ---: |
| `edit_batch` | 7–10 | 10 | **4** (read, preview, apply, verify) |
| `debug_ledger` | 6–8 | 9 | **5** (read, probe, preview, apply, verify) |
| `debug_nested` | 6–9 | 7 | **5** (read, probe, preview, apply, verify) |

These were projections. The 30-attempt three-arm validation later measured
**0/5 CLI adoption on `edit_multi`** (the agent used native heredocs/Write) and
**5/5 adoption on `debug_expand`**, where the CLI still mixed native reads,
`pdb` and repeated verification and ended slower than native. Median paired
ratios versus native were 1.190 elapsed / 1.268 input for the CLI and 1.092 /
1.394 for MCP. Native remained fastest and lowest-input; see
[BENCHMARK.md](BENCHMARK.md#three-arm-cli-validation-2026-11-05).
