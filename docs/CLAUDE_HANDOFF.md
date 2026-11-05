# FFSAKE: execution handoff to a local coding agent

> Continuation rule: follow the root AGENTS.md. Successful CI completes a milestone, not this ongoing assignment. Continue with the next actionable PLAN.md item in the same session. This file applies to Codex as well as Claude Code.
>
> State update: performance is the active goal. The numbered steps below are historical context; do not reimplement them or duplicate completed benchmark runs. Read current traces, source and PLAN.md, implement the smallest justified interaction-cost reduction, validate model-free, and request a bounded paid budget before measuring it.

## Competitive unfamiliar-task goal (2026-11-05, candidate pending validation)

Active goal: make FFSAKE reliably competitive with native tools on unfamiliar editing and debugging while preserving capabilities and safeguards.

Baseline (final held-out, 30 attempts, $2.8672, candidate `3e9a9c8` from `2b78219`): artifact correctness 15/15 in both arms; harness completion 10/15 FFSAKE versus 12/15 native; median paired elapsed ratio 1.268 and input ratio 1.127. FFSAKE was slower mainly on `debug_ledger` (13 calls / 349,547 input versus native 7 / 154,137) and missed workflow evidence on `debug_nested` (0/5 versus 2/5).

Diagnosis from retained traces: ledger runs used a one-shot probe and then a separate `debug_launch`/`continue`/`terminate` sequence to observe a second loop iteration; every FFSAKE run called `ffsake_tasks` before its real work; nested runs set the breakpoint at the `main.py` call site, so the exact `key=limits` local-name marker never appeared even though the nested dicts were observed; the remaining overhead includes the fixed MCP catalog and extra native verification commands.

Candidate (tested model-free, not paid-measured), release binary `8e3bfa49eb5c2e4ddca40430315d44152ba013698be6a5bec42f8a517d14e2e1`: `ffsake_debug_probe` accepts `stops` 1-4 and returns `stop_inspection_2`...`stop_inspection_4` before cleanup; `ffsake_workspace_info` returns a compact task summary. On the retained ledger fixture the old 4-call probe/launch/continue/terminate path returned 8,409 bytes; one `stops=2` probe returns 5,062 bytes in 1 call with `index=0` then `index=1`. Projected median calls: `edit_batch` 10 versus 10, `debug_ledger` 9 versus 7, `debug_nested` 7 versus 7. Rust tests, Clippy, formatting, DAP multi-stop smoke, MCP/profile/worktree/wait/repository smokes and the exact-debugpy preflight pass. A conservative description/instruction rewrite then reduced core context from 37,598 to 31,812 bytes (-15.4%) and editing context from 20,238 to 17,659 bytes (-12.7%) with no tool, parameter, authorization or cleanup change; profile/byte-history checks still pass. The evidence-backed barrier and redesign are in `docs/ARCHITECTURAL_BARRIER.md`: the measured loss is turn count (157 vs 113; input per call was equal at 22,090 vs 22,353), the consolidations project 130 vs 113, and the remaining 17 turns equal the `ToolSearch` count. The next redesign candidates are Rust-tool consolidation below a 20 KB core catalog and a CLI shim if host tool discovery persists. The preflight also requests four stops on the one-shot cache fixture to confirm the probe returns cleanly when the program exits before reaching the requested stop count.

Budget: the previous 60 paid attempts ($5.7511 total reported) are exhausted. Requested validation budget: **30 attempts, $6 ceiling** - 20 for counterbalanced candidate-versus-native screening on two new unseen tasks (one multi-file edit, one debug requiring at least two stops or expansion), 10 reserved for one final confirmation on a third unseen task. No reruns for favorable results; count pilots, failures and retries.

Next action after approval: freeze the candidate binary at the tested commit, preflight exact adapters and policies, then run the screening batch and publish sanitized per-run evidence. Before scoring, replace the pilot's exact-name workflow marker with a disclosed general check that requires a debugger-observed runtime value whose name or value contains the target, and report it separately from artifact completion.

