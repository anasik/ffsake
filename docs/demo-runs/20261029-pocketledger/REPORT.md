# FFSAKE hands-on demo report: pocketledger

Date: 2026-10-29
Repo: `/Users/anaskhan/Documents/GitHub.nosync/ffsake`
Baseline commit: `a1423df` ("Avoid stalled HTTP mirror in CI dependency setup")
Demo workspace: `/var/folders/1r/f47wxf3900d38thcw0b675sr0000gn/T/opencode/ffsake-demo/pocketledger`
Canonical workspace path: `/private/var/folders/1r/f47wxf3900d38thcw0b675sr0000gn/T/opencode/ffsake-demo/pocketledger`

## 1. Executive summary

I built a small Rust library (`pocketledger`) with five integration tests and four
deliberate bugs, then drove the existing `target/debug/ffsake-mcp` server over MCP
stdio for every major feature family.

Result: **all five tests pass**, every mutation was previewed before apply, and every
applied change was undone by FFSAKE. The strongest positive signals were the guarded
preview/apply/undo model, exact stale-state rejection, byte-exact multi-file rollback,
and the C++ semantic rename (three files, external compile and run verified).

The strongest caveats were also reproduced in practice:

- Rust native rename missed references inside `format!("{input}")`, exactly as the
  documented omitted-macro limitation predicts, and produced a non-compiling change
  after a "successful" apply. FFSAKE does **not** claim semantic correctness here.
- Rust diagnostics produced false positives without the sysroot (`derive`, `format!`,
  `E0282`). External `cargo test` remained the authority.
- MCP `preview_files` rejects two operations on the same path, so multi-edit files need
  separate previews or the lower-level JSONL `Transform` interface.
- Exact-root search is all-or-nothing; a single file over 4 MiB (e.g. build output under
  `target/`) rejects the whole request unless excluded.
- External access paths must be canonical: macOS `/var` is a symlink to `/private/var`,
  and FFSAKE rejects symlink components.

No repository source was modified. The only file added to the repository is this report.

## 2. What FFSAKE is, and how I chose to exercise it

FFSAKE is a headless, workspace-scoped repository editing/navigation substrate. It is
not a shell wrapper: reads, searches, previews, mutations, undo/redo, Git review, task
execution, archive inspection and language-server queries are explicit typed operations.

Two interfaces exist:

| Interface | Binary | Purpose |
| --- | --- | --- |
| Resident JSONL | `target/debug/ffsake serve WORKSPACE` | Stateful line-delimited JSON; generic `Transform` support |
| MCP stdio | `target/debug/ffsake-mcp WORKSPACE` | Agent-facing adapter exposing named tools |

### Decision: drive the MCP transport directly

The MCP binary is already the thing Claude uses. It is not installed globally
(`which ffsake-mcp` → nothing); `scripts/ffsake_claude.py` generates a temporary MCP
configuration per Claude launch. The checked-in `opencode.json` points at a stale
benchmark directory (`.../ffsake-suite-opencode-app-efnodcci`), so it was irrelevant.

I chose a deterministic stdio client over an interactive model session because this was
a feature sweep, not a model-quality experiment. Every request and response is
reproducible, and model behaviour cannot mask or create FFSAKE behaviour.

The client was a ~50-line Python helper (`driver.py`) implementing:

- `initialize`
- `tools/list`
- `tools/call`
- newline-delimited JSON-RPC 2.0 over stdin/stdout

### Decision: disposable project outside the repository

All demo files live under the OS temp directory. The repository working tree was left
exactly as found; the only addition is this report. Pre-existing uncommitted changes
(`scripts/agent_benchmark.py`, `.ffsake/`, other `docs/demo-runs/*`, `opencode.json`,
`__pycache__`) were not touched.

## 3. Environment

| Item | Value |
| --- | --- |
| OS | macOS (`darwin`), arm64 |
| Rust | `rustc 1.99.1 (58cf8f9c6 2026-10-13)` |
| Cargo | workspace edition 2024; demo crate edition 2021 |
| rust-analyzer | installed (`rust-analyzer-aarch64-apple-darwin`) |
| clangd | `/usr/bin/clangd` |
| clang++ | `/usr/bin/clang++` |
| zip/unzip | installed |
| Python | 3.14.6 |
| Debug adapter | `netcoredbg` **not installed**; `dotnet` present |

