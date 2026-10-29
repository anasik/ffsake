# DAP acceptance — 2026-10-26

The bounded local C# milestone works with a real adapter and an independent agent. Recommendation: **ready to demonstrate this configured workflow**, with the dependencies and limits below. This is not a general debugger platform or proof of all DAP adapters.

## Project, adapter and configuration

The disposable project used the unchanged MIT-licensed `FibonacciGenerator.cs` from [dotnet/samples at ab42c8a](https://github.com/dotnet/samples/tree/ab42c8a4251cb90787aa5d3c94b78d28d85bc46b/core/console-apps/FibonacciBetterMsBuild). The test driver requests 48 terms and asserts F47 is `2971215073L`; the original example requested only 15. Framework was retargeted to .NET 8, with portable symbols and optimization disabled. See the [fixture provenance](../tests/fixtures/debug-fibonacci/README.md).

- macOS ARM64; installed .NET SDK selected by `dotnet`: 10.0.400, target/runtime .NET 8.
- Samsung netcoredbg release **3.2.0-1092**, executable reports **3.2.0-1 (9744e1f, Release)**, MIT license. Downloaded manually as a test dependency outside the repository; not bundled in FFSAKE.
- macOS release ZIP SHA-256: `f4fa33b3ff874910cc184b4bb3b9c56d0abdf5c6521cee0b144d7c6e4a6e59ea`.
- Launch configuration `fibonacci`: argv `/private/tmp/ffsake-dap-adapter/unpacked/netcoredbg/netcoredbg --interpreter=vscode`; launch program `/private/tmp/ffsake-dap-agent/project/bin/Debug/net8.0/Fibonacci.dll`, cwd `/private/tmp/ffsake-dap-agent/project`, `stopAtEntry=false`, request timeout 20s, session cap 600s.
- Fixed host task policy: `build` runs `dotnet build -v quiet`; `verify` runs that DLL. No request-supplied shell commands.

## Independent agent exercise

Claude Code **2.1.310**, default **Opus 5.5**, ran in a separate disposable session. Native tools were disabled with `--tools ''`; `--strict-mcp-config` exposed only FFSAKE. Normal permission mode, isolated settings sources and `--restricted` were used. The configured launch tool was omitted from the ordinary FFSAKE allow list. Claude presented its native **Ffsake Debug Launch** approval for `configuration: fibonacci`; the host approved the already-authorized exercise. This was ordinary approval, not a forced-interaction marker.

Task supplied only the outcome: diagnose the negative last value using the configured debugger, distinguish observations from inference, minimally fix through FFSAKE, preserve the expected value/48-term check, run configured build/verify and terminate. No implementation or debugger call sequence was supplied.

The agent made **27 FFSAKE calls**, including 17 debug-control calls, one launch, three build/test calls, preview and apply. Its first tool call was at 17:25:47.278 UTC and final answer at 17:26:58.954 UTC: **71.676 seconds**, including host approval wait. This is one observed run, not a performance claim.

Actual debugger observations at the `Program.cs:12` breakpoint:

| Value | Observed value/type |
|---|---|
| `terms` | `int[48]` |
| `actual` | `long`, `-1323752223` |
| `terms[44]` | `701408733` |
| `terms[45]` | `1134903170` |
| `terms[46]` | `1836311903` |
| `terms[47]` | `-1323752223` |

The agent inferred the cause from these observations plus source inspection: `1134903170 + 1836311903 = 2971215073`, beyond signed 32-bit range, and subtracting 2³² gives the observed negative value. Widening in the driver occurs after the generator's overflow. This arithmetic explanation is an inference, not an expression evaluated by the debugger.

The applied [minimal diff](demo-runs/20261026-dap/agent-fix.diff) changes the cache value type, `Fib`, `FibValue` and `Generate` from `int` to `long`. Index parameters and the entire driver remain unchanged. Both the agent's fixed jobs and independent host build/run passed: zero warnings/errors, output `2971215073`, exit 0. This extends the sample to the requested range; it does not make Fibonacci unbounded.

The agent terminated session 1 and observed `status=terminated, cleaned=true`. Independent host process inspection found neither adapter PID 74880 nor the fixture DLL process remaining.

**Actual friction:** two rejected calls, both independently recovered. First poll omitted `session` (`missing field session`); guidance now states every non-list action requires it. A multi-edit `preview_files` on the same path rejected with `batch operations contain conflicting paths`; the agent used one `preview_edit` instead. No guard was weakened, no host implementation coaching and no native repository fallback occurred. Polling adds calls; bounded synchronous inspection in the background is intentionally simple.

Evidence: [deduplicated actual tool calls/results](demo-runs/20261026-dap/agent-tools.json), [agent final answer](demo-runs/20261026-dap/agent-final.txt), [resulting diff](demo-runs/20261026-dap/agent-fix.diff). The full local transcript is retained at `docs/demo-runs/20261026-dap/agent-transcript.jsonl` (not committed; condensed tool evidence is committed).

## Backend verification, distinct from agent usability

The [real-adapter integration trace](demo-runs/20261026-dap/real-integration.json) verifies a source breakpoint and reason, stack line/function, scopes/variables, step into/over/out with observed stop locations, continue, stale-handle rejection, ordinary preview/apply while stopped, natural completion and explicit termination of a second stopped process. Adapter and debuggee PIDs are checked after cleanup. An initial assertion expecting an immediately verified breakpoint was corrected: netcoredbg reports it pending until configuration completes; the check now requires the actual breakpoint stop at the requested file/line.

The existing sample externally prints `-1323752223` and exits 1 before the fix. netcoredbg's natural `exited` event reported 0 in this run; that adapter observation is retained, and is not substituted for the external regression verdict.

Fifteen model-free protocol/lifecycle cases cover output bounds, resume/invalidation during inspection, response correlation, oversized frames, crashes, no response/no reader, missing required capability, cancellation, EOF, session expiry, reverse terminal-request refusal and ordinary reads while running. The controlled peer deliberately ignores disconnect and spawns a descendant: both are killed. Authorization checks cover unauthorized external source metadata without handles, authorized source retrieval, read-only grants, revocation before delivery, and stale handles. Malformed access storage cannot block termination. These tests use a fake protocol peer for controlled failures, not as evidence of actual language debugging.

Local checks: **155 Rust tests**, **89 Python harness tests**, Clippy and formatting; existing MCP/profile, access, persistent-access and workflow smokes pass, including 171 MCP boundary rejections. A sandboxed Rust run initially could not create an existing test's Unix socket; the authorized unsandboxed suite passed unchanged. CI runs the protocol check plus real C# integration with a pinned/checksummed Linux adapter. Final commit CI is the release gate. The first run (40370092368, implementation bd7f07c) stalled before tests on Ubuntu’s Azure HTTP package mirror; it was canceled and retried. The repeated setup delay prompted a CI-only change to the official HTTPS mirror with bounded download waits, retaining every package and test.

## Reproduction and limitations

Use [setup and commands](DEBUGGING.md#reproduce). For a real-agent replay, copy `tests/fixtures/debug-fibonacci` into a disposable project, build it, supply the fixed debug/task policies above with paths adjusted, and use:

```sh
claude --restricted --setting-sources '' --settings /absolute/host/claude.json \
  --strict-mcp-config --mcp-config /absolute/host/mcp.json \
  --tools '' --permission-mode default --no-chrome
```

Host settings allow ordinary FFSAKE tools and configured tasks, leaving `mcp__ffsake__ffsake_debug_launch` to normal approval. See `scripts/ffsake_setup.py` for generated host policy. An empty `--tools` list is part of this acceptance isolation, not required for normal use.

One tested adapter, Unix local stdio, one active session, configurationDone capability required. This launch demonstration did not test attach; see the later [attach evidence](demo-runs/20261029-debug-attach/REPORT.md). No remote, arbitrary evaluate, mutation, terminals or automatic downloads. Authorizing debugging executes code; it is not constrained by FFSAKE file grants. Cleanup covers owned process groups plus cooperative adapter termination, not adversarial detached processes. Source retrieval/inspection rechecks authorization; runtime memory/output is not a filesystem sandbox. Existing editing/history safeguards remain unchanged.

A second compatible adapter uses another fixed `argv`/`launch` JSON entry; [the LLDB example](DEBUGGING.md#another-adapter) needs no language-specific core changes but was not acceptance-tested. Detailed limits, licensing and the client-library decision are in [DEBUGGING.md](DEBUGGING.md). Pause after this milestone.
