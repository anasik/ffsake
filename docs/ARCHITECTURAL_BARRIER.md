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

## Three-arm measurement (2026-11-05)

The 30-attempt validation compared native, curated core MCP and the no-MCP CLI
on two previously unused tasks: `edit_multi` and `debug_expand`. Five attempts
per arm per task, counterbalanced, $2.9466 total. All 30 attempts were valid;
all five artifacts passed and workflow evidence passed in every arm.

| Task / arm | Calls | Seconds | Total input | ToolSearch | CLI adopted |
| --- | ---: | ---: | ---: | ---: | ---: |
| Edit native | 7 | 18.1 | 155,698 | 0 | 0/5 |
| Edit MCP | 8 | 17.8 | 195,919 | 4/5 runs | 0/5 |
| Edit CLI | 7 | 17.3 | 158,212 | 0 | 0/5 |
| Debug native | 7 | 16.1 | 186,880 | 0 | 0/5 |
| Debug MCP | 10 | 22.2 | 274,364 | 5/5 runs | 0/5 |
| Debug CLI | 11 | 24.4 | 248,492 | 0 | 5/5 |

Median paired ratios versus native: MCP **1.092 elapsed / 1.394 input**; CLI
**1.190 elapsed / 1.268 input**. Native remained fastest and lowest-input
overall.

The CLI result is decisive against the last product-only hypothesis:

- On `edit_multi` the agent did not use the CLI at all. It used native Bash
  heredocs and `Write` despite explicit CLI guidance. The edit arm therefore
  measures prompt overhead, not CLI behavior.
- On `debug_expand` the agent adopted the CLI in all five runs, but still mixed
  native `Read`, `pdb` and repeated test commands. The CLI probe did not replace
  exploration or verification turns.
- The MCP arm paid one `ToolSearch` per debug run plus extra workspace/read
  turns.

Zero catalog cost did not produce turn parity. The dominant variable is host and
model tool choice, not FFSAKE's server latency, schema size or missing
capability.

## Forced-guarded policy (2026-11-06)

Disabling native `Read`, `Edit`, `Write`, `Glob` and `Grep` in the MCP arm
produced the cleanest comparison yet. Ten attempts on `edit_multi`, five native
and five forced MCP, all correct:

| Arm | Correct | Calls | Seconds | Total input |
| --- | ---: | ---: | ---: | ---: |
| Native | 5/5 | 7 | 16.8 | 135,707 |
| Forced MCP | 5/5 | 7 | 18.9 | 197,998 |

Median paired ratios: **1.118 elapsed, 1.454 input**. Calls matched exactly, yet
the guarded arm paid about **9,000 more input tokens per turn**. The catalog is
20.9 KB (about 5.2k tokens per request), and the guarded `read_files` and
combined preview diffs are larger than native Bash output. This isolates
per-turn context cost from turn count.

A five-attempt forced-CLI follow-up disabled the same tools and used the
zero-catalog CLI. Only **2/5 artifacts passed**; two runs made no edit, and the
passing runs used Bash heredocs or a mix of CLI and shell writes. Forcing the
CLI path is not a viable policy.

The measured conclusion is now stronger than a projection: native tools remain
necessary for reliable completion, and FFSAKE is not competitive on elapsed or
input even when its path is forced. The remaining levers are per-turn payload
and host/model tool policy, not a missing capability.

## Cheapest model and compact catalog (2026-11-06)

All paid tests used Claude Haiku 5.5, the cheapest available Claude model.

| Study | Native | Guarded | Median ratios |
| --- | --- | --- | --- |
| Debug three-arm | 10 calls / 20.4 s / 175k | MCP 12 / 24.2 / 382k | 1.185 elapsed / 2.060 input |
| Debug three-arm | 10 / 20.4 / 175k | CLI 10 / 22.2 / 241k | 1.034 / 1.208 |
| Edit noninteractive | 15 / 20.5 / 174k | MCP 13 / 21.9 / 242k | 1.002 / 1.393 |
| Edit noninteractive | 15 / 20.5 / 174k | CLI 14 / 19.8 / 174k | 0.910 / 1.052, 0/5 adopted |

