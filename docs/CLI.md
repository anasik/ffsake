# Resident agent interface

Build with `cargo build --bin ffsake`, then start `target/debug/ffsake serve /absolute/workspace` once. Keep its stdin/stdout pipes open across tool requests. The explicit root is trusted startup configuration; request paths remain workspace-relative. EOF terminates the process. No TCP listener, shell execution, build execution, LSP, or orchestration is included.

Requests and responses are newline-delimited JSON. Each request produces one flushed response, in order: `{"ok":true,"result":...}` or `{"ok":false,"error":"..."}`. Errors do not terminate the session. Startup failures exit nonzero. Requests exceeding 1 MiB are drained and rejected. Diagnostics use stderr. Use JSON serialization, not shell string interpolation.

## Search → review → apply → undo

Send each line after reading the preceding response; substitute returned handles/transaction IDs:

```json
{"op":"search","pattern":"needle","limit":1000}
{"op":"preview_matches","handle":1,"replacement":"replacement","expected_matches":400}
{"op":"apply","handle":2}
{"op":"undo","transaction":"RETURNED_ID"}
```

Search returns a resident MatchSet handle, match/file counts, and FFF's `next_file_offset`. Set `details:true` to include path-qualified byte ranges. It does not echo file contents or hashes. Preview returns a separate handle plus existing per-file unified diffs. Apply consumes that preview on success and returns a persistent journal transaction ID and changed-file count. Failed apply retains the preview for inspection/retry; stale input requires searching/previewing again. Replacement text is literal, including for regex search.

Search defaults to literal, case-sensitive mode, 100 results, offset 0. Set `regex:true` for regex. Limits are 1–1000. A MatchSet covers the returned page only: expected_matches applies to that page, not every possible repository match. Continue with the returned file offset according to FFF pagination; never interpret a page as proof of repository-wide completeness. Target filtering happens after FFF paging and can produce empty scoped pages. After CLI apply/undo/redo, the next search automatically waits for an index refresh. `refresh` explicitly waits for a full FFF rescan when external changes or watcher latency matter; it does not update existing handles.

## Other requests

| Request | Behavior |
| --- | --- |
| `{"op":"read","path":"src/lib.rs","start":1,"end":40}` | Ranged read, line numbers are one-based |
| `{"op":"read_files","files":[{"path":"src/lib.rs","start":1,"end":40},{"path":"AGENTS.md"}]}` | Ordered bounded reads with explicit `ok`/`missing` status |
| `{"op":"list","path":"."}` | Safe directory listing |
| `{"op":"target","target":{"include":[{"Glob":"src/**/*.rs"}],"exclude":[]}}` | Resolve sticky search scope; explicit search target overrides it |
| `{"op":"redo","transaction":"ID"}` | Journal redo with state verification |
| `{"op":"release","handle":1}` | Release MatchSet or preview storage |
| `{"op":"refresh"}` | Wait for index refresh |

A selector has `include` and `exclude` arrays. Elements are `"All"`, `{"Exact":"path"}`, `{"Glob":"pattern"}`, or `{"Regex":"pattern"}`. Empty includes select nothing. Set an All target to reset sticky scope. Exact directories select descendants.

For mutations beyond MatchSet replacement, `preview` accepts the existing `Transform` data structure, without a second scripting language. For example:

```json
{"op":"preview","transform":{"target":{"include":[{"Exact":"src/lib.rs"}],"exclude":[]},"operations":[{"ReplaceIdentifier":{"from":"Old","to":"New","expected_matches":2}}],"max_files":1}}
```

The same representation exposes range/position/line mutations, match-relative insert/delete, hash assertions, and file deletion from `transform.rs`. Enum names and fields follow that Rust API exactly. Client-authored generic transforms are still validated by the existing runtime. Selections can be expressed as path-qualified ApplyRanges; higher-level selection planning remains in the library.

