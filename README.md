# FFSAKE

FFSAKE gives coding agents repository tools through MCP: find and read files, review and apply guarded edits, and undo or redo editor transactions. It also provides reviewed Git operations and named build/test tasks. It is an alpha project; host approval settings still control tool use, and task execution is not sandboxed.

**Quick setup:** from the project you want to use, give your agent the [repository URL](https://github.com/anasik/ffsake) and ask it to follow [the installation guide](docs/INSTALL.md). Or run the commands below. You need Git, Python 3.11+, stable Rust/Cargo, and a C compiler/build tools.

```sh
# Run from your project. Reuse an existing source checkout if present.
PROJECT_DIR="$(pwd -P)"
FFSAKE_SOURCE="$HOME/.local/src/ffsake"
git clone https://github.com/anasik/ffsake.git "$FFSAKE_SOURCE"
cd "$FFSAKE_SOURCE"
python3 scripts/ffsake_install.py install
python3 scripts/ffsake_install.py configure --host claude --project "$PROJECT_DIR" --guidance          # review
python3 scripts/ffsake_install.py configure --host claude --project "$PROJECT_DIR" --guidance --apply  # apply
python3 scripts/ffsake_install.py doctor --host claude --project "$PROJECT_DIR"
```

Install the binaries once per machine; configure each host and project you want to use. Repeated setup reuses matching installations and entries without silently upgrading. The optional `--guidance` flag previews a marked, idempotent addition to the host's project instructions. Replace `claude` with `codex` or `opencode` for those hosts. Trust or approve the MCP server if your host asks. In an existing Claude chat, use `/mcp` → `ffsake` → **Reconnect**; a new chat in the configured project needs no setup. [Full setup, updates and limitations](docs/INSTALL.md).

## Contents

- [Supported agents and platforms](#supported-agents-and-platforms)
- [Everyday usage](#everyday-usage)
- [Optional build, debugging and language tools](#optional-build-debugging-and-language-tools)
- [Update and uninstall](#update-and-uninstall)
- [Troubleshooting](#troubleshooting)
- [Reference and project status](#reference-and-project-status)

## Supported agents and platforms

The source installer and MCP configuration cover Claude Code, Codex and OpenCode 1.x. Isolated checks recognized Claude Code 2.1.312 and Codex CLI 0.163.0 configurations and connected OpenCode 1.19.5. A fresh Codex agent completed an installation; interactive Claude approval was not tested in that installation exercise. See the [acceptance report](docs/demo-runs/20261029-alpha-install/REPORT.md).

Source installation has been exercised on macOS and in Linux CI. Windows is not supported by the installer or current process-group runner. No downloadable release binaries were published when the installation path was audited. FFSAKE runs as a host-managed MCP stdio process; it does not require a daemon.

## Everyday usage

Ask your agent to use FFSAKE to explore and edit your project. For example: “Find where requests are validated, propose the smallest fix, show me the diff, and run the project's test task.” The server can search, read ranges, preview changes, apply them with stale-content checks, and undo or redo complete transactions, including after reopening history; stale state rejects. Editor history does not reverse Git operations, task effects or debuggee side effects. A search returning no matches does not always prove absence: indexed search has hidden-file and ignore exclusions; [explicit directory search and archive browsing](docs/ARCHITECTURE.md#hidden-source-search-and-zipjar-inspection) cover bounded searches and ZIP/JAR source reads.

The project directory is the default file scope. To open an external file or folder, ask the agent to use the access tools; the host's normal approval policy applies. Grants can be read-only or read/write and persist until removed. A file grant does not open its parent directory. Explicit restrictive access exports remain authoritative. See [host access and its limits](docs/HOST_ACCESS.md).

For Git, the agent can inspect changes, review selected-file staging and commits, and review branch creation, switching, fetch and non-force push before executing them. Host approval applies to mutations and network operations. Force push, tags, pull, merge, rebase and arbitrary refspecs are outside this scope; some executable Git configurations are refused. See [Git and task workflows](docs/CLI.md#git-review-and-host-configured-buildtest-jobs).

## Optional build, debugging and language tools

- **Build and test:** Ask the agent to list existing tasks or propose a named process task. Task registration and execution follow host approval; task authorizations persist per project and definitions are revalidated before each run. Output can be read incrementally and running tasks cancelled. Tasks execute project code, can change files, and are neither sandboxed nor part of editor history. Explicit restrictive startup task policies remain authoritative. [Task workflow and limits](docs/CLI.md#git-review-and-host-configured-buildtest-jobs).
- **Debug:** A configured external DAP adapter can launch a local program or attach to an explicitly selected already-running local process, set breakpoints, inspect stack frames and variables, step, pause and continue. Attachment uses host approval and detaches without requesting termination of the independent target. C# with netcoredbg and Python with debugpy have passed local launch tests; the installed Apple LLDB adapter crashed during C++ launch and is not yet validated. See the [compatibility report](docs/demo-runs/20261030-debug-compatibility/REPORT.md). FFSAKE does not bundle it. [Setup and limitations](docs/DEBUGGING.md) · [demonstration](docs/DEBUGGING_DEMO.md).
- **Language intelligence:** The full MCP profile supports bounded semantic navigation, diagnostics and reviewed rename proposals through configured Rust and C++ language servers. Language servers are optional dependencies; other servers have not been established as supported. Results depend on supplied project context; external builds and tests are still needed. Ordinary editing does not require a language server. [Semantic interface and limitations](docs/CLI.md#shared-language-intelligence).

These capabilities are implemented and tested within their documented bounds. Broader adapters, sandboxing and plugins remain planned, not guarantees of the current alpha. See [PLAN.md](PLAN.md) for the roadmap and evidence.

## Update and uninstall

Inspect the source checkout for local changes, pull without discarding them, and explicitly authorize an update:

```sh
git -C "$FFSAKE_SOURCE" status --short
git -C "$FFSAKE_SOURCE" pull --ff-only
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" install  # reports available revision
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" update
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" doctor --host claude --project "$PROJECT_DIR"
```

Reconnect an existing MCP session after the update. To remove the managed binaries, run `python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" uninstall`. This leaves project files, journals, the source checkout and host configuration in place; remove only the FFSAKE host entry separately if desired. [Full update and uninstall instructions](docs/INSTALL.md#update).

## Troubleshooting

- **Server missing or disconnected:** Run the `doctor` command above for your chosen host and project. It checks binary integrity and performs MCP initialization and tool discovery without model calls. It cannot verify host trust, managed configuration overrides or an already-running connection. Check your host's trust/approval settings and reconnect.
- **Existing entry or configuration conflict:** Setup refuses conflicting or duplicate FFSAKE entries rather than overwriting them. Review the reported file manually. Existing OpenCode project `opencode.jsonc` also requires a manual merge. See [configuration details](docs/INSTALL.md#configure-a-project).
- **Build fails or an optional tool is missing:** Check the prerequisites above. Install and configure a language server or debug adapter only if you need that feature; they are not bundled.
- **An edit or undo is stale:** Read the current files and prepare a fresh preview. Undo reverses an entire transaction and can reject if later changes touched it; selectively reversing an earlier change requires a new reviewed edit. [Mutation details](docs/ARCHITECTURE.md#mutation-model).

## Reference and project status

- [Installation, configuration, doctor, update and uninstall](docs/INSTALL.md)
- [MCP and resident CLI contracts](docs/CLI.md)
- [Architecture and development reference](docs/ARCHITECTURE.md)
- [Benchmark methodology and evidence](docs/BENCHMARK.md)
- [Roadmap and development record](PLAN.md)

FFSAKE uses [FFF](https://github.com/dmtrKovalenko/fff) as its search and indexing substrate.