Stop when success is demonstrated on held-out tasks, the budget is exhausted, three materially different candidates fail to improve the bottleneck, or progress needs a safeguard/dependency change. Do not start unrelated features, a new harness or fine-tuning.

## Current handoff (2026-11-04, performance follow-up)

Implementation `936a70a` combines stop inspection with debugger control actions. `continue`, `next`, `step_in`, `step_out` and `pause` now wait up to 900 ms for the next stop, then return a bounded inspection in the same response; explicit `inspect` inspects the current stop without resuming. Defaults are 5 frames, 4 scopes, 8 variables per scope, at most 64 variable entries, 512 characters per value and a 32 KiB automatic-inspection budget. `inspect=false` opts out, and `auto_wait=false` implies it. No evaluation, raw DAP, resume or extra launch was added. Handles, authorization, stale-state clearing and owned-process cleanup remain guarded; if no stop arrives, the control result succeeds and reports `wait_timed_out`.

Local validation for this handoff: 156 Rust tests, 117 Python tests, Clippy with warnings denied, formatting, the 16-case debug protocol smoke, the combined-inspection wait/opt-out smoke, and debugpy compatibility. The model-free sequence uses five MCP calls (launch, access, breakpoints, continue with inspection, terminate) with zero explicit polls or waits and checks default/explicit limits. Real C#/netcoredbg remains CI-only locally.

Claude Code `2.1.317` (sonnet host alias) completed a five-attempt counterbalanced old/changed/native comparison on payroll and shipping. The changed release binary was `6a54214630a41d6bd65450f970b061ed39f982b62c42f13a6a81818e8a7fc34a`; rebuilding committed `936a70a` produced the same hash. Payroll old `38.0 s / 25 calls` versus changed `28.5 s / 17 calls` (−25.0% elapsed; −32.0% calls; −31.4% input); shipping old `32.1 s / 23 calls` versus changed `21.8 s / 14 calls` (−32.1% elapsed; −39.1% calls; −49.9% input); combined elapsed −24.4%. Shipping met the 30% target; payroll did not, so the target is **not met on both fixtures**. Native remained faster. Payroll changed was 4/5 correct versus old 3/5 valid and native 5/5; shipping was 5/5 in every mode. One old payroll attempt failed at the provider and a sixth attempt completed the five valid old runs; both are retained, with the failure excluded only from the valid timing median. Counting all attempts, old payroll median was 39.6 s (−28.0%). The shipping verifier was corrected from a never-run evidence path and a wrong `subtotal` marker to the actual `amount` variable, then strengthened to require parsed runtime values from FFSAKE inspection responses and native debugger output rather than command/source keywords. The same 15 runs still passed 5/5 in every mode, with no model rerun and no check loosened. Re-evaluating retained traces corrected prior indexes: prior-fix payroll native 4/5→5/5 and shipping changed 5/5→4/5; result-fix payroll native 3/5→5/5 and shipping old 5/5→3/5. Pilot plus measured attempts used 34 of the 40-run cap; no second candidate was opened. Codex validation remains blocked by exhausted workspace credits.

Public per-run indexes are under `docs/agent-benchmark-runs/20261104-inspection/`, including corrected accounting for the two prior Claude reports. Private reports and traces stay untracked; never publish raw transcripts or personal paths. CI run 41820074918 passed at `71973a0`; the thread-inference commit passed run 41842540361 at `18cca2b`. A retained-trace audit found no product defect in the latest failed payroll run: inspection returned the pre-mutation `adjustments=[]`, and the agent skipped the post-mutation stop needed to observe `[50.0]`; the verifier correctly rejected it. The [real-project testing checklist](REAL_PROJECT_TESTING.md) records editing, build/test and debugging evidence for actual use.