All preview plans freeze selected file membership and assert source hashes again when applied. Existing workspace confinement, internal namespace protection, range validation, max-file checks, and journal commit/undo guards remain in force. The CLI validates transaction IDs before calling the journal. Handles are process-local, monotonic, and limited to 128 live MatchSets/previews combined; release unused handles. Journal transaction IDs survive restarts. The client owning the process is trusted to mutate the selected workspace; this is not a multi-user authorization service.

## Batched reads and edits

`read_files` accepts 1–32 explicit `{path, start?, end?}` entries. Each returned `files` entry has the requested `path` and either `status: "ok"` with a `read` object (the single-read result), or `status: "missing"`. Read source files are limited to 256 KiB each, path strings to 4096 UTF-8 bytes, and total returned text to 1 MiB before JSON escaping. Limits reject rather than truncate. Ranges use positive one-based line numbers; reversed ranges reject. Unsafe paths, symlinks, directories, non-UTF-8 contents and limits fail the whole call. Reads are sequential, not a snapshot of simultaneous filesystem state. Single-file `read` retains its previous behavior.

Use `preview_edit` for a known existing UTF-8 file:

```json
{"op":"preview_edit","path":"src/lib.rs","old_text":"OldName","new_text":"NewName","expected_matches":2}
```

For several independent changes use a single mixed preview, then apply its returned handle:

```json
{"op":"preview_files","operations":[{"op":"edit","path":"src/lib.rs","old_text":"OldName","new_text":"NewName","expected_matches":2},{"op":"create","path":"docs/MIGRATION.md","content":"Use NewName.\n"}]}
```

The 1–100 operations are `create(path, content)`, `copy(from, to)`, `move(from, to)`, `delete(path)`, and `edit(path, old_text, new_text, expected_matches)`. All refer to the initial workspace state. Overlapping paths and dependencies reject; destinations must be absent. Edits require a nonempty old string and a positive exact count. One apply produces one undoable journal transaction. The individual `preview_create`, `preview_copy`, `preview_move`, and `preview_delete` requests remain supported.

MCP tools use the same names prefixed with `ffsake_`, omitting the `op` argument. Generic `preview` is available in resident JSONL; MCP exposes the focused preview tools. Tool errors leave the session usable.

## History and filesystem guarantees

Existing-file replacements preserve current permissions. On Unix, new journal entries capture ordinary rwx bits for recreated files; copy/move preserves source rwx bits and rejects source permission changes after preview. Older manifests lack metadata they never captured. Ownership, ACLs, xattrs and special mode bits are not restored.

Undo/redo verifies current workspace state and both saved snapshot sides before writing, then rechecks snapshot hashes when used. Missing/corrupt or symlinked journal data rejects replay. Ordinary late write or final manifest failures roll back earlier members; a failed rollback is explicitly reported with affected paths. Remove the external obstruction before retrying a successfully rolled-back operation. Do not treat an incomplete rollback as success.

Temporary files are reserved exclusively: a pre-existing file, link or directory at the temporary name rejects the write and remains untouched. These guards do not provide power-loss atomicity or an OS sandbox against hostile concurrent filesystem changes.

## Reproducible validation

```sh
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all -- --check
cargo build --bin ffsake
python3 scripts/benchmark.py target/debug/ffsake
cargo build --locked --bin ffsake-mcp
python3 scripts/mcp_smoke.py target/debug/ffsake-mcp
python3 scripts/repository_smoke.py target/debug/ffsake-mcp
```

The benchmark runs the actual JSONL executable, checks protocol recovery and safety failures, then runs ten search → preview → apply → undo cycles on 200 files, 20 changed files, and 400 UTF-8 byte-range matches. Every changed byte and restored file is checked. It compares against four shell requests using grep plus Python/difflib and explicit undo snapshots. Both retain a review boundary before apply. Startup is reported separately; filesystem validation overhead is included in both timings. The shell baseline is a concrete implementation, not a universal measure of shell performance or model token usage. One shell script could batch phases if no agent review is needed; one piped CLI stream could do the same. No general round-trip advantage is claimed from this fixture alone.