Build used:

```sh
cargo build --locked --bin ffsake --bin ffsake-mcp
```

Both binaries were already up to date (`Finished dev profile in 0.50s`).

## 4. The demo project

### 4.1 Shape

```
pocketledger/
  Cargo.toml
  README.md
  src/lib.rs          module wiring
  src/money.rs        Money (integer cents) + string parsing
  src/ledger.rs       Entry + Ledger (record, balance, balance_for)
  src/report.rs       top_accounts + average_expense
  tests/ledger_tests.rs
```

The domain was chosen so search, exact edits, renames, LSP navigation, tests and Git
review all have meaningful targets. Integer cents avoid floating-point noise; the
public API (`Money`, `Ledger`, `Entry`, `top_accounts`, `average_expense`) gives the
LSP tools real symbols and cross-file references.

### 4.2 Seeded bugs

1. `Money::parse` mishandled negatives: `-5.25` became `-475` because it parsed the
   absolute parts and then added a negative dollar value to a positive cent value.
2. `top_accounts` sorted ascending by signed balance.
3. `average_expense` divided total expenses by **all** entries, not by expense count.
4. Bug 1 propagated into the balance test (`100.00 - 25.50` computed as `75.50`).

### 4.3 Baseline

Before any FFSAKE mutation:

```
running 5 tests
.... 4/5
parses_negative_amounts --- FAILED
average_expense_ignores_income --- FAILED
balance_combines_income_and_expenses --- FAILED
top_accounts_are_largest_first --- FAILED

test result: FAILED. 1 passed; 4 failed
```

## 5. Phase-by-phase log

### Phase 1 — interface discovery

The full MCP catalog exposed **43 tools**:

```
ffsake_debug_launch, ffsake_debug, ffsake_git, ffsake_tasks, ffsake_run_task,
ffsake_semantic, ffsake_read, ffsake_read_files, ffsake_list, ffsake_target,
ffsake_archive, ffsake_search, ffsake_rust_context_from_manifests,
ffsake_rust_references, ffsake_rust_definition, ffsake_rust_implementation,
ffsake_rust_inspect, ffsake_rust_hover, ffsake_preview_rust_action,
ffsake_preview_semantic_rename, ffsake_preview_rust_rename,
ffsake_rust_rename_proposal, ffsake_rust_code_actions,
ffsake_rust_workspace_diagnostics, ffsake_rust_diagnostics, ffsake_rust_symbols,
ffsake_select_rust_identifiers, ffsake_preview_matches, ffsake_preview_edit,
ffsake_preview_rename, ffsake_preview_files, ffsake_preview_create,
ffsake_preview_copy, ffsake_preview_move, ffsake_preview_delete, ffsake_apply,
ffsake_undo, ffsake_redo, ffsake_release, ffsake_refresh, ffsake_open_access,
ffsake_list_access, ffsake_remove_access
```

Operations exercised:

- `ffsake_list "."` returned directories and files with sizes, including `.git`,
  `source`, `tests`, `target`, `Cargo.*`, `README.md`, and the zip fixture.
- `ffsake_read README.md` returned the text plus `start_line`, `end_line`,
  `total_lines`.
- `ffsake_read_files` on five files returned ordered per-file results, each with an
  `ok` or `missing` status.
- `ffsake_target` with `Glob "src/**/*.rs"` resolved the sticky scope to
  `src/ledger.rs`, `src/lib.rs`, `src/money.rs`, `src/report.rs`.
- Literal search for `top_accounts` returned one range at
  `src/report.rs` line 4, byte 69..81.
- Regex search for `pub fn [a-z_]+` returned 10 ranges across 3 files.
- Excluding `tests/**` from an `account` search correctly reduced the result set.

### Phase 1b — search semantics and guards

**Observation: `limit` is a file-page limit, not a match limit.**