A new `compact` profile advertised four multiplexed tools plus access tools:
**3,827 bytes / 7 tools** instead of 20,929 bytes / 28. It reduced fixed context
but caused repeated generic calls: edit 1.128 elapsed / 1.421 input (4/5
correct); debug **1.929 / 2.616** with 22 calls versus 11 native. The model paid
more turns than the catalog saved.

A hybrid profile kept compact read/change tools but restored the granular
one-call `debug_probe` (**5,979 bytes / 7 tools**). It avoided the call explosion
and was the best guarded variant: **1.139 elapsed / 1.592 input**, 14 calls
versus native 11, all 5/5 correct. It still lost on both criteria.

A final native-like CLI surface (`read`, `edit`, `apply`, `probe`) achieved
5/5 adoption and 5/5 correctness on both tasks, but measured **1.214/1.520** on
edits and **1.435/2.108** on debug. Forcing it cut calls by 19-29% and brought
debug to elapsed parity (0.998) but input was still 1.189.

Trimming the payload then produced the first measured win: compact probe
`values`, ranged reads and forced CLI on `debug_expand` measured **0.838 elapsed
/ 0.923 input**, 8 calls versus 14, **5/5 correctness versus native 4/5**. A
second edit trim (compact preview/apply output, shorter guidance) did not help:
edit measured **1.436/1.872** with 10 calls versus 15, both 5/5. A single-call
batch preview plus one apply also failed: **1.249/2.023**, 9 calls versus 15,
both 5/5. Range-capped reads and line-addressed edits were worse still:
**1.955/2.623** with 15 calls versus 14. A split workflow (native edits, CLI
reads/probe) held the debug target at **0.985/0.888** but not edits
(**1.064/1.195**; CLI reads were not adopted). Forcing the CLI read path while
keeping native edits made it worse: **1.426/1.444**, 4/5 correct, despite 19%
fewer calls. Removing CLI guidance to cut overhead collapsed correctness to
**1/5** (1.428/1.506): the model could not discover the read path. The edit path
therefore has no measured configuration that meets the target—guidance adds
overhead, and without it correctness fails. Reviewed edits and per-turn
CLI/model behavior, not metadata size, call count, address scope or read-tool
choice, dominate. A retained-trace verifier fix for compact
`values` raised CLI debug from 0/5 to 5/5 without a rerun.

Cheaper models did not make FFSAKE competitive, and shrinking the catalog alone
did not either. The remaining overhead is the model's tool-use behavior plus
guarded payloads; native tools remained fastest and lowest-input in every fair
comparison.

## Fresh-task permission barrier (2026-11-06)

The CLI-first integration was tested on two previously unused fixtures under host
permission profiles rather than skip-all approvals: `edit_units` (three-file unit
conversion) and `debug_cursor` (multi-page cursor). Native allowed
`Bash,Read,Edit,Write,Glob,Grep`; the CLI arm allowed `Bash,Edit,Write` and
intended to remove native reads. Twenty Haiku attempts, $0.1129.

| Task / arm | Correct | Artifact | Calls | Seconds | Input | Denials |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Edit native | 4/5 | 4/5 | 14 | 17.0 | 68k | 0-1 |
| Edit CLI | 1/5 | 1/5 | 20 | 30.8 | 155k | 3-6 |
| Debug native | 4/5 | 5/5 | 12 | 19.0 | 100k | 0-2 |
| Debug CLI | 0/5 | 0/5 | 18 | 32.8 | 171k | 2-6 |

The host did not reliably enforce the intended tool restriction: native Read
still appeared, and the CLI arm accumulated more permission denials despite
fewer configured scopes. The CLI-first permission story therefore reduces setup
artifacts but does not reduce runtime friction or preserve correctness on fresh
work.

This is the concrete barrier: FFSAKE cannot control host tool selection or
auto-approval granularity from inside the product. A host plugin/extension that
exposes the scoped operations as first-class tools (with native read/edit
disabled and edits auto-approved) would be needed to test the intended workflow.
That requires host-side integration work outside this repository; without it,
native tools remain faster, lower-input and more correct on unfamiliar tasks.

