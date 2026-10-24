# FFSAKE: execution handoff to a local coding agent

> Continuation rule: follow the root AGENTS.md. Successful CI completes a milestone, not this ongoing assignment. Continue with the next actionable PLAN.md item in the same session. This file applies to Codex as well as Claude Code.
>
> State update: multi-file reads and their diagnostic are complete at 5e14596. The numbered steps below are historical context; do not reimplement them or duplicate completed benchmark runs. Read the current PLAN.md, source and local reports for the next action. The current next area is larger real-repository workflows, with further interface work driven by concrete findings.

You are taking over active development of `anasik/ffsake` on Anas's Mac. This is an instruction to continue the work, not a request for a plan. Read the repository's applicable instructions and current source, then execute the next task below.

## Authorization and working style

- Complete implementation, appropriate tests, commit, push directly to `main`, and monitor GitHub Actions until the pushed commit succeeds. No routine approval questions, draft PRs, or plan-only stops. This workflow is already authorized by Anas.
- Preserve unrelated local changes. Inspect git status, branch and worktrees before touching anything. Never reset, clean, force-push or discard work to make the tree convenient. The old `../ffsake-pilot` worktree may still contain the original permission-fix edits; do not overwrite or reapply them blindly.
- Resolve ordinary implementation choices yourself. Ask only for a genuinely missing decision or destructive action beyond the authorized scope. Tool-enforced permission prompts may still apply.
- Use FFSAKE MCP for repository reading/search/editing when available. Shell is explicitly authorized for builds, tests, benchmark orchestration, report analysis and Git. If FFSAKE is unavailable in this session, native repository tools are also authorized to finish this handoff; do not stop merely because a tool is missing.
- Use a normal local Codex or Claude Code development session for this work. `scripts/ffsake_claude.py` deliberately disables native tools for constrained editor pilots and is unsuitable for independently running Cargo and Git.
- Send short updates while work runs. Poll started CI and benchmark processes through completion. After a green commit, report the checkpoint briefly and continue the next actionable roadmap item. End only for a real stopping condition described in AGENTS.md; include validation, commit and the exact blocker or user action then.
- Do not add Codex coauthor attribution. Avoid unrelated new features. JEV and a new coding harness are parked. OpenCode benchmarking is paused; use Claude and Codex.

## Verified state at handoff, 2026-10-24

Latest implementation commit: `a02fb7513509b4ae5fa3768ccf8c901082ccb92d` on `main`. This handoff is a later documentation commit. Recheck the live branch before making changes.

CI passed at that implementation commit: 47 Rust tests, 57 Python tests, Clippy with warnings denied, formatting, executable benchmark and MCP smoke. CI run: https://github.com/anasik/ffsake/actions/runs/40087022888 . Anas also ran all 47 Rust tests and MCP smoke on his Mac before the latest Python-only harness fix, including 109 rejected boundary operations.

The product is a Rust headless editor backend built around FFF indexed search, workspace-scoped resident handles, frozen previews, apply, and persistent journal undo/redo. Agent interfaces are resident JSONL and MCP. It is primarily about useful editor operations and simpler permission management, with equivalent or better performance as an objective. Do not optimize benchmark numbers by weakening safety or correctness.

Implemented:

- Ranged reads, directory listing, scoped literal/regex search and guarded MatchSets.
- Exact/range/line/identifier/selection transformations and multiline block edits.
- Previewed create/copy/move/delete and mixed batches including literal edits of distinct existing UTF-8 files. One batch preview and apply produces one journal transaction and undo/redo entry.
- Batch entries refer to initial state. Dependencies, conflicting aliases and overlapping paths are rejected. No overwrite destinations. Exact positive edit match counts; stale source/destination states reject apply. Later write failures roll back earlier changes.
- Workspace boundary and symlink guards, with `.ffsake` reserved. These are application guards, not an OS sandbox or complete protection against adversarial concurrent filesystem races.
- Existing-file permissions survive atomic replacement during commit, undo and redo. Recreated files do not recover original metadata; manifests do not store file modes. Leave that larger change out of the current task.

Read `PLAN.md`, `docs/BENCHMARK.md`, and relevant source. Likely files:

- `crates/fff-workspace/src/resident.rs`
- `crates/fff-workspace/src/bin/ffsake-mcp.rs`
- `crates/fff-workspace/src/file_ops.rs`
- `crates/fff-workspace/src/journal.rs`
- `scripts/agent_benchmark_suite.py`
- `scripts/ffsake_claude.py`
- `scripts/mcp_smoke.py`
- `tests/test_agent_benchmark_suite.py`

The current explicit MCP allowlist contains 16 tools. If changing the interface, update the schema, dispatch, prompts and tool allowlists consistently rather than assuming new tools are automatically approved.

## Benchmark evidence and interpretation