Repeated searches for `entry` with `limit` 1, 2, 3 and 100 all returned all six matches
inside `src/ledger.rs` on the first page:

```
limit 1   -> files 1 matches 6 next_file_offset 1
limit 2   -> files 1 matches 6 next_file_offset 1
limit 3   -> files 1 matches 6 next_file_offset 1
limit 100 -> files 2 matches 15 next_file_offset 0
```

Correct pagination uses the returned `next_file_offset`:

- page 1 (`offset 0`, `limit 1`) → `src/ledger.rs`, 6 matches, next 1
- page 2 (`offset 1`, `limit 1`) → `src/report.rs`, 9 matches, next 0

The docs describe `limit` as a result limit and warn that target filtering happens after
FFF paging. In practice the unit is files. This matters for agents that write
`limit: 100` and assume 100 total matches.

**Hidden files / direct search.** A hidden `.scratch_calcite/notes.txt` containing
`Planner` was found by default search because the workspace is a Git repository
(coverage said hidden filtering applies mainly to non-Git workspaces). Explicit
`Exact` targets switched the backend to `explicit_paths` and bypassed ignore rules:

- `target.include: [{"Exact": ".scratch_calcite"}]` → 1 hit.
- `target.include: [{"Exact": "."}]` → 1 hit, `hidden_directories: included`,
  `ignore_rules: bypassed`, skips `.git`, `.ffsake`, symlinks, special files and
  binary/non-UTF-8 contents.

**Exact-root bound.** Later, after Cargo produced large artifacts, an exact-root search
over the whole workspace failed with:

```
fff search error: explicit search exceeds 4 MiB/file or 16 MiB total
```

Adding `exclude: [{"Glob": "target/**"}]` made it succeed. Exact-root search is
all-or-nothing: one oversized file rejects the request rather than skipping it.

**Safety guards confirmed:**

| Attempt | Result |
| --- | --- |
| Search target `../external` | rejected: invalid workspace-relative path |
| Read `../external/secret.txt` | rejected: invalid workspace-relative path |
| Read `.ffsake` | rejected: reserved for ffsake internals |
| Read invalid UTF-8 (`0xFF 0xFE`) | rejected: file is not valid UTF-8 |
| Read symlink escaping workspace | rejected: path escapes workspace |
| Edit through that symlink | rejected: symlink component forbidden |

Valid UTF-8 containing a NUL byte was readable (`"abc\u0000def"`); the documented
restriction is non-UTF-8, not NUL specifically.

### Phase 2 — host-configured tasks and Git review

A host task policy was supplied at server startup:

```json
{
  "version": 1,
  "tasks": {
    "test":  {"kind": "test",  "argv": ["cargo", "test", "--quiet"],  "cwd": ".", "timeout_seconds": 120, "output_bytes": 65536},
    "build": {"kind": "build", "argv": ["cargo", "build", "--quiet"], "cwd": ".", "timeout_seconds": 120, "output_bytes": 65536}
  }
}
```

- `ffsake_tasks` reported `execution_enabled: true` and the fixed argv.
- `ffsake_run_task {"name":"test"}` ran the baseline and returned
  `success: false`, `exit_code: 101`, elapsed 97 ms, with full captured stdout/stderr
  and the four failures above.
- `ffsake_run_task {"name":"shell"}` was rejected: unknown or disabled task.
- `ffsake_git status` returned HEAD, status codes, protected entries excluded, and
  untracked directories summarized (`.scratch_calcite/`, `Cargo.lock`,
  `pocketledger.zip`, `target/`).
- `ffsake_git diff` returned an empty diff at baseline (files were committed).
- Staged diff was later tested by host-staging `src/money.rs`; `staged: true` returned
  a 1,020-byte diff, and the index was reset afterwards.

Note: a configured job is authorized execution, not journaled or sandboxed. `cargo test`
created `target/` and `Cargo.lock` as untracked files; Git review correctly excluded
`.ffsake`.

### Phase 3 — fixing the bugs through guarded previews

The parse bug was first addressed with an exact body replacement:

