# Host access acceptance — 2026-10-26

Implementation: `56a52a2`. Setup and precise integration limits: [HOST_ACCESS.md](HOST_ACCESS.md). Full local evidence is retained under `docs/demo-runs/20261026-host-access/`: Claude transcripts, timestamped bidirectional MCP messages, host export/revocation records, commands, verification assertions and test logs. The final exact-head CI result is recorded with that evidence.

## Real Claude session

Claude Code **2.1.310**, reporting **Opus 5.5**, ran in a disposable project with native repository tools disabled. The host supplied synthetic fixtures in two sibling locations: one approved individual file beside an unapproved neighbor, and a separate directory for the live-root experiment. The host performed configuration changes; the agent received outcome-oriented read requests and did not edit authorization state.

Five actual FFSAKE reads produced these results:

1. Before the exact-file host export was activated: denied, with an explicit authorization error.
2. After the host manually exported a session-bound, read-only grant for that file: read succeeded and returned its synthetic content.
3. The neighboring unapproved file: denied. Its contents never appeared in the protocol transcript. The parent was not added as a working directory.
4. After the host used `/add-dir` for the separate directory and selected **Yes, for this session**: its file read succeeded without restarting FFSAKE. The captured protocol includes `notifications/roots/list_changed`, FFSAKE's new `roots/list` request, and Claude's updated list containing the primary and that directory.
5. After the host removed the individual-file grant from the running session's export: a new read of that file was denied, despite the earlier successful read.

This demonstrates exact-file isolation, a real live `/add-dir` scope update, and live manual revocation. It does **not** demonstrate automatic inheritance of Claude's complete permissions or automatic native approval-and-retry. The retry was explicitly requested after the host changed the export. The directory was already declared with a read-only export gated by MCP-root membership; `/add-dir` supplied membership, not its read/write permission.

Claude's actual `/add-dir` dialog offered session, remembered-directory and denial choices. MCP roots did not carry which lifetime was selected. Individual-file one-time/session/project lifetime tests below exercise the explicit host export, not an invented Claude approval API.

## Backend and integration verification

`python3 scripts/access_smoke.py target/debug/ffsake-mcp` passes external exact-file/directory reads, default primary-only search, explicit external search, read-only primary/external restrictions, traversal/symlink/protected-path rejection, denied self-grant arguments, malformed policy fail-closed behavior, project persistence, host-session isolation, durable one-time consumption across restart, and root-refresh pending/removal/re-addition checks. It exercises revoked apply/undo/redo, stale source previews, mixed history with no partial undo, external parent creation/history under a directory grant, ZIP/JAR source reading, and rejected cross-root transactions/context.

The same test invokes real Rust and clangd on external sources, maps results back to absolute identities, performs an external C++ rename, compiles and runs the result, and undoes it. Existing real-server semantic checks also pass. Local validation includes 150 Rust tests, 86 harness tests, Clippy/format, MCP/directory/archive regressions and existing Git/build-test policy checks. CI includes the new access smoke alongside the existing repository and language-server matrix.

Reproduction:

```sh
cargo build --locked --bins
cargo test --locked --workspace
cargo clippy --locked --workspace --all-targets -- -D warnings
python3 scripts/access_smoke.py target/debug/ffsake-mcp
python3 scripts/semantic_smoke.py target/debug/ffsake-mcp
python3 scripts/workflow_smoke.py target/debug/ffsake-mcp
```

For the manual agent exercise, create disposable fixtures and an export following HOST_ACCESS.md, launch `scripts/ffsake_claude.py` with `--access` and `--host-session-id`, request the exact file and its neighbor, then add a separate predeclared directory with `/add-dir`. Revoke a file by atomically replacing the export and request another read. Do not add the individual file's parent to make its read work.

## Friction and limitations

An initial interactive launcher used a heredoc and accidentally presented launcher text as the prompt; it was interrupted with no FFSAKE tool calls. A clean file-based launcher was used for the acceptance session. The first launcher also disabled built-in slash commands, so it was restarted with `/add-dir` enabled. Terminal key encoding required an explicit Enter key event. These orchestration failures are retained rather than omitted.

The running demo build used the original authorization-error prefix; final code removes the misleading “invalid workspace-relative path” prefix for direct authorization errors. Rights enforcement was unchanged by that message fix. The automated smoke was rerun on the final built binary. Observed tool use is not a claim of OS isolation.

Stock Claude does not supply this server with an authoritative complete permission export, file-approval lifetime or native approval callback. Manual host authorization/export/retry is required. Native shell/build tools and installed language-server/toolchain processes retain their separately authorized execution boundaries; this is not a kernel filesystem sandbox. Revocation is checked at request boundaries, not atomically during an in-flight transaction. External search uses explicit targets, grants cannot overlap, and transactions/semantic contexts spanning grant roots are rejected. One-time approval covers one request, so preview and apply need separate approvals or an explicitly authorized session grant.

Recommendation: ready to demonstrate the bounded manual-host integration and live root intersection. Do not present it as transparent synchronization of all native host permissions or automatic native approval-and-retry.
