# Agent benchmark evidence

## 2026-10-30 Claude measured matrix

- The 50 surviving Claude Code session transcripts are retained in local salvage storage. They were removed from the public tree and reachable branch history because raw sessions contained personal information. GitHub still served the old commit by ID after the rewrite; cached objects and copies require separate cleanup.
- `rc-claude-20261030-2.reconstructed.json` is derived from those transcripts. It records the run order, role, prompt, reported cost, token components, tool calls and preserved correctness outcome. It is not the original harness JSON.
- `SHA256SUMS` records the public reconstructed summary.

The original consolidated JSON and harness logs were written under `/private/tmp/ffsake-onboarding`, which macOS removed before the report was committed. The Claude Code transcripts under `~/.claude/projects` survived because they live outside `/private/tmp`; they were retained in local salvage storage. Cost, token, tool-call, adoption and correctness totals in the reconstructed summary match the pre-cleanup aggregate exactly. Per-run harness wall time and verifier metadata (changed-file lists and exact audit fields) were not recovered. The preserved aggregate correctness is 24/25 native and 25/25 FFSAKE, with one native cross-file run editing `README.md` outside its allowed change set. See `docs/BENCHMARK.md` for the full method and caveats.

The local transcripts contain prompts, model output, tool calls, account email and absolute paths. A pattern scan found no obvious API keys or private keys; it is not a complete secret audit. They require sanitization before publication.

## Reproduce

Run from the repository root with Claude Code installed, the MCP binary built, and a debugpy-enabled Python for the debug task:

```sh
python3 scripts/rc_benchmark.py \
  --binary target/release/ffsake-mcp \
  --output /private/path/to/rc-claude-REPLACE.json \
  --agents claude \
  --tasks cross,maintenance,build,git,debug \
  --repetitions 5 \
  --debug-python /path/to/debugpy/venv/bin/python
```

A fresh run writes a complete harness JSON with verifier metadata. It will use the current Claude Code version and may not reproduce the 2026-10-30 sample exactly. Codex and OpenCode were not part of this matrix; see the limitations in `docs/BENCHMARK.md`.

## 2026-11-01 Codex measured matrix