```rust
let trimmed = input.trim();
let (negative, rest) = match trimmed.strip_prefix('-') {
    Some(rest) => (true, rest),
    None => (false, trimmed),
};
let cleaned = rest.trim_start_matches('$');
// ...
let magnitude = dollars * 100 + cents;
Ok(Money::from_cents(if negative { -magnitude } else { magnitude }))
```

The average fix iterated once, counted only negative entries, and returned zero when
there were no expenses.

**Attempted batch of three edits → rejected.** Sending two edits to the same file in
one `ffsake_preview_files` call produced:

```
batch operations contain conflicting paths
```

This is deliberate: all operations refer to the same initial state and overlapping
paths are rejected.

**Working transaction 1** (two files, two edits) produced a combined preview with two
unified diffs, then:

```
apply batch 1 -> changed_files: 2, transaction: tx-...-0
```

**Working transaction 2** fixed `average_expense` in a second preview/apply.

**First sort fix was semantically wrong.** I replaced ascending signed sort with
descending signed sort:

```rust
totals.sort_by(|left, right| right.1.cmp(&left.1));
```

The test still failed because it expected ranking by activity magnitude: salary 200,
rent −150, groceries −80. The correct fix was magnitude ordering:

```rust
totals.sort_by(|left, right| right.1.unsigned_abs().cmp(&left.1.unsigned_abs()));
```

This is a useful reminder that FFSAKE validates mechanics, not intent; `cargo test` is
still the correctness authority.

After the magnitude fix: **5 passed, 0 failed**.

### Phase 4 — undo/redo and stale-state guards

Two distinct behaviours were observed.

**Later edits block undo of an earlier transaction.** Transaction 1 changed both
`src/money.rs` and `src/report.rs`. Transaction 2 later changed `src/report.rs`.
Undoing transaction 1 then failed, as designed:

```
cannot apply transaction for src/report.rs:
expected fb4056..., found 7d6e4d...
```

This is the documented whole-transaction guard. To reverse only the earlier change while
keeping the later one, the correct workflow is a fresh guarded inverse edit.

**Clean undo/redo works.** The final sort edit was undone, the test failed again with
the original `groceries` vs `rent` assertion, redo restored it, and the test passed:

- apply → 5 passed
- undo → 4 passed, 1 failed
- redo → 5 passed

**Stale preview rejection.** A preview editing `scratch.txt` from `alpha` to `beta` was
created, then the host rewrote the file to `changed by host`. Apply failed:

```
content changed: expected ac678d..., actual ec0493...
```

The scratch file was removed through a fresh preview/delete transaction.

One minor observation: after the failed undo of transaction 1, a subsequent redo
returned `state: "Applied"` rather than an explicit "already applied / undo failed"
message. The failed undo remained authoritative and no bytes changed.

### Phase 5 — syntax-aware and case-preserving renames

**Tree-sitter identifier selection.** `ffsake_select_rust_identifiers` on
`["src/money.rs","src/ledger.rs","src/report.rs"]` for `account` selected **9**
identifiers across `ledger.rs` and `report.rs`, with byte ranges and lines. It excluded
unrelated identifiers such as `top_accounts` and did not touch comments/strings that did
not contain the identifier.

Invalid replacement `1bad` was rejected, but the message was generic:

```
Rust source has parse errors; no selection created
```

A clearer diagnostic would help an agent distinguish "invalid replacement identifier"
from "your source was already broken".

`preview_matches` renamed all nine `account` identifiers to `payee`, apply succeeded,
tests passed, and undo restored the original.

**Case-preserving rename.** A regex count showed 20 `entry`/`Entry` spellings. A wrong
expected count was rejected with:

```
expected 1 matches, found 20
```

The correct preview changed `Entry` → `Record` and `entry` → `record` across ledger and
report, including the import (`use crate::ledger::{Record, Ledger};`), function
signatures, fields, locals and closures. Apply passed the tests; undo restored the
original spelling.

### Phase 6 — file and directory operations

All of these worked as documented:

