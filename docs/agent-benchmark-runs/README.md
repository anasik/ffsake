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
