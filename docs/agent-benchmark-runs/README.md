# Agent benchmark evidence

## 2026-10-30 Claude measured matrix

- `20261030-claude-transcripts.tar.gz` contains the 50 surviving Claude Code session transcripts from the complete measured matrix: five tasks, two arms, five matched repetitions. Each directory is one run and contains the raw stream transcript.
- `rc-claude-20261030-2.reconstructed.json` is derived from those transcripts. It records the run order, role, prompt, reported cost, token components, tool calls and preserved correctness outcome. It is not the original harness JSON.
- `SHA256SUMS` records both files.

The original consolidated JSON and harness logs were written under `/private/tmp/ffsake-onboarding`, which macOS removed before the report was committed. The Claude Code transcripts under `~/.claude/projects` survived because they live outside `/private/tmp`; they were copied into the archive. Cost, token, tool-call, adoption and correctness totals in the reconstructed summary match the pre-cleanup aggregate exactly. Per-run harness wall time and verifier metadata (changed-file lists and exact audit fields) were not recovered. The preserved aggregate correctness is 24/25 native and 25/25 FFSAKE, with one native cross-file run editing `README.md` outside its allowed change set. See `docs/BENCHMARK.md` for the full method and caveats.

The transcripts contain prompts, model output, tool calls and absolute disposable paths. They were checked for credential patterns; no API keys or private keys were found. They are local benchmark evidence, not a sanitized public transcript.

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