| Operation | Result |
| --- | --- |
| Create file + create empty directory in one batch | one transaction, `mixed_batch: true` |
| Create `docs/CHANGELOG.md` | auto-created the `docs/` parent |
| Copy file | generated a new-file preview and apply |
| Move file | delete + create in one transaction |
| Create file under a new directory | auto-created `examples/` |
| Copy directory | preserved the file and directory mode bits |
| Delete populated directory | moved the tree into protected journal storage |
| Undo delete directory | restored the directory and file byte-for-byte |
| Redo delete directory | archived it again |
| Remove empty directory | worked, undo restored it |
| Delete file | worked, undo restored bytes and mode |
| Move onto an existing destination | rejected: destination already exists |
| Create an existing file | rejected: use preview_edit |
| `remove_directory` on a non-empty directory | rejected: expected empty directory |

Directory operations expose an inventory with relative paths, byte sizes, SHA-256 hashes
and Unix modes. `delete_directory` returned `retained_in_journal: true`; undo returned a
`tree_archive` with `restore_when_applied: false`; redo flipped it back to `Applied`.

### Phase 7 — archive inspection

`pocketledger.zip` was created from the **initial buggy** source, which made the
stale-content guard easy to observe.

- Listing returned `archive_hash`, 9 entries, sizes and compressed sizes.
- Reading `src/report.rs` with the returned hash succeeded and showed the original
  `sort_by_key` and division-by-all-entries code.
- Reading with an all-zero expected hash → `archive changed since listing/read`.
- Missing entry → `specified file not found in archive`.
- `README.md` as an archive path → `archive path must end in .zip or .jar`.
- Pagination with `offset: 2, limit: 3` returned three entries and `next_offset: 5`.

Nothing was extracted or executed.

### Phase 8 — host-authorized external access

**Important macOS-specific discovery:** passing
`/var/folders/.../external` failed with `symlink component is forbidden` because `/var`
is a symlink to `/private/var`. Using the canonical `/private/var/...` path worked.
This is correct fail-closed behaviour, but the error message does not tell the user that
`realpath` is the fix.

Verified sequence:

| Step | Result |
| --- | --- |
| Read external file before grant | rejected: explicit read grant required |
| `ffsake_open_access` exact file, `read_only` | grant persisted with `lifetime: project` |
| Read granted file | returned `TOP-SECRET-OUTSIDE-WORKSPACE` |
| Read sibling file | rejected: exact file grant excludes neighbours |
| Write through read-only grant | rejected: read/write grant required |
| Remove grant | grant list empty |
| Read after removal | rejected again |
| Grant whole directory `read_only` | both files readable |
| Write through read-only directory | rejected |
| Upgrade directory to `read_write` | grant updated |
| Create external `new.txt` via preview/apply | succeeded |
| Undo external create | removed the file |
| Revoke directory grant | list empty |
| Read after revocation | rejected |

The journal lives in the primary project even for external edits; undo worked across the
grant boundary.

### Phase 9 — Rust language server

`ffsake_rust_context_from_manifests` read `Cargo.toml` and the four source files,
returned manifest/source hashes, and produced frozen arguments:

```json
{"crate_root":"src/lib.rs","edition":"2021",
 "files":["src/ledger.rs","src/lib.rs","src/money.rs","src/report.rs"],
 "crates":[{"root":"src/lib.rs","edition":"2021","features":[],"cfg_flags":[],"dependencies":{}}]}
```

Results:

| Tool | Result |
| --- | --- |
| `rust_symbols` | 8 symbols in money.rs, 11 in ledger.rs, hierarchical ranges |
| `rust_hover` on `Money` | rendered struct definition, size 8, align 0x8 |
| `rust_definition` on `Money` usage | resolved to `src/money.rs` line 2 |
| `rust_references` on `Money` | 22 locations across all four files |
| `rust_implementation` on `Ledger` | 1 implementation block |
| `rust_inspect` on `Money` | definition + 22 references + hover, 23 locations |
| `rust_diagnostics` on report.rs | 3 `E0282` type-annotation-needed errors |
| `rust_workspace_diagnostics` | 16 diagnostics; ledger had 7, lib had 0 |
| `rust_code_actions` | 4 actions on report.rs, 4 on money.rs, 5 on ledger.rs |

