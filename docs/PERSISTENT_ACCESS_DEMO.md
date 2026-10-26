# Persistent trusted-Claude access acceptance — 2026-10-26

Implemented after explicit authorization to trust Claude approval enforcement in opt-in trusted-host mode. This resolves the decision recorded in [the earlier probe](NATIVE_APPROVAL_PROBE.md); it does not change its SDK/unauthenticated-client findings.

## User workflow demonstrated

Claude Code 2.1.310 / Opus 5.5, auto mode, existing MCP allow rule, native repository tools disabled, disposable primary project and external sibling files:

1. External read failed. Claude requested exact-file read-only access. The native Yes/No prompt appeared; selecting No left the persisted grant list empty.
2. A second request was approved in the native UI. Claude retried the read successfully. Reading the adjacent unapproved file failed; the parent was never granted.
3. An edit preview failed under read-only rights. A separate native prompt approved read/write expansion. Claude previewed and applied `alpha` → `beta`, then verified the file.
4. Both Claude and its FFSAKE server exited. A new Claude session using the same primary project read the file and listed the existing project grant without requesting access again.
5. The agent requested removal of the exact grant. After native approval, removal succeeded and another read failed. The edit remained; revocation did not undo it.
6. A separate noninteractive Claude invocation attempted open-access. Claude denied it because no approval surface was available; the final store remained empty.

Interactive sessions: `9de01c28-573e-476c-a398-3b391fd86a62` and `b5063605-3849-412f-8ba1-23b18583692c`. Noninteractive session: `8409ea15-301f-43ee-9b54-c6ff0b3dd80e`. Four native prompts were exercised: initial denied open, approved read-only open, approved rights expansion, approved removal. Native tools were disabled; no `/add-dir`, manual grant-file editing or per-file shell authorization was used. The host selected the test approval/denial choices and independently audited fixture bytes and store state.

Raw transcripts, deduplicated tool requests/results, commands, fixture paths and store snapshots are retained locally in `docs/demo-runs/20261026-persistent-access/`. The unapproved neighbor stayed byte-identical and was never returned by a successful read. The raw audit distinguishes an attempted tool use denied by Claude from dispatch to FFSAKE.

## Reproduce

Build and launch against a disposable project:

```sh
cargo build --locked --bin ffsake-mcp
python3 scripts/ffsake_claude.py /absolute/disposable/project --trusted-host --tool-profile core
```

Create two sibling files outside that project using the host. Ask Claude to read one, request exact-file read-only access when denied, and stop if approval is denied. Select No, then explicitly ask again and select Yes. Ask it to attempt the neighbor read without requesting that neighbor's access. Ask for a small edit to the approved file; verify read-only rejection, approve the separate read/write request, and inspect preview/apply. Exit Claude, launch again with the same canonical project, and ask for a read and access listing without creating grants. Finally ask to revoke that exact file, approve removal, and verify a subsequent read is denied.

For an existing MCP configuration use server arguments `[/absolute/project, --trusted-host, claude-code]`, then reconnect after replacing the binary. Existing chats can retain their conversation. Without the explicit flag, reconnecting does not enable the access tools.

## Backend and integration evidence

- Three new Rust tests cover concurrent updates without lost grants, restart/live-instance revocation, read-only mode, disabled creation, invalid-update atomicity, project identity, protected-store access, symlink substitution, preservation of a restrictive primary mode, and revocation of multiple invalid scopes after restart.
- `python3 scripts/persistent_access_smoke.py target/debug/ffsake-mcp` runs actual MCP processes. It verifies conditional catalogs and approval markers; exact file/folder access; neighbor/parent isolation; unknown `approved` arguments; absolute-path/traversal/symlink handling; read-only rejection; rights expansion; server restart; project isolation; protected store; stale apply; revoked cached edit/search/semantic previews; revoked undo/redo without content disclosure or mutation; external archive browsing; cross-root refusal; live revocation between two instances; and conflict rejection with explicit exports. This is a trusted-dispatch simulation, not a native UI test. It is included in CI and passed locally against debug and release builds.
- Full local Rust tests, Clippy, formatting, 86 existing harness tests and the six launcher tests (including the new opt-in/conflict check) passed. Exact-head CI is the final gate and runs the expanded harness suite.
- Release binaries were rebuilt. No global installed `ffsake`/`ffsake-mcp` executable or existing FFSAKE entry in the inspected local/global Claude configuration was found; no unrelated application configuration was changed.

## Boundaries and friction

Ready to demo in explicitly trusted Claude mode. The agent completed each approved transition without fallback tools or implementation coaching. It suggested revoking/regranting to downgrade permissions even though approved open-access with read-only can directly downgrade; guidance documents the shorter path. No correctness blocker was observed in these cases.

The startup flag is an explicit trust declaration. FFSAKE cannot authenticate a human approval from MCP metadata, and SDK callbacks can programmatically approve marked tools. Never enable this mode for an untrusted/nonconforming host. It does not synchronize Claude's filesystem permissions or `/add-dir`; explicit host exports cannot be combined with it. Grants are persistent project grants, not once/session approvals. Primary-project relocation is not transparent; overlapping roots and cross-root transactions remain unsupported. Revocation takes effect on the next request, cannot interrupt an in-flight operation, and cannot erase previously disclosed text from a chat. Restart was tested; no physical machine reboot or power-loss fault injection was performed.