## Host hook enforcement result (2026-11-06)

A real Claude Code `PreToolUse` hook was implemented and installed for the CLI
arm. It denies native `Read`/`Glob`/`Grep` and file-reading shell commands,
allows `Edit`/`Write`, and allows `ffsake`, program and test commands. Model-free
hook tests pass. Twenty Haiku attempts on fresh tasks, $0.1637, all 5/5 correct.

| Task / arm | Calls | Seconds | Input | Denials |
| --- | ---: | ---: | ---: | ---: |
| Edit native | 13 | 18.0 | 174,685 | 0-1 |
| Edit hooked CLI | 19 | 25.5 | 327,241 | 8-11 |
| Debug native | 10 | 17.9 | 172,998 | 0-2 |
| Debug hooked CLI | 17 | 29.7 | 399,732 | 7-11 |

Enforcement worked, but the agent retried denied reads 7-11 times before
switching to the CLI. `--tools`/`--allowedTools` did not remove native Read.
Deny-only host hooks therefore add friction and cost; native remains faster and
lower-input on fresh work.

The concrete missing capability is a host plugin or input-rewriting hook that
exposes a first-class scoped read tool and removes native Read without a denial
loop. That host integration is outside this repository’s current boundary.

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
- Projected turns were 4-5 per task, but the measured three-arm run showed the
  CLI was not adopted for `edit_multi` (0/5) and, when adopted for
  `debug_expand` (5/5), did not reduce total turns or elapsed time because the
  agent still mixed native reads, `pdb` and repeated verification.
- Limits: persistent debug sessions, task jobs, Git review/mutate handles and
  match-set replacements require `serve` or MCP because handles are
  process-local. The one-shot mode is not a new permission authority.
- Remaining cost: the host must be told the CLI exists. That guidance and
  adoption are what the next paid batch must measure.

### R4. Honest measurement (harness integrated)

- Freeze candidate and verifier before scoring.
- Use the generalized runtime-evidence check above; report artifact completion
  separately.
- The harness now supports a third arm: `rc_benchmark.py --cli-binary` runs
  native, core MCP and CLI counterbalanced, five attempts per arm per task.
  Per-run trace now records `toolsearch_calls`, `mcp_calls`, `cli_calls` and
  `cli_adopted`; CLI debug evidence is checked from probe output
  (`cli_probe`, `cli_cleanup`) in addition to artifact verification.
- Two fresh unseen tasks are prepared and preflighted: `edit_multi` (three-file
  billing validation/serialization/handler fix) and `debug_expand` (tier-boundary
  bug requiring at least two loop iterations; `stops=2` observes `index=1`).
  Reference fixes pass and initial fixtures fail for both.
- `rc_benchmark.py --dry-run` validates the CLI binary, MCP catalog
  (20,929 bytes/28 tools), Claude CLI, CLI read preflight and the exact 30-attempt
  three-arm plan without model calls.
- Report per fixture and arm: calls, elapsed, cached/uncached input, output,
  cost, failures, timeouts, native fallback, ToolSearch count and CLI adoption.

## Remaining gap and decision

The three-arm, forced-policy, cheapest-model and compact-catalog results close
the product-only redesign path. Consolidations reduced catalog and response
size, the CLI removed catalog cost entirely, and the compact profile cut the
catalog to 3.8 KB. Forcing tool availability either left calls equal with 45%
more input or degraded completion to 2/5; the compact profile traded bytes for
more calls (up to 2.6x input on debug); the cheapest model produced the same or
worse ratios. The remaining gap is host/model tool choice and guarded payload,
not a missing FFSAKE capability.

A further product-only change would have to force a tool choice or collapse
diagnosis, edit and verification into one opaque command. The first requires
host policy (for example, removing native read/write tools for repository files
in the evaluated arm); the second would weaken reviewed edits and stale-state
separation, which this work will not do. The goal therefore remains **not met**
on the measured criteria, and the blocker is a host-behavior decision plus a
new paid budget, not an unimplemented FFSAKE capability.