Performance is again the active goal. Current changed payroll traces contain four `continue requires thread after configuration` failures across five runs; each forced a retry. `continue` now infers the stopped thread when `thread` is omitted after configuration, while an explicit thread still wins and a stop without a thread ID still errors. Model-free checks pass: 156 Rust tests, Clippy, formatting, the 16-case protocol smoke including end-to-end continue-without-thread, wait and MCP smokes. The first five-attempt changed-payroll check was invalid because the private runner resolved the debugpy venv symlink; those calls remain recorded as `benchmark_setup_error`. The runner now preserves the virtualenv path, a regression test covers it, and a model-free preflight launches the exact generated policy, stops at line 18, inspects `adjustments=[50.0]` and cleans up before paid work; launch failure aborts the batch. Five additional attempts then passed artifact and workflow checks 5/5 with runtime `[50.0]`; median 14 calls and 23.9 s versus retained 17 calls and 28.5 s, descriptive only. There were zero missing-thread errors, but no post-stop continue omitted `thread`, so the inference was not exercised and no overhead improvement is claimed. The prior sixth attempt remains an unused reserve. The efficiency candidate enriches `ffsake_workspace_info` with bounded top-level entries, the debug configuration summary and optional known-file reads, because every corrected run separately called `ffsake_list`, `ffsake_debug list` and read the source; model-free checks pass, but adoption is unmeasured without a new paid budget. The next candidate combines debug launch, initial breakpoints and first stop; `ffsake_debug_launch` now accepts `breakpoints` and `resume`, reaches the first stopped inspection in one call, and passes the 157-test suite, Clippy, formatting, MCP/worktree/DAP smokes and an exact-policy debugpy preflight. It should remove two model turns if adopted; adoption remains unmeasured without a paid budget. The third candidate omits the repeated capabilities map from stopped/running/terminated snapshots while keeping it on configuring responses, in `ffsake_debug` list and in the combined-start result; the 17-case DAP smoke verifies both presence and omission. Automatic inspection now uses two frames and the first local scope by default, reducing model-free combined-launch response bytes from 3,369 to 2,592 and second-stop bytes from 2,418 to 1,634; explicit inspect/stack/scopes still allow the full bounded inspection. A 30-attempt counterbalanced paid A/B against frozen `6899b6a` then measured changed correctness at 5/5 on both fixtures, with payroll elapsed −19.9%, input −2.3%, calls −15.4% and shipping elapsed −0.8%, input −10.0%, calls −20.0%; the 30% target was not met. The verifier was corrected to count a combined launch with `resume` and returned inspection as launch/resume/inspection, raising changed evidence to 5/5 without rerunning. The public A/B index is `docs/agent-benchmark-runs/20261105-efficiency/ab.json`; native remained faster on both fixtures. The A/B traces also showed scalar breakpoint lines causing a type-error retry in two changed runs; breakpoint parsing now accepts one line or an array, with Rust and protocol-smoke coverage. A second A/B of automatic debug-program source in workspace context reduced input descriptively but regressed changed correctness to 3/5 payroll and 4/5 shipping and increased elapsed time, so that candidate was reverted at `da5a07c`; the negative index is `docs/agent-benchmark-runs/20261105-efficiency/ab-debug-source.json`. The one-shot probe now applies initial breakpoints, steps once after the first stop, returns both inspections and cleans up in one call. A 20-attempt native-versus-probe A/B measured changed payroll 16.9 s / 8 calls / 206,079 input versus native 16.5 s / 6 / 155,556, and shipping 13.3 s / 7 / 199,355 versus native 14.2 s / 7 / 155,817, with 5/5 correctness on both. The final line-numbered stepped probe then measured changed payroll 16.9 s / 7 calls / 174,341 input versus native 17.2 s / 7 / 181,501, and shipping 12.1 s / 6 / 173,905 versus native 13.6 s / 7 / 156,389, with 5/5 correctness. Against retained frozen-old medians this is −33.8% elapsed/−49.0% input payroll and −33.5% elapsed/−43.0% input shipping, so both fixtures exceed the 30% targets and performance is near native.

## Generalization goal revision (2026-11-05)

Pilot score consumed 30 paid attempts and $2.8839. Artifact correctness was 15/15 in both arms, but harness completion used exact runtime-marker names and failed valid dict/nested inspections: FFSAKE 7/15 versus native 6/15. Median paired elapsed ratio was 1.067 and input ratio 1.282. The pilot is preserved as non-final evidence and is not pooled with the next evaluation.