Every result carried `analysis_mode: "explicit_crate_snapshot"`,
`complete: false`, `compiler_checked: false`, source hashes and the documented
limitations.

**Diagnostics were not trustworthy in this mode.** The workspace report contained
`unresolved macro derive`, `unresolved macro format!`, `no method to_string on &str`,
and several `E0282`s. The project compiled and tested cleanly with Cargo. These are
artifacts of the omitted sysroot/dependency context, exactly as the tool warns. An agent
must not treat them as build truth.

**Code-action preview/apply.** The first discovered action on report.rs was
`Change visibility to pub(crate)` (1 edit, insertion of `pub(crate) ` at byte 0).
Passing a wrong expected title was rejected with
`code-action title differs from expected identity`. The correct preview inserted
`pub(crate) use ...`, apply succeeded, tests passed, and undo restored the file.

### Phase 10 — the native Rust rename caveat, reproduced

I asked rust-analyzer to rename the `input` parameter of `Money::parse` to `raw`. The
server accepted the proposal and returned **2** text edits:

- the parameter declaration
- the first use in `let trimmed = input.trim();`

It did **not** update the two `format!("bad amount: {input}")` occurrences inside the
`format!` macro. Apply succeeded, but `cargo test` failed:

```
error[E0425]: cannot find value `input` in this scope
  --> src/money.rs:26:78
error[E0425]: cannot find value `input` in this scope
  --> src/money.rs:30:48
```

Undo restored the file and the tests passed. This is not a defect in FFSAKE's guards:
it is the documented "omitted standard-library macros" limitation of the explicit
snapshot. It is a concrete demonstration that a native rename preview is not a semantic
correctness guarantee and that external compilation is mandatory.

### Phase 11 — C++ semantic rename with clangd

A three-file C++ fixture was added:

```cpp
// cpp/budget.hpp
#pragma once
int addBudget(int a, int b);
int joinBudget(int a, int b);
```

```cpp
// cpp/budget.cpp
#include "budget.hpp"
int addBudget(int a, int b) { return a + b; }
int joinBudget(int a, int b) { return addBudget(a, b); }
```

```cpp
// cpp/main.cpp
#include "budget.hpp"
#include <cstdio>
int main() { std::printf("%d\n", joinBudget(2, 3)); return 0; }
```

Baseline compiled with `clang++ -std=c++17` and printed `5`.

Exercised through `ffsake_semantic` with `{"language":"cpp","standard":"c++17"}`:

- definition of `addBudget` at the call site → 1 range in budget.cpp line 3
- references to `addBudget` → 3 ranges (declaration, call, header)
- hover on `joinBudget` → function signature and parameters
- diagnostics → 0
- document symbols → 2 functions
- rename `joinBudget` → `combineBudget` → 3 edits across `budget.cpp`,
  `budget.hpp` and `main.cpp`

The frozen `ffsake_preview_semantic_rename` matched the proposal exactly. Apply changed
all three files; external compile succeeded and the binary still printed `5`. Undo
restored all three files; compile and run still printed `5`.

**Coordinate lesson:** my first rename call used a column inside `addBudget`, so
clangd renamed `addBudget`, not `joinBudget`. The tool did exactly what the position
asked; it does not guess the intended symbol. The second run used a computed position on
`joinBudget` and produced the intended cross-file rename.

### Phase 12 — release, refresh, core profile, JSONL transforms

- **Release:** a search returned handle 1; `ffsake_release` returned
  `{"released": true}`; `preview_matches` on the released handle failed with
  `unknown MatchSet handle`.
- **Refresh:** a host-side append of `refresh-marker-xyz` to `README.md` was indexed
  after an explicit `ffsake_refresh`; search then found it at line 11.
- **Core profile:** `ffsake-mcp WORKSPACE --profile core` listed **27** tools.
  `ffsake_rust_symbols` was absent from the catalog, a valid Rust request was rejected
  with `tool disabled by core profile`, and ordinary `ffsake_read` still worked.
