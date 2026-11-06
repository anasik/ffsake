# CLI-first integration

FFSAKE can be installed without registering an MCP server or a tool catalog.
This is the lowest-friction integration for hosts that already permit shell
commands, and with the Claude read-redirect hook it also gives the host a
single governed read path.

## Setup

```sh
python3 scripts/ffsake_cli_setup.py --host claude --project /path/to/repo \
  --redirect-reads --apply
```

This writes:

- one guidance block in `CLAUDE.md` (or `AGENTS.md`);
- `.claude/ffsake-read-policy.json`, a `PreToolUse` hook that serves native
  `Read` through `ffsake read` and returns the text through `updatedInput`;
- the single host permission rule for shell access: `Bash(ffsake:*)`.

Run Claude Code with the policy:

```sh
claude --settings .claude/ffsake-read-policy.json ...
```

No `.mcp.json`, MCP server entry, tool catalog or per-tool approval list is
created. Native `Edit`/`Write` stay available because they are the efficient,
reliable path for ordinary code changes. Native `Read` still works for the
model, but its content comes from FFSAKE's workspace policy.

Commands are written with a literal `.` workspace and no variables or quotes so
the host's static read block can parse them:

```sh
~/.local/share/ffsake/current/ffsake read . src/lib.rs:10-40 src/runtime.rs:1-60
~/.local/share/ffsake/current/ffsake probe . --debug .ffsake/rc-debug.json \
  --config app --breakpoint main.py:12 --stops 2
```

## Permission comparison

| Artifact | MCP core | CLI + redirect |
| --- | ---: | ---: |
| Host config files | `.mcp.json` / host config | one settings file |
| Advertised operations | 28 tools | one executable + native tools |
| Per-tool approval entries | up to 28 | **0** |
| Host permission scopes | per MCP tool | **one** `Bash(ffsake:*)` rule |
| Catalog bytes | 20.9 KB | **0** |
| Read path | MCP read tool | native `Read` rewritten to ffsake |
| Workspace access policy | optional `--access` | same optional policy |
| Debug policy | optional `--debug` | same optional policy |

The CLI still enforces workspace access, reviewed preview/apply, stale-state
checks, undo/redo and owned-adapter cleanup. The hook only changes where read
content comes from; it does not bypass denial or approval checks.

Add `.ffsake/` to the project's `.gitignore`; that directory holds the optional
debug configuration and hook read cache.

## Measured trade-offs (Claude Haiku 5.5)

Final 10-repetition runs per task, native versus redirected reads + native edits
+ one-call probe. Edit shapes: `edit_units`, `edit_settings` and the multi-file
rename `edit_refactor`. Debug pairs: `debug_cursor` and `debug_retry`.

| Task | Native | FFSAKE | Pooled median |
| --- | ---: | ---: | --- |
| Edit correctness | 30/30 | **30/30** | — |
| Edit elapsed | 15.8–19.1 s | 16.4–18.8 s | 1.038 |
| Edit input | 142,514–174,323 | 156,571–173,954 | **1.003** |
| Edit cost | $0.00592–$0.00670 | $0.00615–$0.00655 | **1.015** |
| Debug correctness | 17/20 | **20/20** | — |
| Debug elapsed | 15.6–20.7 s | 14.6–15.3 s | **0.806** |
| Debug input | 170,198–203,481 | 140,386–140,649 | **0.826** |
| Debug cost | $0.00605–$0.00707 | $0.00522–$0.00528 | **0.867** |

The debug arm won 16/20 elapsed pairs and 17/20 input pairs. Native failures
were workflow failures: the agent fixed the program without observing the
required runtime iterations. The FFSAKE probe returns observed values in one
call and cleans up, so the workflow requirement is part of the normal path.

Runtime permission denials were 0–2 for FFSAKE and 0–4 for native; the earlier
deny-only hook produced 7–11 retries per run and is retired.

Two guidance details matter and are included in the setup block: combine simple
commands in one Bash call, and always run one probe before editing. An earlier
fair run without the mandatory probe was 18/20 because two runs skipped it.

Fit within run-to-run variance: edit input/cost are at parity across three
shapes (including a multi-file rename), edit elapsed is +3.8%, and debug is
clearly better on all three efficiency measures while also being more correct.

## Measurement correction

The file audit originally treated Python bytecode caches (`__pycache__/*.pyc`)
as unexpected repository changes, and the verification-time audit treated caches
created by the verifier as agent changes. That failed otherwise-correct runs.
`agent_benchmark_suite.transient_artifact` now ignores bytecode and tool caches;
`reevaluate_rc_results.py` recomputed retained traces without model calls.

## When to keep MCP

Use MCP when you need the richer tool set: Git review/mutation, persistent task
jobs, Rust/LSP intelligence, or persistent debug sessions with cross-turn
handles. CLI + redirect is the lower-friction default for scoped reads, native
edits and one-shot debugging.
