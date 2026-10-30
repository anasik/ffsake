# Debug adapter compatibility — 2026-10-30

These are direct MCP integration exercises, not autonomous-agent debugging sessions. Existing user fixtures were copied to disposable test fixtures; the original work was not changed. The examples have intentional bugs and are not production code.

## Python: debugpy 1.8.17 — launch passed; attach blocked

On macOS 27.0 ARM64 and Python 3.14.6, [debugpy 1.8.17](https://github.com/microsoft/debugpy) ran the payroll fixture via `python -m debugpy.adapter`, with the virtual-environment interpreter path retained. Before debugging, the fixture exited 1 and reported Grace 950 and Ada 1050 instead of 1000. Through FFSAKE, a source breakpoint stopped in `total_pay`; stack/scopes/variables showed `adjustments=[50.0]` for both calls and Ada's `base=1000`. This observation supports the inference that the mutable default list retains Grace's overtime adjustment. Step-over, continue, stale frame rejection, process exit and adapter/debuggee cleanup passed. [MCP trace](debugpy.json).

Local PID attach was **blocked** before attaching: this debugpy advertised `supportsTerminateDebuggee`, but did not advertise the DAP `supportTerminateDebuggee` capability required by FFSAKE's existing safety guard. The separately started target stayed alive. No debugger guard was relaxed. [Attach probe](debugpy-attach.json). The first launch attempt incorrectly resolved the venv interpreter symlink to the system Python and failed to find debugpy; the corrected test retains the venv path.

## C++: Apple lldb-dap — failed on this host

The installed Apple Command Line Tools adapter reports `lldb-1700.0.9.46`. FFSAKE initialized it, then the adapter crashed while launching the symbolized C++ fixture; FFSAKE reported an adapter frame/connection failure and cleaned the session. No breakpoint, frame, variable or stepping success is claimed. [MCP trace](lldb.json). Retaining the compiler's `.o` file produced the same failure ([trace](lldb-retained-object.json)).

An independent `lldb` CLI control, with no FFSAKE or DAP client, also crashed with SIGBUS (`returncode=-10`) during `target create` on the same fixture ([control](lldb-direct.json)). macOS crash reports for both `lldb` and `lldb-dap`, including the earlier direct attach probe, show `DataExtractor::GetULEB128` and `ObjectFileMachO::ParseSymtab` on the faulting stack. This locates the crash in the installed LLDB/symbol-processing environment and rules out FFSAKE as a necessary trigger; it does not establish the underlying Apple bug or prove other LLDB builds will fail. Attachment was not attempted again with this failing adapter. No safety settings or FFSAKE guards were weakened.

## Reproduce

Build `ffsake-mcp`, install debugpy in a disposable virtual environment, then run `scripts/debug_compat_smoke.py BINARY --kind python --adapter /absolute/venv/bin/python --report /tmp/debugpy.json`. For C++, use `--kind cpp --adapter /absolute/lldb-dap`. Run `scripts/debugpy_attach_probe.py BINARY --python /absolute/venv/bin/python --report /tmp/debugpy-attach.json` for the guarded attach check, and `scripts/lldb_control_probe.py --lldb /absolute/lldb --report /tmp/lldb-direct.json` for the independent control. All scripts create disposable targets. Linux CI runs the Python launch and guarded attach checks; the macOS LLDB result is specific to this host.
