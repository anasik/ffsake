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
