# Host-controlled access acceptance — 2026-10-26

## Scope and policy

Default MCP persistent access, ordinary host approval, no forced Claude interaction marker, no required trusted-host flag. Host allow/auto decisions are intentional authorization. Explicit exports remain authoritative and exclude persistent grants. See [setup](HOST_ACCESS.md).

## Installed-host observations

| Host | Normal configuration | Intentional automatic configuration | Restart and revocation |
| --- | --- | --- | --- |
| Claude Code 2.1.310 | Native Yes/No for exact-file open; No left grants empty; approved retry read the fixture. Ordinary reads did not prompt. | Generated explicit allow rules dispatched open/remove without a prompt. | New session/server read the prior grant, neighbor denied, revoke/read denied, reopen/read succeeded, final revoke/read denied. |
| Codex CLI 0.163.0 | Per-tool prompt produced native Allow/Cancel; cancellation granted nothing; approved retry read the fixture. Ordinary reads did not prompt. | Per-tool `approval_mode = "approve"` dispatched open/remove without prompts. | New session/server read prior grant, neighbor denied, revoke/read denied, reopen/read succeeded, final revoke/read denied. |
| OpenCode 1.18.31 | Exact-tool ask produced native Allow once/Allow always/Reject. Rejection left grants empty; Allow once dispatched the persistent grant. | `--mini --auto` still prompted with explicit ask rules for open/remove. Generated explicit allow completed open/read/remove/denied-read without prompts. | New session/server read the prior grant, neighbor denied, approved revocations blocked subsequent reads. |

These are observations of installed versions and settings, not guarantees that every host policy prompts. Claude auto classifier and Codex automatic reviewer were not separately measured; explicit allow configurations exercise intentional unattended dispatch. No client identity checks or custom approval UI were added. No reboot was performed; separate process/session restart and on-disk durability were tested. Backend checks cover read-only mutation rejection, rights expansion, project isolation and stale/revoked preview/history protection; those were not repeated manually in every host.

## Reproduction

Use a disposable directory with `project/`, sibling `external/approved.txt` containing `host approval fixture`, and `external/neighbor.txt` containing a distinct sentinel. Build the MCP binary, then generate the chosen host setup into the project:

```sh
cargo build --locked --bin ffsake-mcp
python3 scripts/ffsake_setup.py claude /absolute/fixture/project --binary /absolute/ffsake/target/debug/ffsake-mcp --output-dir /absolute/fixture/project
# Substitute codex or opencode for the other hosts.
```

Start the host in that project. Trust the disposable project if the host asks. For the normal check ask: “Using only FFSAKE, try reading [absolute approved.txt]. If denied, request exact-file read_only access. If the host denies that request, stop without retrying.” Reject once, verify the protected store has zero grants externally, ask to retry, approve, and verify the read. Never tell the model that approval already happened.

Quit the host/server. Start a new session and ask to read the file without opening access again, try the neighbor without granting it, remove the original grant, verify denial, reopen/read, remove again and verify final denial. To test intentional allowance, generate fresh configuration with `--access-approval allow` (or merge those changes in the disposable configuration) and repeat the cycle. OpenCode's tested `--auto` behavior should not be assumed to override its explicit ask rules.

The Claude test used `--tools ""` to disable native repository tools, strict MCP configuration and isolated settings. Codex/OpenCode were instructed to use only FFSAKE; native-tool isolation was not enforced for those hosts. The recorded tool traces distinguish actual use from this instruction. Host launch commands, fixture paths, raw transcripts and store evidence are retained locally under `docs/demo-runs/20261026-host-approval/`; they are not checked into Git. Codex required waiting for its update and folder-trust startup dialogs before submitting the task; two premature submissions exited without running a task. OpenCode emitted an incidental keymap warning at exit.

## Automated checks

- Rust workspace tests: 153 passed; clippy with warnings denied and rustfmt passed.
- Python harness/setup tests: 88 passed, including generated normal/allow configurations and refusal to overwrite existing setup.
- Persistent MCP smoke: default catalog without forced marker; legacy startup compatibility; restart, live second-instance revocation, exact file/directory isolation, read-only rights, stale previews, revoked history/handles, archives, semantic context, protected store, cross-root rejection and restrictive-export precedence.
- Explicit-export suite: modes/lifetimes, live MCP roots, external semantic analysis and authorization boundaries.
- MCP editing smoke: previews/apply/undo/redo and 171 rejected boundary cases. Workflow and search/archive suites passed.
- Initial sandboxed Rust mode/symlink and workflow process-inspection checks hit OS permission errors; rerunning those checks with authorized host permissions passed. No verifier was weakened.

CI status will be reported for the exact pushed commit. This scope does not expand semantic or other features.