Reports are local under `docs/agent-benchmark-runs/`. Inspect them directly; do not require Anas to upload or paste their contents.

Repeated project task, `suite-20261023-190038-sty7ubge.json`: all 12 runs correct. Claude median baseline 31.248 s versus FFSAKE 18.103 s; reported median cost $0.125834 versus $0.0643391. Codex baseline 57.572 s versus FFSAKE 58.197 s. These small synthetic samples are not universal performance proof. Observed FFSAKE-only calls do not prove host-enforced isolation, particularly for Codex whose native tools remain available.

Latest diagnostic, `suite-20261024-153809-w2at71po.json`, maintenance task (one repetition):

| Agent | Mode | Elapsed | Calls | Verification |
| --- | --- | ---: | ---: | --- |
| Claude | baseline | 18.241 s | 8 | 25/26; only file-set/artifact failure |
| Claude | FFSAKE | 16.586 s | 6 | 26/26 |
| Codex | baseline | 44.639 s | 4 | 26/26 |
| Codex | FFSAKE | 34.710 s | 23 | 26/26 |

Both FFSAKE agents used exactly one seven-operation `ffsake_preview_files` call and one apply. Claude's other four calls were three reads and one search. Codex's other 21 calls were 19 reads and two searches, including instruction-file discovery and post-apply verification; three missing-path probes returned errors. Mutations themselves succeeded without recovery.

Claude baseline implemented the task correctly. Its own Python CLI tests created three `dispatch/__pycache__/*.pyc` files, causing the strict file audit to fail. Do not claim a correctness win from this. Commit `a02fb75` fixes the environment by setting `PYTHONDONTWRITEBYTECODE=1` in `run_process` for every agent/mode and verifier child process. Tests verify nested imports inherit it without changing the host environment; deliberately added cache artifacts still fail. No audit exclusions were relaxed and no historical report was rewritten.

The `maintenance` task begins with a runnable Python CLI, then requires code/config/README edits, a byte-preserving move and copy, a deletion and a new file. The host verifier has 26 checks covering file set, preserved bytes, defaults, retired/new config keys and strict integer/range validation in separate CLI processes. Only its two requested deletions are allowlisted. Existing default tasks remain `micro,medium,app`; opt into maintenance explicitly.

## Initial assignment (completed; consult current PLAN.md)

1. Inspect git state and any reports newer than the diagnostic above. A three-repetition maintenance comparison was requested but no resulting report has been seen in this handoff. If it exists, analyze it; do not duplicate it. Check for an already running suite before launching another.
2. If no completed post-fix report exists and no suite is running, build and run the already-authorized comparison locally:

   ```bash
   cargo build --locked --bin ffsake-mcp
   python3 scripts/agent_benchmark_suite.py --agents claude,codex --tasks maintenance --repetitions 3 --timeout 900
   ```

   This makes 12 agent runs, saved after every case. Monitor it through completion. Analyze failures and invalid-comparison reasons before taking medians at face value. If a nested CLI refuses to launch, diagnose the exact restriction and use a supported local invocation; do not change global authentication or silently substitute models.
3. Address the observed read/verification round-trip overhead. Inspect the existing resident read/target APIs before designing anything. Prefer extending existing capabilities to read several bounded, explicitly selected files in one call if the traces still justify it. Handle missing files clearly so absence checks do not require repeated failing reads. Preserve workspace/symlink/internal-path guards, bounded requests/output, UTF-8 behavior and existing single-file compatibility. Do not introduce generic shell execution into FFSAKE or a second scripting language. Do not suppress legitimate verification merely to lower tool-call counts.
4. Add focused regression and MCP smoke coverage for the implemented interface change, including boundaries and missing-file behavior. Update documentation and prompts/allowlists where necessary. Run appropriate checks, fix failures, commit and push to `main`, then monitor the exact pushed CI run to success. Use a targeted post-change agent diagnostic only when it answers whether the interface is adopted; avoid endless identical paid reruns.
5. Record concrete findings in `PLAN.md` and `docs/BENCHMARK.md`. Clearly distinguish proven correctness, single-run timing, repeated medians, unavailable Codex cost and still-unverified permission isolation.

## Local validation

Run relevant checks during development and the required suite before declaring completion:

```bash
cargo test --locked --workspace
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo fmt --all -- --check
cargo build --locked --bin ffsake
cargo build --locked --bin ffsake-mcp
python3 scripts/benchmark.py target/debug/ffsake
python3 scripts/mcp_smoke.py target/debug/ffsake-mcp
python3 -m unittest discover -s tests -p 'test_agent_benchmark_suite.py' -v
```

Use `gh` or available GitHub tools to push and monitor CI. Do not claim completion with required checks pending or failed. If an external constraint truly prevents progress, give the exact blocker and the smallest necessary user action; otherwise continue working.
