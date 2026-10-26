# Native approval verification — 2026-10-26

Historical checkpoint: the user subsequently authorized an explicitly enabled trusted-host mode that trusts Claude approval enforcement. The negative-control findings below still define that trust boundary; see [current setup](HOST_ACCESS.md).

Status: the Claude terminal approval mechanism passed the minimal probe. The supported-host authentication boundary is **not established**, so persistent grant creation has not been implemented or enabled. Existing external-access exports and all workspace guards remain unchanged. This is a verification checkpoint, not completion of the persistent-access feature.

## Observed behavior

Tested Claude Code 2.1.310 / Opus 5.5 in a disposable project, with native tools disabled, auto mode enabled, an existing allow rule for the probe, and both PreToolUse and PermissionRequest hooks returning allow. The probe only records dispatch; it neither reads external paths nor creates grants.

| Case | Result |
| --- | --- |
| First interactive call | Native Yes/No prompt appeared; no server dispatch while pending |
| Select No | Call interrupted; no server dispatch and no grant |
| Second interactive call | Native Yes/No prompt appeared again |
| Select Yes | Exactly one server dispatch with the requested arguments |
| Noninteractive `-p`, auto mode, same allow rule/hooks, prompts disabled | Permission denial; no server dispatch |
| Official Python SDK 0.2.174, same Claude 2.1.310 binary, automatic can_use_tool allow | Dispatched without a human prompt; same MCP client identity and call metadata structure |
| Client replay without Claude or any approval UI | Identical initialization and approved-call payload dispatched successfully to the probe |

Both hooks were observed, and neither skipped the terminal prompt. This does not exhaustively test every hook configuration, updated-input hook behavior, remote UI, version, or permission mode. The noninteractive agent incorrectly suggested adding an allow rule as a remedy; the rule already existed. Inspect actual dispatch and permission-denial records, not the agent's explanation.

Interactive session: `3df4528c-974b-44b1-8664-882b82d73302`. Noninteractive session: `75f91f97-0f17-4e88-b676-7e4b29ff2b06`. Local raw evidence is retained under `docs/demo-runs/20261026-native-approval/`: protocol traffic, hook inputs, execution records, Claude transcripts, launcher arguments, noninteractive stream and replay results. These traces are not an FFSAKE external-file acceptance run.

## What the marker proves and does not prove

The [official MCP documentation](https://code.claude.com/docs/en/mcp#require-approval-for-a-specific-tool) specifies Boolean `_meta["anthropic/requiresUserInteraction"]: true` on the tool catalog entry. It requires a prompt despite allow rules and auto permission modes. It also explicitly permits the Agent SDK's `canUseTool` callback to approve such calls programmatically. [Hook documentation](https://code.claude.com/docs/en/hooks) describes the corresponding PreToolUse restriction.

The SDK negative control actually dispatched the marked tool with an automatic callback and no human UI (session `38d08931-e762-466e-a85c-f5d2fcf56d76`). Its complete initialize parameters matched the interactive session. This confirms the documented exception with the same installed Claude executable, rather than merely assuming SDK behavior.

The captured approved request contains the tool arguments, a tool-use ID and a progress token. It contains no authenticated human-approval receipt. MCP clientInfo is self-reported. Replaying the request demonstrates that a server trusting only the marker, client name/version or tool-use ID would accept an unapproved invocation. This is a limitation of that proposed gate, not a vulnerability in the existing FFSAKE server: the probe is deliberately permissive and production FFSAKE has no grant-creation tool.

Pinning a Claude version alone would not distinguish an interactive terminal from SDK use of that version. Environment variables and a server opt-in flag are host declarations, not independently authenticated per-call approvals. A trustworthy, separately controlled launcher/transport could potentially constrain which client may invoke grant creation; that boundary has not been implemented or validated here. These results do not prove that such a design is impossible. Do not advertise universal MCP support or ship a clientInfo-only grant gate.

## Reproduction

These are development probes, never an authorization bridge. Run from the repository with Python 3 and the tested Claude installed. The interactive/noninteractive commands use the configured Claude account and incur model usage. Use a fresh output directory for each reproduction.

```sh
export FFSAKE_PROBE_OUTPUT="$(mktemp -d)"
python3 scripts/approval_probe/launch_probe.py
```

Trust the disposable fixture. Ask Claude to call `ffsake_access_probe` once with path `/tmp/synthetic-approved.txt`, kind `file`, mode `read_only`; select No. Ask for the same call again and select Yes. Check that `probe-executions.jsonl` has exactly one entry. Exit Claude before continuing:

```sh
python3 scripts/approval_probe/launch_probe.py --print > "$FFSAKE_PROBE_OUTPUT/noninteractive.jsonl"
# Execution count must remain one; stream result contains permission_denials.
python3 scripts/approval_probe/replay.py "$FFSAKE_PROBE_OUTPUT/probe-protocol.jsonl"
```

The replay uses a fresh isolated output directory and asserts that a client ignoring the marker can dispatch the harmless probe. It performs no model call and grants no filesystem access.

Optional SDK reproduction (also incurs model usage): install `claude-agent-sdk==0.2.174` in a disposable virtual environment, set `FFSAKE_PROBE_OUTPUT` to a separate empty directory, and run `python scripts/approval_probe/sdk_probe.py`. The callback deliberately approves only this harmless probe without UI; never use it as a real authorization hook.

## Unfinished scope / release gate

Persistent open/list/remove operations, protected atomic storage, restart/project isolation, rights expansion and revocation acceptance are not implemented by this checkpoint. No claim is made that these new requirements passed. Existing host-export functionality is documented in [HOST_ACCESS.md](HOST_ACCESS.md).

Before access expansion ships, establish and test a supported-client boundary that rejects ordinary/unknown/SDK dispatch without human approval, or explicitly resolve the trust-model requirement. Then implement persistence and run the requested real external-file workflow, revocation/history checks and exact-head CI. Do not replace that prerequisite with an agent-supplied approval field, elicitation, MCP roots, or a permissive hook.