- **Resident JSONL `Transform`:** `ffsake serve` accepted a transform with **two
  `ApplyPositions` operations on the same file** (which MCP `preview_files` rejects),
  previewed both insertions as one diff, applied them as one transaction, read the
  changed file, undid the whole transaction, and refreshed. This confirms the generic
  transform path is more expressive than the focused MCP batch tool.

### Phase 13 — task timeout, debug and remaining boundaries

- `netcoredbg` is not installed. `ffsake_debug {"action":"list"}` returned
  `{"configurations":[],"execution_enabled":false,"one_session":true,"transport":"local stdio DAP"}`.
  `ffsake_debug_launch {"configuration":"app"}` was rejected:
  `unknown debug configuration; host must configure --debug at startup`.
  No real DAP session was therefore possible. This is an environment limitation, not an
  observed failure of the DAP implementation.

## 6. Final state

After all undo/redo and cleanup:

- `cargo test`: **5 passed, 0 failed**.
- Tracked changes in the demo repository:
  - `README.md` (test marker appended during refresh test)
  - `src/money.rs` (negative parsing fix)
  - `src/report.rs` (magnitude sort + average fix)
- Untracked demo artifacts: `.ffsake/`, `.scratch_calcite/`, `Cargo.lock`, `HISTORY.md`,
  `cpp/`, `docs/`, `examples/`, `pocketledger.zip`, `target/`.
- Permissions: editing `HISTORY.md` with mode `0o755` preserved `0o755`.
- FFSAKE journal: every applied mutation recorded and reversible; stale and
  cross-transaction guards held.

## 7. Feature coverage matrix

Legend: ✅ exercised successfully, ⚠️ exercised with an important caveat,
🔒 intentionally rejected/guarded, ➖ unavailable in this environment.

| Feature | Status | Evidence |
| --- | --- | --- |
| `read`, `read_files`, `list`, `target` | ✅ | ranged reads, batched statuses, sticky scope |
| Literal / regex / direct search | ✅ | ranges, pagination, exact-root backend |
| Search target exclusions | ✅ | `tests/**`, `target/**` |
| `preview_matches` / `preview_edit` | ✅ | unified diffs, exact counts |
| `preview_files` mixed batch | ✅ | create + create_directory in one transaction |
| Same-file batch edits | 🔒 | `batch operations contain conflicting paths` |
| `apply`, journal `undo`, `redo` | ✅ | byte-exact multi-file rollback |
| Stale preview guard | 🔒 | `content changed: expected ... actual ...` |
| Cross-transaction undo guard | 🔒 | later edit blocked earlier undo |
| Tree-sitter identifier selection | ✅ | 9 `account` identifiers |
| `preview_rename` case preserving | ✅ | 20 `Entry`/`entry` spellings |
| File create/copy/move/delete | ✅ | preview/apply/undo |
| Directory create/remove/move/copy/delete | ✅ | inventories, modes, archive/restore |
| Archive list/read/hash guard | ✅ | zip, 9 entries, stale hash rejected |
| Git status/diff (staged/unstaged) | ✅ | protected paths excluded |
| Host task list/run | ✅ | real cargo test, exit code 101 then 0 |
| Host access open/list/remove | ✅ | exact file, directory, read-only/read-write |
| Symlink/traversal/reserved guards | 🔒 | workspace escape denied |
| Rust symbols/hover/definition/references | ✅ | 8/11 symbols, 22 references |
| Rust implementation/inspect | ✅ | 1 implementation, 23 locations |
| Rust diagnostics/workspace diagnostics | ⚠️ | huge false-positive rate without sysroot |
| Rust code actions + preview/apply | ✅ | 4 actions, one applied and undone |
| Rust native rename proposal/preview | ⚠️ | missed `{input}` in `format!`, compile failed |
| Rust manifest context import | ✅ | frozen explicit crate arguments |
| C++ definition/references/hover/diagnostics/symbols | ✅ | clangd explicit snapshot |
| C++ semantic rename preview/apply | ✅ | 3-file rename, external compile/run |
| `ffsake_semantic` shared entry point | ✅ | Rust references, C++ queries |
| Release/refresh | ✅ | released handle rejected, index refreshed |
| Full vs core MCP profiles | ✅ | 43 vs 27 tools, blocked tool rejected |
| JSONL resident `Transform` | ✅ | two same-file operations in one transaction |
| DAP debugging | ➖ | `netcoredbg` not installed; launch disabled |
| Explicit `--access` export mode | ➖ | not exercised; persistent grants were |
| Task timeout/output truncation | ➖ | not exercised |

