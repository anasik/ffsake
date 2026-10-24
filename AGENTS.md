# Repository execution instructions

## Standing authorization

Anas has authorized ongoing FFSAKE development within the roadmap in PLAN.md. Implement, test, commit and push directly to main, then monitor the exact pushed GitHub Actions run to completion. Routine confirmation and pull requests are not required. Tool-enforced access controls still apply.

## Continue after milestones

A completed feature, passing tests, a pushed commit, or green CI is a checkpoint, not an instruction to end the session. After each checkpoint:

1. Reconcile PLAN.md with the work actually completed and current evidence.
2. Select the next concrete, justified action within the existing FFSAKE roadmap.
3. Continue executing it in the same session. Send a short progress update instead of yielding merely to announce the next step.
4. Keep changes reviewable and validate each change appropriately. Reuse completed measurements rather than launching identical paid benchmarks without a new question.

When asked why work stopped, answer briefly and resume the next available action. Do not wait for a second instruction to resume already-authorized work.

Stop only when Anas tells you to stop, an actual external/access constraint blocks all useful independent work, or a material decision requires information or authorization that is genuinely absent. Report the exact blocker and smallest necessary user action. Do not invent low-value work, broaden into unrelated products, or bypass security controls to keep running.

## Preserve work and project boundaries

Inspect git status, branches and worktrees before editing. Preserve unrelated local changes; do not reset, clean, force-push or discard them. Do not add Codex coauthor attribution.

Use FFSAKE tools for repository operations when available. Native tools are authorized when needed to complete development. Shell is authorized for builds, tests, benchmark orchestration, report analysis and Git. A constrained editor-only launcher is not required for the main development session.

Maintain workspace, symlink, internal-path, stale-content and transaction guards. Do not weaken verifiers or skip verification to improve benchmark numbers. Keep reported timings distinct from general performance claims and observed tool use distinct from enforced isolation.

JEV and a separate coding harness are parked. OpenCode benchmarking is paused. Do not silently reopen these threads.

## Sources of current state

PLAN.md and current source are authoritative for the next work. docs/BENCHMARK.md records benchmark evidence. docs/CLAUDE_HANDOFF.md is a cross-agent handoff; its historical snapshots do not override newer commits, local reports, or these continuation instructions.

These instructions apply equally to Codex, Claude Code and other coding agents.
