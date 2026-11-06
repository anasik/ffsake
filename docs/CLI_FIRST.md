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

Final 10-repetition run per task (`final-40.json`), native versus redirected
reads + native edits + one-call probe:

| Task | Native | FFSAKE | Paired median |
| --- | ---: | ---: | --- |
| Edit correctness | 10/10 | **10/10** | — |
| Edit cost | $0.00614 | $0.00620 | +1% |
| Edit elapsed | 16.5 s | 17.1 s | 1.075 |
| Edit input | 156,682 | 157,229 | 1.075 |
| Debug correctness | 7/10 | **10/10** | — |
| Debug cost | $0.00694 | **$0.00655** | **-5.6%** |
| Debug elapsed | 20.2 s | 21.0 s | 1.038 |
| Debug input | 201,868 | 215,081 | 1.196 |

Native debug failures were workflow failures: the agent fixed the program
without observing the two required page iterations. The FFSAKE probe returns the
observed values in one call and cleans up, so all ten runs satisfied the
workflow. Runtime permission denials were 0-2 in both arms; the earlier
deny-only hook produced 7-11 retries per run and is no longer used.

An earlier 20-attempt run (`earlier-20.json`) measured edit 1.056 elapsed /
0.998 input and debug 0.898 / 0.856. Across both runs the honest claim is
**parity on edit efficiency, parity-to-better on debug, and better debug
correctness and cost**; token and elapsed differences are within run-to-run
variance.

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
