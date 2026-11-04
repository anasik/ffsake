# Real-project testing checklist

Use this once on a real project task. It is a usability check, not a benchmark; do not run matched synthetic repetitions or paid comparison studies for it. Keep raw prompts, tool arguments, outputs, paths and session identifiers in private notes.

## Run record

| Field | Value |
| --- | --- |
| Project / revision | |
| Branch or worktree | |
| Task and expected behavior | |
| Build command | |
| Test command | |
| Debug configuration | |
| Host / FFSAKE version | |

## 1. Prepare

- [ ] Work on a disposable branch or linked worktree; do not use production credentials.
- [ ] Record the starting `git status` and preserve unrelated staged, unstaged and untracked work.
- [ ] Confirm the baseline build/test command and current result.
- [ ] State the smallest observable success condition and the files expected to change.
- [ ] Confirm the FFSAKE primary root before editing or running tasks.
- [ ] Decide how debugging will be launched or attached, and how the process will be cleaned up.

## 2. Editing

- [ ] Locate the relevant code with scoped reads/search.
- [ ] Make the smallest previewed edit; check the diff before applying.
- [ ] Confirm stale-content checks and authorization behave as intended.
- [ ] Review the final diff and unrelated-file audit.
- [ ] Use undo/redo only when testing history behavior; verify the resulting files.
- [ ] Record file-editing calls, repeated reads, rejected previews and any native edit fallback.

## 3. Build and test

- [ ] Run the project's real build and test command.
- [ ] For a long-running task, observe output while it is running rather than only after exit.
- [ ] Record exit codes, failing checks and whether the failure was reproduced before the fix.
- [ ] Re-run the same command after the change and confirm the result.
- [ ] Note any use of a native shell/task runner and whether an FFSAKE task was available.

## 4. Debugging

- [ ] Launch or attach using the real project configuration.
- [ ] Set a breakpoint where the relevant value exists; do not infer runtime state from source.
- [ ] Inspect frames, scopes and variables at the stop.
- [ ] Record the variable name, value and stop location.
- [ ] If the stop is before a mutation, step over it, continue to a later stop, or move the breakpoint before concluding.
- [ ] Use the expected number of actions; note redundant polls, waits, retries and rejected calls.
- [ ] Continue/step to confirm behavior, then terminate or detach cleanly.
- [ ] Confirm no owned adapter or debuggee process remains.
- [ ] Note any native debugger fallback and why it was needed.

## 5. Capture unnecessary work

Record each item in the run notes:

- **Necessary call:** advances the task, obtains required evidence, or verifies a result.
- **Unnecessary call:** duplicate read, repeated identical poll, preview without apply, wrong action, stale handle retry, or a call that could have been inferred from an already returned result.
- **Error:** tool error, permission denial, timeout, wrong breakpoint, stale state or provider error; state whether recovery needed an extra call.
- **Native fallback:** shell, native file edit or native debugger use; record the tool and reason, and whether an FFSAKE equivalent existed.

| Phase | FFSAKE calls | Unnecessary calls | Errors | Native fallback / reason | Outcome |
| --- | ---: | ---: | ---: | --- | --- |
| Editing | | | | | |
| Build/test | | | | | |
| Debugging | | | | | |

## 6. Exit criteria

- [ ] The real task is complete and the diff is reviewed.
- [ ] Build and tests pass, or the remaining failure is understood and documented.
- [ ] Runtime evidence supports the diagnosis rather than only source reading.
- [ ] Debugger and task processes are cleaned up.
- [ ] Unrelated changes and safeguards are preserved.
- [ ] The notes contain no secrets, raw prompts, tool arguments, outputs, personal paths or session IDs.