Remaining budget: **30 paid attempts** and **$7.1161**. Reserve all 30 for final evaluation. Candidate remains frozen at `3e9a9c8cc89d65bb090da590caea4bea1b9aea504e9d293c0ef5173b0889fcca` from `2b78219`; do not modify candidate, prompts or verifiers during final runs.

Final held-out tasks: `edit_batch` (three-file percentage-discount edit), `debug_ledger` (multi-iteration loop boundary), and `debug_nested` (nested settings merge requiring expansion). Independent program/test verifiers were defined before runs; model-free preflights pass. Completion is measured by those artifact/runtime verifiers; debugger workflow evidence is reported separately rather than using the pilot's exact-name markers. This limitation is disclosed before final scoring.

Final plan: five attempts per arm per task, counterbalanced, exactly 30 attempts. Stop when complete or budget/setup fails. Publish sanitized per-run evidence and final verdict; do not rerun for favorable results.

## Generalization goal (2026-11-05)

Freeze the current candidate before scoring. Candidate binary `3e9a9c8cc89d65bb090da590caea4bea1b9aea504e9d293c0ef5173b0889fcca` from `ddbfc68`; model-free preflight passed for all three fixtures before scoring. Use three previously unused debug fixtures: `debug_inventory` (larger multi-module boundary bug), `debug_intervals` (adjacent-interval boundary bug), and `debug_cache` (missing-key exception path). Their observable verifiers run the program and tests; workflow evidence requires a runtime marker observed from the debugger. Prompts do not contain fixes or expected values.

Compare FFSAKE against native with Claude, identical task prompts, permissions and starting state, counterbalanced order, five attempts per arm per fixture. Budget: **maximum 30 paid attempts** (3 fixtures × 2 arms × 5), subject to available credits. Run `scripts/unfamiliar_debug_preflight.py` with the exact candidate and debugpy before scoring; abort on setup failure.

Success requires no lower completion rate than native on any fixture, overall median paired elapsed ratio ≤1.0, and overall total-input-token ratio ≤1.10, with all safeguards intact. If a defect requires changing candidate, fixture or verifier during scoring, preserve affected attempts, disclose the change, and do not pool builds.

Stop when the comparison is complete or a genuine blocker prevents progress. Do not start another optimization loop afterward.

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

Unfamiliar-debug generalization (2026-11-05): frozen candidate `3e9a9c8cc89d65bb090da590caea4bea1b9aea504e9d293c0ef5173b0889fcca` from `2b78219`; three new fixtures (`debug_inventory`, `debug_intervals`, `debug_cache`), independent program/test verifiers and runtime-marker checks, model-free preflights passed. Thirty paid attempts, five per arm per fixture, no timeouts. Artifact correctness was 5/5 for both arms on every fixture; harness completion was FFSAKE 7/15 versus native 6/15, lower on inventory (0/5 vs 2/5) and cache (2/5 vs 3/5). Median paired ratios: elapsed 1.067, input 1.282; native cost $1.3017, FFSAKE $1.5822. Verdict partially met: generalization of artifact correctness held, but efficiency and harness-completion criteria did not. No changes were made during scoring and no attempts were rerun. Public index: `docs/agent-benchmark-runs/20261105-generalization/debug.json`.

Final held-out generalization (2026-11-05): candidate `3e9a9c8cc89d65bb090da590caea4bea1b9aea504e9d293c0ef5173b0889fcca` unchanged. Three held-out tasks (`edit_batch`, `debug_ledger`, `debug_nested`) with independent program/test verifiers; model-free preflights passed. Thirty final attempts, no timeouts. Artifact completion 15/15 both arms; harness completion FFSAKE 10/15 vs native 12/15, lower on nested settings (0/5 vs 2/5). Median paired ratios: elapsed 1.268, input 1.127. Cost: native $1.2203, FFSAKE $1.6468. All 15 FFSAKE runs used native fallback. Verdict: **not met**—artifact correctness generalized, but harness completion and efficiency criteria failed. Pilot (30 attempts, $2.8839) is preserved separately and not pooled. Total 60/60 attempts used; no reruns. Public final index: `docs/agent-benchmark-runs/20261105-generalization/final.json`.
