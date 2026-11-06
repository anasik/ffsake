# CLI-first integration

FFSAKE can be installed without registering an MCP server. This is the
lowest-friction integration for hosts that already permit shell commands.

## Setup

```sh
python3 scripts/ffsake_cli_setup.py --host claude --project /path/to/repo --apply
```

This writes one guidance block to `CLAUDE.md` (or `AGENTS.md`) and prints the
single host permission rule:

```
Bash(ffsake:*)
```

No `.mcp.json`, MCP server entry, tool catalog, or per-tool approval list is
created. The model uses the installed `ffsake` executable through Bash:

```sh
F="$HOME/.local/share/ffsake/current/ffsake"
"$F" read "$PWD" src/lib.rs:10-40 src/runtime.rs:1-60
"$F" probe "$PWD" --debug .ffsake/rc-debug.json --config app --breakpoint main.py:12 --stops 2
"$F" edit "$PWD" src/lib.rs --old 'old' --new 'new'
"$F" apply "$PWD" <token>
```

## Permission comparison

| Artifact | MCP core | CLI-first |
| --- | ---: | ---: |
| Host config files | `.mcp.json` / host config | guidance block only |
| Advertised operations | 28 tools | one executable |
| Per-tool approval entries | up to 28 | **0** |
| Host permission scopes | per MCP tool | **one** `Bash(ffsake:*)` rule |
| Catalog bytes | 20.9 KB | **0** |
| Workspace access policy | optional `--access` | same optional policy |
| Debug policy | optional `--debug` | same optional policy |

The CLI still enforces workspace access, reviewed preview/apply, stale-state
checks, undo/redo and owned-adapter cleanup. Native Edit/Write remain available
for ordinary code changes; the CLI is the scoped path for reads and one-call
debugging.

## Measured trade-offs

- Debug: best Haiku runs measured **0.838 elapsed / 0.923 input** and
  **0.985 / 0.888**, 5/5 versus native 4/5. A later batch measured 1.052 / 1.141,
  so the advantage is not stable across every sample; the median of the three
  debug comparisons is about parity-to-better.
- Edits: reviewed CLI edits lost (best 1.176 / 1.445). Native edits with no CLI
  guidance measured input parity (1.004) and noisy elapsed (1.164). Reviewed-edit
  output and model turn behavior remain the barrier.
- Correctness: 5/5 across native, MCP and CLI arms in the final comparisons, with
  FFSAKE guards still tested by `scripts/cli_smoke.py`.

## When to keep MCP

Use MCP when you need the richer tool set: Git review/mutation, persistent task
jobs, Rust/LSP intelligence, or persistent debug sessions with cross-turn
handles. CLI-first is the lower-friction default for scoped reads, reviewed edits
and one-shot debugging.