## 8. Notable behaviours, quirks and rough edges

1. **`limit` is per files, not matches.** A page can return far more than `limit`
   matches. Agents must use `next_file_offset`.
2. **Exact-root search fails closed on one oversized file.** Build directories should
   be excluded explicitly.
3. **Diagnostics are context-limited.** Rust `derive`, `format!`, `to_string` and
   `E0282` reports were false positives under the explicit snapshot. `complete: false`
   is doing real work.
4. **Native rename is not semantic completion.** The Rust `input` → `raw` rename missed
   macro interpolation and broke compilation. Preview + external build remains
   mandatory.
5. **MCP batch edits are path-exclusive.** Two edits to one file need separate
   transactions or the JSONL `Transform`.
6. **Coordinates are literal.** The C++ rename followed the supplied position, even
   when that position was on the wrong function. Computing positions from symbol
   selection ranges is safer than eyeballing columns.
7. **Use canonical paths for external grants.** `/var` vs `/private/var` on macOS
   triggers the symlink guard.
8. **Undo is whole-transaction and state-guarded.** A later edit to any member blocks
   undoing an earlier transaction; this is correct but can surprise an agent that
   expects per-file history.
9. **Invalid Rust replacement errors are generic.** `1bad` reported a source parse
   error rather than an invalid-identifier error.
10. **Redo after a failed undo is ambiguous.** It returned `Applied` without an
    explicit state explanation.

## 9. What was not tested

- Actual DAP debugging (no `netcoredbg`/C# fixture).
- `--access` explicit host-export lifetimes (`once`, `session`, `project`) and
  cross-root transactions; only the persistent MCP grant flow was used.
- Task timeouts, output truncation and process-group cleanup.
- `.jar` archives (only `.zip`).
- Multi-crate Rust graphs, features and `cfg_flags`.
- Windows path/permission behaviour.
- External symlink replacement during an in-flight operation.

## 10. Reproduction notes

Build:

```sh
cargo build --locked --bin ffsake --bin ffsake-mcp
```

Start the MCP interface with the demo workspace and task policy:

```sh
target/debug/ffsake-mcp /private/var/folders/.../pocketledger \
  --tasks /private/var/folders/.../tasks.json
```

Start the resident JSONL interface:

```sh
target/debug/ffsake serve /private/var/folders/.../pocketledger
```

Raw transcripts and scripts remain in the disposable demo directory:

```
/var/folders/1r/f47wxf3900d38thcw0b675sr0000gn/T/opencode/ffsake-demo/
  driver.py
  phase1_discovery.py ... phase13_safety.py
  phase*.log
  tools.json
  tasks.json
  pocketledger/
```

Those files are outside the repository and may be removed by the OS. This report
contains the decision log and the material observations, not every raw byte.

## 11. Overall assessment

FFSAKE feels like a serious editor substrate rather than a shell wrapper. The preview
boundary, content hashes, whole-transaction journal and explicit failure messages are
the strongest parts. It is notably good at making an agent stop and review before
writing, and at refusing stale work.

The main risk is over-trusting the semantic layer. Rust diagnostics and native rename
both produced confident-looking but context-incomplete results; the tool is honest in
its metadata, but an agent must actually read `complete: false` and run the build. The
C++ clangd path was cleaner in this small fixture, though it shares the same snapshot
limits.

For ordinary repository navigation, search, guarded editing, file/directory surgery,
Git review and host-authorized test execution, the workflow held up end to end. The
demo ended with a clean test suite, a fully reversible journal, and no changes to the
FFSAKE repository beyond this report.
