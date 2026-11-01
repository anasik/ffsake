# Agent benchmark evidence

## 2026-10-30 Claude measured matrix

- The 50 surviving Claude Code session transcripts are retained in local salvage storage. They were removed from the public tree and reachable branch history because raw sessions contained personal information. GitHub still served the old commit by ID after the rewrite; cached objects and copies require separate cleanup.
- `rc-claude-20261030-2.reconstructed.json` is derived from those transcripts. It records the run order, role, prompt, reported cost, token components, tool calls and preserved correctness outcome. It is not the original harness JSON.
- `SHA256SUMS` records the public reconstructed summary.

The original consolidated JSON and harness logs were written under `/private/tmp/ffsake-onboarding`, which macOS removed before the report was committed. The Claude Code transcripts under `~/.claude/projects` survived because they live outside `/private/tmp`; they were copied into the archive. Cost, token, tool-call, adoption and correctness totals in the reconstructed summary match the pre-cleanup aggregate exactly. Per-run harness wall time and verifier metadata (changed-file lists and exact audit fields) were not recovered. The preserved aggregate correctness is 24/25 native and 25/25 FFSAKE, with one native cross-file run editing `README.md` outside its allowed change set. See `docs/BENCHMARK.md` for the full method and caveats.

The local transcripts contain prompts, model output, tool calls, account email and absolute paths. A pattern scan found no obvious API keys or private keys; it is not a complete secret audit. They require sanitization before publication.

## Reproduce

Run from the repository root with Claude Code installed, the MCP binary built, and a debugpy-enabled Python for the debug task:

```sh
python3 scripts/rc_benchmark.py \
  --binary target/release/ffsake-mcp \
  --output docs/agent-benchmark-runs/rc-claude-REPLACE.json \
  --agents claude \
  --tasks cross,maintenance,build,git,debug \
  --repetitions 5 \
  --debug-python /path/to/debugpy/venv/bin/python
```

A fresh run writes a complete harness JSON with verifier metadata. It will use the current Claude Code version and may not reproduce the 2026-10-30 sample exactly. Codex and OpenCode were not part of this matrix; see the limitations in `docs/BENCHMARK.md`.

## 2026-11-01 Codex measured matrix

The [cross](20261101-codex/cross.json), [maintenance](20261101-codex/maintenance.json), [build](20261101-codex/build.json), and [debug](20261101-codex/debug.json) files contain per-run metrics and a content-free redacted event index. They omit prompts, commands, arguments, tool output, session IDs and paths to protect privacy; they cannot independently establish every workflow observation. Original transcripts and verification details remain in local private storage. [Model-free safety outcomes](20261101-codex/safety.json) and [one setup timing](20261101-codex/setup.json) are separate. See [methodology and results](../BENCHMARK.md#release-candidate-codex-comparison-2026-11-01).