The [cross](20261101-codex/cross.json), [maintenance](20261101-codex/maintenance.json), [build](20261101-codex/build.json), and [debug](20261101-codex/debug.json) files contain per-run metrics and a content-free redacted event index. They omit prompts, commands, arguments, tool output, session IDs and paths to protect privacy; they cannot independently establish every workflow observation. Original transcripts and verification details remain in local private storage. [Model-free safety outcomes](20261101-codex/safety.json) and [one setup timing](20261101-codex/setup.json) are separate. See [methodology and results](../BENCHMARK.md#release-candidate-codex-comparison-2026-11-01).

## 2026-11-01 Cross-file optimization

The [sanitized follow-up index](20261101-codex/cross-optimization.json) retains per-run metrics and content-free events. The [model-free debugger probe](20261101-codex/debug-poll-probe.json) contains only synthetic observations and timings. Full transcripts, verifier records and frozen binaries remain in local private storage. See the [audit and comparison](../BENCHMARK.md#cross-file-interaction-optimization-2026-11-01).

## 2026-11-02 Interaction overhead

The [diagnostic index](20261102-performance/diagnostic-cross.json) contains five runs per condition with numeric results and content-free MCP timings. The [cross-file](20261102-performance/comparison-cross.json), [maintenance](20261102-performance/comparison-maintenance.json) and [corrected debugger](20261102-performance/corrected-debug-changed.json) indexes preserve sanitized per-run evidence; the full table links each debugger arm. The [diagnostic freeze](20261102-performance/diagnostic-freeze.json) and [comparison freeze](20261102-performance/comparison-freeze.json) identify the inputs without publishing session transcripts. The [debugger probe](20261102-performance/debug-poll-vs-wait.json) contains synthetic observations and timings. Raw transcripts and binaries are retained in private local storage; public indexes omit prompts, tool arguments, outputs, paths and session IDs. See [methodology and results](../BENCHMARK.md#interaction-overhead-study-2026-11-02).

## 2026-11-04 Combined stop inspection

The [inspection index](20261104-inspection/inspection.json) contains 31 per-run records for payroll and shipping: five valid attempts per mode plus the provider-failed old payroll attempt and its supplemental retry, and five shipping attempts per mode. It includes numeric timing/token/call metrics, correctness and content-free debug-action sequences, but no prompts, arguments, tool output, paths or session IDs. The [prior-fix](20261104-inspection/prior-final.json) and [result-fix](20261104-inspection/prior-resultfix.json) indexes preserve the corrected all-attempt accounting for the two earlier comparison reports. All three indexes use the same runtime-value workflow check; no model run was repeated for these corrections. The [continue-thread setup-failure index](20261104-inspection/continue-thread-setup-failure.json) preserves five paid attempts that never reached a working debugger; they are marked invalid and are not overhead evidence. The [corrected index](20261104-inspection/continue-thread-corrected.json) records five valid attempts after the adapter-path preflight passed; all passed runtime evidence, but the inference was not exercised. See [methodology and results](../BENCHMARK.md#next-interaction-cost-candidate-2026-11-04).

## 2026-11-05 Efficiency A/B

The [efficiency A/B index](20261105-efficiency/ab.json) records the 30-attempt counterbalanced native/old/changed comparison on payroll and shipping. Changed was 5/5 correct on both fixtures; payroll changed reduced elapsed by 19.9% and input by 2.3%, shipping by 0.8% and 10.0%, so the 30% target was not met.

The [debug-source A/B index](20261105-efficiency/ab-debug-source.json) preserves the rejected follow-up candidate. It reduced input but caused changed correctness to fall to 3/5 payroll and 4/5 shipping, so the candidate was reverted.

The [one-shot probe index](20261105-efficiency/probe-step.json) records the 20-attempt native-versus-probe comparison. Probe changed was 5/5 correct on both fixtures, payroll 16.9 s versus native 16.5 s, shipping 13.3 s versus native 14.2 s; input remained about 28–32% above native.

The [near-native probe index](20261105-efficiency/probe-lines.json) records the final 20-attempt native-versus-probe comparison. Changed was 5/5 correct on both fixtures; payroll measured 16.9 s versus native 17.2 s and shipping 12.1 s versus native 13.6 s.

## 2026-11-05 Generalization debug

The [generalization index](20261105-generalization/debug.json) records 30 counterbalanced paid attempts on three previously unused debug fixtures. Artifact correctness was 5/5 in both arms on every fixture. Harness completion was 7/15 for FFSAKE versus 6/15 native; overall median paired ratios were 1.067 elapsed and 1.282 input, so the efficiency criteria were not met.

## 2026-11-05 Final held-out generalization

The [final index](20261105-generalization/final.json) records the 30-attempt held-out comparison on `edit_batch`, `debug_ledger` and `debug_nested`. Artifact completion was 15/15 in both arms, but harness completion was 10/15 FFSAKE versus 12/15 native and median paired ratios were 1.268 elapsed and 1.127 input. The goal was not met; the pilot remains separate and is not pooled.

## 2026-11-05 Three-arm CLI validation

The [three-arm index](20261105-cli-arm/three-arm.json) records 30 counterbalanced paid attempts on `edit_multi` and `debug_expand` across native, core MCP and CLI. All arms were 5/5 correct; median paired ratios were 1.092 elapsed/1.394 input for MCP and 1.190/1.268 for CLI. The CLI was not adopted for the edit task and was slower when adopted for the debug task.

## 2026-11-06 Forced-guarded policy

The [forced-guard indexes](20261106-forced-guard/guarded-mcp.json) record a 10-attempt native-versus-forced-MCP run (all correct; 7 calls each; 1.454x input) and a 5-attempt [forced CLI](20261106-forced-guard/forced-cli.json) run (2/5 correct). Disabling native file tools did not make the guarded path competitive and the forced CLI degraded completion.

## 2026-11-06 Cheapest-model validation

The [Haiku indexes](20261106-haiku/three-arm.json) record three studies using Claude Haiku 5.5: a 30-attempt three-arm run, a 15-attempt noninteractive edit rerun, and a 20-attempt compact-catalog run. Native remained fastest and lowest-input; the compact profile reduced catalog bytes but increased calls and input.

## 2026-11-06 Hybrid guarded profile

The [hybrid index](20261106-haiku/hybrid-debug.json) records 10 Haiku attempts on `debug_expand`: compact query/change plus granular debug_probe, 5/5 correct, but 14 calls / 328,706 input versus native 11 / 206,668 (1.139 elapsed / 1.592 input).

## 2026-11-06 Single-command CLI surface

The [CLI subcommand index](20261106-haiku/cli-subcommands.json) records 30 Haiku attempts with native-like `read`/`edit`/`probe` commands. CLI was adopted 5/5 and correct 5/5 on both tasks, but remained slower and higher-input than native. A retained-trace verifier correction raised CLI debug evidence from 0/5 to 5/5 without rerunning.

## 2026-11-06 Forced native-like CLI

The [forced CLI index](20261106-haiku/forced-cli-subcommands.json) records 10 Haiku attempts with native read/write tools disabled. All were 5/5 correct; debug reached elapsed parity (0.998) with 9 calls versus 14 but used 19% more input. Edit remained 1.403 elapsed / 1.544 input.

## 2026-11-06 Payload-trimmed forced CLI

The [payload debug index](20261106-haiku/payload-debug.json) records the first target win: forced CLI on `debug_expand` at 0.838 elapsed / 0.923 input with 5/5 correctness versus native 4/5. [Payload edit](20261106-haiku/payload-edit.json) remained 1.176/1.445. A retained-trace verifier correction for compact `values` raised CLI debug from 0/5 to 5/5 without rerunning.

## 2026-11-06 Edit payload trim

The [compact edit index](20261106-haiku/payload-edit-compact.json) records a 10-attempt Haiku rerun with compact preview/apply output and ranged reads. Both arms were 5/5, but forced CLI was 1.436 elapsed / 1.872 input; the trim did not improve the edit path.

## 2026-11-06 Batch edit rerun

The [batch edit index](20261106-haiku/payload-edit-batch.json) records 10 Haiku attempts with one-call batch preview and one apply. Both arms were 5/5, but forced CLI was 1.249 elapsed / 2.023 input; batching did not improve the edit path.

## 2026-11-06 Line-addressed edit

The [line-addressed index](20261106-haiku/payload-edit-lines.json) records 10 Haiku attempts with ranged reads and `--lines` edits. Both arms were 5/5, but forced CLI was 1.955 elapsed / 2.623 input; narrowing scope increased repeated turns.

## 2026-11-06 Split workflow

The [split index](20261106-haiku/split-workflow.json) records 10 Haiku attempts using native edits and CLI reads/probe. Debug held the target (0.985 elapsed / 0.888 input, 5/5), but edits did not (1.064/1.195) because CLI reads were not adopted and guidance added overhead.

## 2026-11-06 Forced CLI reads

The [force-reads index](20261106-haiku/force-reads.json) records 10 Haiku attempts with native Read/Glob/Grep disabled and native edits kept. CLI reads were adopted, but forced CLI was 1.426 elapsed / 1.444 input with one failure (4/5).

## 2026-11-06 No-guidance forced reads

The [no-guidance index](20261106-haiku/no-guidance.json) records 10 Haiku attempts with native reads disabled and no CLI guidance. Only 1/5 was correct; the model could not discover the CLI read path. Budget is effectively exhausted.

## 2026-11-06 Task-scoped guidance and CLI-first

The [task-guidance index](20261106-haiku/task-guidance.json) records 20 Haiku attempts with CLI guidance only for debug tasks. Edit input was at parity (1.004) but the CLI was unused; debug used the CLI and measured 1.052/1.141. Across three debug batches the median is ~0.985/0.923. CLI-first setup adds one permission rule and zero tool approvals, with no MCP config.

## 2026-11-06 Fresh permission-controlled tasks

The [fresh index](20261106-permission/fresh.json) records 20 Haiku attempts on new `edit_units` and `debug_cursor` fixtures with host permission profiles. Native was faster, lower-input and more correct; the CLI arm had more denials and failed both tasks (1/5 edit, 0/5 debug).

## 2026-11-06 Host hook policy

The [hook index](20261106-permission/hook-policy.json) records 20 Haiku attempts with a PreToolUse hook denying native reads. All arms were 5/5 correct, but the CLI arm retried denied reads 7-11 times and measured 1.590/1.894 on edits and 1.250/1.669 on debug. Deny-only host enforcement adds friction.

## 2026-11-06 Read-redirect hook

The [final 40-attempt index](20261106-redirect/final-40.json) records 10 Haiku repetitions per fresh task with native `Read` rewritten through `ffsake read` and native edits kept. Edit was 10/10 both at 1.075 elapsed / 1.075 input; debug was 10/10 FFSAKE versus 7/10 native at 1.038 elapsed / 1.196 input and 5.6% lower cost. The [earlier 20-attempt index](20261106-redirect/earlier-20.json) shows edit 1.056/0.998 and debug 0.898/0.856. Bytecode caches are excluded from the file audit; retained traces were re-evaluated without model calls.

## 2026-11-06 Final redirect integration

[final-debug-40.json](20261106-redirect/final-debug-40.json) records the final 20 debug pairs: 20/20 FFSAKE versus 17/20 native, pooled median 0.806 elapsed / 0.826 input / 0.867 cost. [edit-settings-40.json](20261106-redirect/edit-settings-40.json) records the second edit pair; together with `final-40.json` edit is 20/20 both at 1.047 elapsed / 0.996 input / 0.999 cost. [debug-no-always-probe-40.json](20261106-redirect/debug-no-always-probe-40.json) retains the earlier failure mode where two runs skipped the probe.

[edit-refactor-20.json](20261106-redirect/edit-refactor-20.json) adds a third edit shape, a multi-file helper rename: 10/10 both at 1.036 elapsed / 1.009 input / 1.034 cost. With `final-40.json` and `edit-settings-40.json`, edit is 30/30 both at 1.038 / 1.003 / 1.015.

## 2026-11-06 Permission-minimal final run

[final-80.json](20261106-hook-allow/final-80.json) is the final 80-attempt run without skip-all: native 6 allowed tools versus FFSAKE 3 plus the Read hook. Edit was 20/20 both at 1.024/0.946/1.016; debug was 20/20 versus native 15/20 at 0.766/0.655/0.739, with 0-1 denials versus native 0-4. [nohook-80.json](20261106-strict/nohook-80.json) retains the no-hook alternative (3 entries, zero denials, slower edits), and [haiku-20.json](20261106-opencode/haiku-20.json) records the OpenCode second-host check.

## 2026-11-09 OpenCode read-redirect

[redirect-40.json](20261109-opencode-redirect/redirect-40.json) records 40 valid OpenCode 2.2.5 + Claude Haiku 5.5 attempts with a v2 local plugin serving native `read` through `ffsake read`. Edit was 10/10 both at 1.258 elapsed / 1.132 input with 7 native versus 14 CLI calls; debug was 10/10 versus native 8/10 at 0.914 / 0.943. Pooled 1.075 / 1.043, zero denials in both arms, provider-reported cost zero. The plugin is kept as an enforcement/correctness mechanism; the edit regression is retained. [haiku-20.json](20261106-opencode/haiku-20.json) remains the earlier guidance-only sample.

## 2026-11-09 Default CLI on generalization bottlenecks

[ledger-nested-20.json](20261109-generalization/ledger-nested-20.json) records 20 valid Claude Haiku 5.5 attempts on the two fixtures that dominated the 2026-11-05 held-out regression, using the current CLI + read-redirect integration. Ledger was 5/5 artifact and 4/5 harness both, 11 native versus 6 CLI calls, paired 0.988 elapsed / 0.684 input; nested was 5/5 artifact both and 5/5 versus native 2/5 harness, paired 0.869 / 0.797. Pooled 0.915 / 0.741, total cost $0.1254. Both are bottleneck regression fixtures, not new held-out tasks; the MCP `workspace_info` task summary remains unmeasured.

## 2026-11-09 MCP candidate on generalization bottlenecks

[mcp-ledger-nested-20.json](20261109-generalization/mcp-ledger-nested-20.json) records 20 valid Claude Haiku 5.5 attempts measuring the multi-stop probe plus `ffsake_workspace_info` task summary in the MCP arm. Ledger was 5/5 harness both at 10 calls each, paired 1.115 elapsed / 1.925 input; nested was 1/5 versus native 0/5 harness at 13 calls each, paired 1.200 / 2.008. Pooled 1.158 / 1.993 and about 50% higher median cost, total $0.1708. Calls match native, so the turn gap closed; per-turn MCP catalog and `ToolSearch` overhead remains. The CLI arm on the same fixtures is 0.915 / 0.741, so CLI stays the default.

## 2026-11-09 Edit batch under the default CLI

[edit-batch-10.json](20261109-generalization/edit-batch-10.json) records 10 valid Claude Haiku 5.5 attempts on the third 2026-11-05 held-out fixture with the current CLI + read-redirect integration. Artifact and harness were 5/5 both at 11 native versus 10 CLI calls; paired median 1.237 elapsed / 1.162 input, median cost near equal, total $0.0550. Pooled with the ledger and nested runs, 15 pairs give 0.988 elapsed / 0.935 input for $0.1805 total. These are regression fixtures, not fresh held-out tasks.

## 2026-11-09 Fresh held-out recursive debugging

[debug-recursive-10.json](20261109-generalization/debug-recursive-10.json) records 10 valid Claude Haiku 5.5 attempts on a new `debug_recursive` fixture frozen before the run: a recursive tree total missing inner-node values. Artifact was 5/5 both; harness was 5/5 CLI versus native 2/5, all native failures being missed runtime observations. Calls were 11 native versus 5 CLI; paired median 0.718 elapsed / 0.584 input, zero CLI denials versus 1-4 native, total $0.0669. One fixture and five pairs are not a broad generalization claim.

## 2026-11-09 Fresh held-out feature editing

[edit-feature-10.json](20261109-generalization/edit-feature-10.json) records 10 valid Claude Haiku 5.5 attempts on a new `edit_feature` fixture frozen before the run: implement missing pending-list and summary behavior in a small tracker package against fixed tests. Artifact and harness were 5/5 both; calls 10 native versus 9 CLI, paired median 0.997 elapsed / 0.958 input, zero denials in both arms, total $0.0534. Together with `debug-recursive-10.json`, the two fresh held-out shapes meet both efficiency criteria. The fixture is committed with the evidence; its provenance hash pins the exact code used.

## 2026-11-09 Fresh held-out batch, ten pairs each

[heldout-40.json](20261109-generalization/heldout-40.json) records 40 valid Claude Haiku 5.5 attempts across the two fresh held-out fixtures, ten pairs each, total $0.2376. Recursive debug was artifact 10/10 both, harness 9/10 CLI versus native 7/10, 11.5 versus 6 median calls, paired 0.760 elapsed / 0.734 input, native denials 0-4 versus zero CLI. Feature edit was 10/10 artifact and harness both, paired 1.022 / 0.948. Pooled 0.941 / 0.938 across 20 pairs. This supersedes the five-pair samples in `debug-recursive-10.json` and `edit-feature-10.json` for headline numbers. It omits per-run event streams to stay under the repository's 256 KiB bounded-read limit; the five-pair per-task indexes retain them.

## 2026-11-09 Edit batch at ten pairs

[edit-batch-20.json](20261109-generalization/edit-batch-20.json) records 20 valid Claude Haiku 5.5 attempts on `edit_batch` with the default CLI, ten pairs, $0.1070. Artifact and harness were 10/10 both at 11 native versus 6 CLI calls; paired median 1.044 elapsed / 0.950 input, zero denials, lower median cost. Pooled with the ledger and nested runs, 20 pairs give 0.975 elapsed / 0.932 input for $0.2325. The earlier five-pair edit sample (1.237 / 1.162) was noise.
