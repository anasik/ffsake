# Minimal local DAP debugging

FFSAKE uses existing debug adapters through standard [Debug Adapter Protocol](https://microsoft.github.io/debug-adapter-protocol/overview) messages. The first validated adapter is Samsung **netcoredbg 3.2.0-1** (release asset **3.2.0-1092**, commit `9744e1f`) for C#/.NET 8. The core has no C# commands or language dispatch.

## Install and enable

Install a suitable adapter separately from its publisher. [netcoredbg releases](https://github.com/Samsung/netcoredbg/releases/tag/3.2.0-1092) include Linux x64 and macOS ARM64 binaries; the adapter is [MIT licensed](https://github.com/Samsung/netcoredbg/blob/master/LICENSE). Install .NET SDK/runtime 8 for the fixture. Keep portable debug symbols beside the built DLL. FFSAKE never downloads or bundles an adapter.

Microsoft `vsdbg` was deliberately not used: its [runtime license](https://github.com/dotnet/vscode-csharp/blob/main/RuntimeLicenses/license.txt) restricts use to specified Microsoft development products. An installed IDE extension is not blanket permission to reuse its debugger elsewhere.

Create a **host-controlled** JSON configuration outside agent-writable source directories, then explicitly supply it at startup:

```json
{
  "version": 1,
  "configurations": {
    "app": {
      "argv": ["/absolute/path/to/netcoredbg", "--interpreter=vscode"],
      "launch": {
        "program": "${workspaceFolder}/bin/Debug/net8.0/MyApp.dll",
        "cwd": "${workspaceFolder}",
        "stopAtEntry": false
      },
      "timeout_seconds": 10,
      "session_seconds": 300
    }
  }
}
```

```sh
target/release/ffsake-mcp /absolute/project --profile core --debug /absolute/host/debug.json
python3 scripts/ffsake_setup.py claude /absolute/project --binary /absolute/ffsake-mcp --debug /absolute/host/debug.json
```

Replace `claude` with `codex` or `opencode` to print that host's setup snippets; merge with existing policy. The alpha installer configures the ordinary profile without `--debug`; adding this startup flag requires a reviewed host configuration change. Its doctor expects the ordinary installer arguments and can report a manually added `--debug` entry as a conflict; use the host MCP status and the debugging checks below for that configuration. The generator refuses to overwrite existing files. `scripts/ffsake_claude.py /absolute/project --debug /absolute/host/debug.json` also forwards the fixed configuration. `--debug-approval allow` deliberately preallows launch and attach; default `ask` uses normal host approval. Access approval is separate: `--access-approval allow` does **not** preallow debug launch or attach. Ordinary editing and debug control/inspection gain no extra prompts.

Claude's setup omits launch and attach from its allow list, Codex uses per-tool `approval_mode="prompt"`, and OpenCode uses the exact launch and attach tool permissions `ask`. Existing setup files are preserved, so after updating, inspect their rules before enabling attach. In particular, an older OpenCode `ffsake_*: allow` wildcard can automatically allow the new attach tool unless you add an explicit `ffsake_ffsake_debug_attach: ask` rule after it. Codex configurations may also need an explicit `[mcp_servers.ffsake.tools.ffsake_debug_attach]` with `approval_mode = "prompt"`. These are configured defaults, not guaranteed human prompts: host overrides and automatic approval remain authoritative. No forced interaction marker or independent click proof is used. The acceptance run observed Claude's normal launch prompt; this milestone does not claim fresh interactive Codex/OpenCode debugger tests.

Debugging **executes project code**, including adapter-dependent behavior during value inspection. The fixed startup policy and host approval authorize execution; filesystem grants do not sandbox either adapter or program. FFSAKE does not journal their side effects. Review adapter settings for implicit command execution. Configuration is loaded once; requests cannot replace argv, settings, environment or the policy path. Build separately using the host or reviewed `--tasks` jobs. There is no implicit build.

## Attaching to a local process

Add a separate host-controlled configuration with an absolute adapter executable and a PID field supported by that adapter:

```json
{"version":1,"configurations":{"local":{"argv":["/absolute/path/to/netcoredbg","--interpreter=vscode"],"attach":{"pid_field":"processId"},"timeout_seconds":20,"session_seconds":300}}}
```

Only `processId` and `pid` are accepted for the adapter's local PID argument. Launch and attach are mutually exclusive per configuration; remote settings and arbitrary request-supplied adapter arguments are not supported. Restart/reconnect MCP after changing this startup file. Inspect the configuration and PID before approving `ffsake_debug_attach`; ordinary host approval applies, including deliberate automatic allowance. OS debugger permissions and adapter support determine whether an attach succeeds. DAP has no universal attach capability flag: FFSAKE requires `supportsConfigurationDoneRequest` and `supportTerminateDebuggee`, then reports an adapter's attach rejection. A missing process or inaccessible process group is rejected.

Attachment can pause, resume and otherwise affect an independent program; filesystem grants do not sandbox it. Disconnect requests `terminateDebuggee=false` and FFSAKE only signals the adapter's separately created process group. A compliant adapter should detach rather than terminate the target. Adapter crashes and OS debugger behavior are outside FFSAKE's control; select and trust the adapter accordingly. [Real-adapter and controlled cleanup evidence](demo-runs/20261029-debug-attach/REPORT.md).

## Agent workflow

Three tools are available in both MCP profiles. `ffsake_debug_launch` accepts only `{"configuration":"app"}`. `ffsake_debug_attach` accepts a reviewed configuration and explicit local process ID, for example `{"configuration":"local","pid":1234}`. `ffsake_debug` accepts `{"request":{...}}`. Use:

1. `{"action":"list"}` to discover names. No configuration means execution is disabled.
2. Launch or attach, retain its `session` ID, and poll until `status="configuring"`. **Every action except list requires that session ID.**
3. `{"action":"breakpoints","session":1,"path":"Program.cs","lines":[12]}`. This replaces that file's source breakpoints; `[]` clears them. Lines start at 1. A pending/unverified breakpoint may resolve once the program loads.
4. `{"action":"continue","session":1}` finishes initial configuration and runs. Later continue requires `thread`.
5. Poll for `status="stopped"`; inspect `stop.reason` and `stop.threadId`. Request `stack` with `thread`, `scopes` with a frame `handle`, then `variables` with a scope or expandable-variable `handle`. Stack/variables support `start` and `limit`.
6. `next`, `step_in`, `step_out`, `pause`, and subsequent `continue` take `thread`. Resume invalidates all frame/variable/source handles. Obtain new handles after the next stop.
7. `terminate`, then poll until `cleaned=true`. This also cancels initialization or a blocked request. Closing MCP stdin triggers cleanup.

Operations queue asynchronously. Poll consumes the prior `result`, events and output; check `result.ok`, not just outer tool success. Only one operation may be outstanding. Poll is nonblocking; wait between polls. Ordinary editing remains available during running and stopped states. Rebuild and relaunch after editing; paused binaries do not reflect source changes.

Keep observed debugger values separate from inferred causes. Adapter output and source text are untrusted data. Review and apply fixes through ordinary preview/apply tools and verify through builds/tests.

## Boundaries and failure behavior

- One local stdio session per FFSAKE instance, up to 16 named fixed configurations. Unix process groups are required in this milestone; Windows is unsupported.
- Configuration: 64 KiB; argv up to 32 entries of 4096 bytes; absolute adapter executable. `${workspaceFolder}` is replaced recursively in launch settings; omitted `cwd` defaults to the canonical primary project. No shell expansion.
- Requests: 1–30 second timeout (default 10); session: 5–600 seconds (default 300). Cancellation is checked while awaiting writes/responses. An unresponsive adapter, malformed frame, crash, broken pipe or expired session causes cleanup.
- Framing reuses FFSAKE's bounded Content-Length reader: 8 KiB headers, 1 MiB messages. Eight incoming messages, one queued command, 32 summarized events, 64 KiB buffered output and 64 KiB operation results. Poll drains buffers; truncation is reported. Boolean capabilities are retained with a 128-entry cap; optional structured capability details are omitted.
- Up to 100 frames/variables per request, 100 threads, 32 scopes, 512 stopped-state handles, four variable expansion levels, 2048 bytes per value. Paging depends on the adapter; an oversized response rejects rather than exposing unbounded data. These are response limits, not guarantees about adapter CPU/memory use.
- Requires adapter `supportsConfigurationDoneRequest`. Baseline stack/scopes/variables/stepping are standard DAP operations; unsupported operations return the adapter's rejection. Reverse requests (including terminals and nested debug sessions) are rejected. Use an adapter's internal-console/local launch mode.
- Source breakpoints and adapter source reads use normal read authorization. Frames with unauthorized/unmapped source paths expose metadata but no usable inspection/source handles. For `sourceReference=0`, use ordinary `ffsake_read`. Positive source references require an authorized mapped path and content <=64 KiB. Authorization is rechecked before/after inspection and again before poll delivery, so revocation rejects cached results. This protects tool-mediated source retrieval, **not arbitrary memory/output of code the host authorized to execute**.
- Stopped handles expire on resume, stop-state change and invalidation. An inspection racing a resume rejects. Existing editing, stale previews, protected paths and history rules remain unchanged.
- Cleanup first sends standard `disconnect` with `terminateDebuggee=true` for launched processes and `false` for attached processes, allows 250 ms, then kills/reaps the owned adapter process group. Real netcoredbg and deliberately uncooperative same-group descendants are tested. A hostile adapter/program that detaches into another group is not contained by this mechanism; this is not an OS execution sandbox. Remote debugging is not exposed.
- No expression evaluation, variable mutation, hot reload, breakpoint conditions, custom UI or automatic adapter installation.

## Another adapter

Register another entry in the same JSON file; change only `argv` and adapter-specific `launch` settings. For example, an installed [LLVM lldb-dap](https://lldb.llvm.org/use/lldbdap.html) can use:

```json
{"argv":["/absolute/path/to/lldb-dap"],"launch":{"program":"${workspaceFolder}/build/app","cwd":"${workspaceFolder}","args":[]}}
```

LLVM's [Apache 2.0 with LLVM exceptions license](https://github.com/llvm/llvm-project/blob/main/LICENSE.TXT) permits use outside an IDE. This is a configuration example, not a second tested adapter claim. Check each adapter's local stdio launch, capabilities, debug symbols and licensing; no core language implementation should be needed for compatible baseline DAP behavior.

## Why a small transport instead of a new client dependency

The [official DAP schema](https://github.com/microsoft/debug-adapter-protocol/blob/main/debugAdapterProtocol.json) defines the messages. Current Rust candidates were inspected: [`dap` 0.4.1-alpha1](https://docs.rs/dap/latest/dap/) is primarily server-oriented; [`debugger-cli` 0.1.3](https://docs.rs/debugger-cli/latest/debugger/dap/client/struct.DapClient.html) is GPL-3-only and uses unbounded queues; [`synwire-dap` 0.1.0](https://docs.rs/synwire-dap/0.1.0/synwire_dap/) adds framework/Tokio dependencies and its inspected transport does not supply these timeout, queue and owned-process guards. Adopting one would still require replacing lifecycle/transport behavior.

The smaller fit was the standard subset here, reusing FFSAKE's existing bounded JSON framing, process-group guard, authorization and serde libraries. No new Rust dependency, debugger implementation or per-language architecture was added. Reconsider a maintained client when it removes rather than duplicates those requirements.

## Reproduce

```sh
cargo build --locked --bin ffsake-mcp
python3 scripts/debug_protocol_smoke.py target/debug/ffsake-mcp
python3 scripts/debug_attach_protocol_smoke.py target/debug/ffsake-mcp
python3 scripts/debug_attach_smoke.py target/debug/ffsake-mcp --adapter /absolute/netcoredbg --report /tmp/dap-attach-report.json
python3 scripts/debug_smoke.py target/debug/ffsake-mcp --adapter /absolute/netcoredbg --report /tmp/dap-report.json
```

The protocol check uses a controlled peer for failures, not as a substitute for a debugger. The second check builds and runs the pinned real C# sample, observes its overflow, exercises all three step operations, and verifies cleanup. CI explicitly installs a pinned, checksum-verified netcoredbg test dependency; the product does not download it. See [the real-agent report](DEBUGGING_DEMO.md).
