# CLI-first integration

FFSAKE can be installed without registering an MCP server or a tool catalog.
This is the lowest-friction integration for hosts that already permit shell
commands, and with the Claude read-redirect hook it also gives the host a
single governed read path.

## Setup

```sh
python3 scripts/ffsake_cli_setup.py --host claude --project /path/to/repo \
  --redirect-reads --apply
python3 scripts/ffsake_cli_setup.py --host opencode --project /path/to/repo \
  --redirect-reads --apply
```

Claude Code writes:

- one guidance block in `CLAUDE.md` (or `AGENTS.md`);
- `.claude/ffsake-read-policy.json`, a `PreToolUse` hook that serves native
  `Read` through `ffsake read` and returns the text through `updatedInput`;
- the single host permission rule for shell access: `Bash(ffsake:*)`.

OpenCode writes the same guidance block plus:

- `.opencode/plugins/ffsake-read-redirect-<workspace-id>/index.js`, a local
  plugin that replaces the native `read` executor with `ffsake read` and
  rejects file-reading shell commands;
- `.opencode/opencode.json` with the permission-minimal allowlist
  (`bash`, `read`, `edit`, `write`, and native search tools).

Run Claude Code with the policy, or start OpenCode in the project:

```sh
claude --settings .claude/ffsake-read-policy.json ...
opencode
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

Measured without skip-all: native defaults versus the CLI arm with the redirect
hook and a three-entry allowlist in the same settings file.

| Artifact | MCP core | Native defaults | CLI + redirect |
| --- | ---: | ---: | ---: |
| Host config files | `.mcp.json` / host config | host settings | one settings file |
| Advertised operations | 28 tools | 6 tools | 3 tools + native `Read` |
| Per-tool approval entries | up to 28 | 6 | **3** |
| Read path | MCP read tool | native `Read` | native `Read` rewritten to ffsake |
| Runtime denials per run | n/a | 0–4 | **0–1** |
| Catalog bytes | 20.9 KB | 0 | **0** |

The settings file contains:

```json
{"permissions": {"allow": ["Bash", "Edit", "Write"]}}
```

plus the `Read` hook. Native `Read` is still available to the model, so edit
turns do not grow; the hook serves it through FFSAKE. Native `Edit`/`Write` stay
available, and native `Glob`/`Grep` are not in the allowlist.

## Measured trade-offs (Claude Haiku 5.5)

Final 80-attempt run on four fresh tasks (`final-80.json`), ten repetitions
each, no skip-all:

| Task group | Correct | Pooled elapsed | Pooled input | Pooled cost |
| --- | ---: | ---: | ---: | ---: |
| Edit (20 pairs) | 20/20 both | 1.024 | **0.946** | 1.016 |
| Debug (20 pairs) | **20/20 vs 15/20** | **0.766** | **0.655** | **0.739** |

Native debug failures were workflow failures: the agent fixed the program
without observing the required runtime iterations. The FFSAKE probe returns the
observed values in one call and cleans up.

The CLI still enforces workspace access, reviewed preview/apply, stale-state
checks, undo/redo and owned-adapter cleanup. The hook only changes where read
content comes from; it does not bypass denial or approval checks.

## Second host

OpenCode 2.2.5 + Claude Haiku 5.5, same fixtures. The first five-attempt run
used CLI guidance only, before a redirect plugin existed:

| Task (guidance only) | Native | FFSAKE CLI | Paired median |
| --- | ---: | ---: | --- |
| Edit units | 5/5 | **5/5** | 0.967 elapsed / 1.272 input |
| Debug cursor | 3/5 | **5/5** | **0.848 elapsed / 0.832 input** |

The OpenCode v2 read-redirect plugin was then measured with ten attempts per
task, 40 valid runs:

| Task (redirect plugin) | Native | FFSAKE CLI | Paired median | Calls (native/CLI) |
| --- | ---: | ---: | --- | ---: |
| Edit units | 10/10 | **10/10** | **1.258 elapsed / 1.132 input** | 7 / 14 |
| Debug cursor | 8/10 | **10/10** | **0.914 elapsed / 0.943 input** | 11 / 9.5 |

Pooled medians were 1.075 elapsed / 1.043 input, with zero permission denials in
both arms. Debug correctness and efficiency transfer; edit does not, because the
redirect turns each read into a shell-shaped turn and the model used about twice
the calls. The plugin is an enforcement and correctness mechanism, not an edit
speedup. OpenCode reported zero cost, so only elapsed, input and correctness are
comparable.

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
