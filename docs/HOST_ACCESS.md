# Host-authorized filesystem access

The host controls approval. Normal settings can prompt; deliberate allow rules and automatic approval modes can dispatch access tools without a human click. FFSAKE accepts that host decision and does not authenticate clients or demand an approval receipt. Starting MCP with a primary project authorizes that project read/write; external access is initially absent.

## Persistent open-file / add-folder workflow

```sh
ffsake-mcp /absolute/project
# Interactive Claude launcher with ordinary tools preapproved:
python3 scripts/ffsake_claude.py /absolute/project
```

Ask the agent to open an external file or add a folder. After an out-of-scope read, it calls `ffsake_open_access` with absolute `path`, `kind: "file"` or `"directory"`, and `mode: "read_only"` or `"read_write"`. The host applies its configured permission policy. Successful dispatch persists that exact scope until revoked, and the agent retries the original operation. Denial before dispatch creates no grant. A file grant never includes its parent or neighbors. Rights expansion requires another open-access invocation subject to the same host policy; opening read-only can also downgrade an existing grant.

`ffsake_list_access` lists grants. `ffsake_remove_access` revokes the exact path without deleting files or reversing edits. Both mutating access tools use ordinary host approval; there is no Claude forced-interaction marker. **A host's “Allow once” approves one tool invocation; this tool explicitly creates a persistent project grant.** It does not mean a one-read grant. Use explicit exports below when host-managed once/session lifetimes are required.

All three tools appear by default in both MCP profiles. JSONL does not create persistent grants. Old `--trusted-host claude-code` startup arguments and the launcher's `--trusted-host` remain accepted as deprecated no-ops. Remove them from new setup. Reconnect the server after replacing its binary; existing chats can retain their conversation.

## Host setup

Build `cargo build --release --bin ffsake-mcp`, then generate the configuration for the chosen host:

```sh
python3 scripts/ffsake_setup.py claude /absolute/project --binary /absolute/ffsake/target/release/ffsake-mcp --output-dir /absolute/project
python3 scripts/ffsake_setup.py codex /absolute/project --binary /absolute/ffsake/target/release/ffsake-mcp --output-dir /absolute/project
python3 scripts/ffsake_setup.py opencode /absolute/project --binary /absolute/ffsake/target/release/ffsake-mcp --output-dir /absolute/project
```

Run only the desired host command. Existing files are never overwritten: omit `--output-dir` to print snippets for merging into existing configuration. Review inherited wildcard allow/deny rules and managed policy. The generator does not change them. `--access-approval allow` deliberately configures automatic allowance of open/remove; `--tool-profile core` selects the smaller catalog.

| Host | Generated setup | Normal access approval |
| --- | --- | --- |
| Claude Code | `.mcp.json`, `.claude/settings.json` | Preallows ordinary FFSAKE tools; open/remove use default MCP permission handling. No explicit ask rules that would override deliberate allow/auto behavior. |
| Codex | `.codex/config.toml` | Per-tool `approval_mode = "prompt"` for open/remove, `"approve"` for ordinary tools. Trust the project to load project config. Deliberate per-tool `"approve"` or host automatic review controls unattended use. |
| OpenCode | `opencode.json` | `ffsake_*: allow`, exact `ffsake_ffsake_open_access` and `ffsake_ffsake_remove_access`: `ask`. Use deliberate `allow` for unattended calls. In tested 1.18.31, `--auto` still prompted with these explicit `ask` rules. |

These are **configured defaults, not guaranteed prompts**. Global/managed policy, hooks, SDK callbacks, auto modes and user overrides remain the host's responsibility. Ordinary reads/edits gain no new approval requirement. The launcher additionally supports `--access-approval allow` and an optional `--permission-mode`; otherwise it preserves the host mode.

Sources checked before implementation: [Claude permissions](https://code.claude.com/docs/en/permissions), [Codex configuration reference](https://developers.openai.com/codex/config-reference), [OpenCode permissions](https://opencode.ai/docs/permissions/), [OpenCode MCP configuration](https://opencode.ai/docs/mcp-servers/). Installed-version observations and reproduction are in [HOST_APPROVAL_DEMO.md](HOST_APPROVAL_DEMO.md).

This does not synchronize native filesystem permissions, Claude `/add-dir`, or arbitrary host deny rules. An operator needing restrictive filesystem policy should use `--access`: it is authoritative, persistent grants are not loaded or merged, and access management tools are absent/rejected. Even legacy `--trusted-host claude-code --access ...` preserves the export unchanged. Tool approval never broadens an explicit export.

## Persistence and revocation

The canonical primary project owns `.ffsake/native-access/grants.json` and its OS lock. The journal anchor stays at that project. Atomic private-file replacement and file/directory sync persist grants across server/session restart and reboot; same canonical project instances share grants, different primary projects remain isolated. Moving the primary directory changes identity. The protected `.ffsake` store cannot be read or edited through ordinary FFSAKE tools; native OS access is a separate host capability.

Running instances reload before operations. Revocation blocks subsequent reads, previews, apply, undo/redo and cached-handle access. It cannot erase content already returned or interrupt an in-flight operation. Grants remain disjoint; overlaps and cross-root transactions reject before mutation. Revoke a narrow grant before adding a broader folder. Search external folders with explicit Exact targets; primary indexing does not move. Existing symlink, traversal, stale-content, history and transaction guards remain unchanged.

## What Claude Code communicates

Claude Code documents `roots/list` as its launch directory plus additional working directories from `--add-dir`, `/add-dir`, and settings, with `notifications/roots/list_changed` when that set changes. FFSAKE negotiates MCP 2025-11-25, requests roots after initialization when an access export is configured, and refreshes them on notifications. Root-dependent rights are suspended immediately while refresh is pending; malformed/error responses leave those rights disabled. Older replies cannot restore superseded roots.

A root contains a URI and optional name, **not** a read/write mode, native Read/Edit deny rules, one-time file approvals, approval lifetime, or the complete host policy. For the explicit-export integration, Claude's MCP API does not expose an authoritative filesystem permission query or a native filesystem approval-and-retry API to this server. `PermissionRequest` hooks decide tool calls; elicitation collects user input. Neither is treated as a filesystem grant. We do not parse private settings/transcripts to guess approvals or install a hook that automatically approves them.

Consequently, `/add-dir` alone does not grant new FFSAKE rights. It activates an already host-exported grant marked `require_mcp_root`, and removal deactivates it. This is live scope intersection, not automatic synchronization of Claude's complete permission policy. An external directory without an explicit export still fails closed.

Sources: [Claude MCP roots](https://code.claude.com/docs/en/mcp), [Claude permissions](https://code.claude.com/docs/en/permissions), [permission hooks](https://code.claude.com/docs/en/hooks), [MCP roots specification](https://modelcontextprotocol.io/specification/2025-11-25/client/roots).

## Explicit manual host authorization

For a host that cannot export its complete policy, the human/operator must supply a minimal authorization export. This is a fallback host declaration, not a second permission decision made by FFSAKE. The host must honor its native restrictions and deny rules when exporting rights. Store the file outside the primary project and outside every grant; protect it from the agent's other tools and OS capabilities. FFSAKE rejects exports inside its accessible roots and has no tool to modify them. Native shell/build execution is a separate host capability and is not sandboxed by these file grants.

Start with:

```sh
FFSAKE_HOST_SESSION_ID=host-generated-unique-session-id \
  ffsake-mcp /absolute/project --access /host-controlled/authorization.json
# JSONL equivalent:
FFSAKE_HOST_SESSION_ID=host-generated-unique-session-id \
  ffsake serve /absolute/project --access /host-controlled/authorization.json
# Interactive launcher forwards the same settings:
python3 scripts/ffsake_claude.py /absolute/project \
  --access /host-controlled/authorization.json \
  --host-session-id host-generated-unique-session-id
```

Example export (all paths must be absolute, with canonical non-symlink ancestors):

```json
{
  "version": 1,
  "project": "/absolute/project",
  "primary_mode": "read_write",
  "grants": [
    {
      "path": "/shared/library",
      "kind": "directory",
      "mode": "read_only",
      "lifetime": "session",
      "session_id": "host-generated-unique-session-id",
      "require_mcp_root": true
    },
    {
      "path": "/private/notes/approved.txt",
      "kind": "file",
      "mode": "read_write",
      "lifetime": "once",
      "session_id": "host-generated-unique-session-id",
      "approval_id": "host-generated-unique-approval-id"
    }
  ]
}
```

The second entry authorizes only that file, never siblings or parent listing. File grants may name a missing file for creation, but do not authorize creating missing parent directories. Directory grants must exist. Grants must be disjoint from each other and the primary project. Optional `primary_mode: "read_only"` restricts ordinary primary-project mutations; default is `read_write`. Exports are bound to the exact canonical primary project. Unknown fields, malformed policy, unsafe paths, overlapping grants, or missing policy fail closed. Maximum: 64 grants / 64 KiB export.

The host should offer only choices its permission system actually supports:

- **Deny:** do not export a grant. The operation remains blocked.
- **Allow once:** export `lifetime: "once"` with a fresh approval ID and the current host session ID. It covers one FFSAKE request that uses the grant, not a preview/apply sequence. Claiming it is durable under the primary project's protected `.ffsake/access-consumed`; a failed attempt may consume it. Reusing its ID after restart does not restore access. A host must never silently turn it into a session grant.
- **Allow for this session:** export `lifetime: "session"` bound to `FFSAKE_HOST_SESSION_ID`. The host generates a new identity for a new session and reuses it only for a genuine resume. FFSAKE does not infer Claude's session ID.
- **Always allow for this project:** the host persists an explicit `lifetime: "project"` entry in that project's export. FFSAKE never writes it on the user's behalf. This setting has no session ID requirement.

In explicit-export mode, the host/operator performs the approval in its available UI, writes/replaces the exact export, then asks the agent to retry. The running server reloads the export before each request. **Automatic native permission prompting, receipt of approval lifetime, persistence into Claude settings, and automatic retry are not implemented/supported by this integration.** Do not claim that the four options above are a new Claude permission dialog. A future authoritative host bridge may export actual decisions; it must not infer them from ordinary tool approvals.

Use atomic file replacement for updates. Remove or downgrade an entry to revoke rights. A re-added grant permits a new request only if its lifetime still allows it and stale-content checks still pass. Invalid exports block requests rather than retain old rights. Updates are observed at request boundaries; an operation already executing uses its captured authorization state. There is no atomic filesystem revocation during an in-flight transaction.

## Identity, journal and execution boundaries

Primary paths retain their relative identities; external paths retain absolute identities. Snapshots passed to Rust/clangd use safe relative names inside a disposable directory and map results back to authorized identities. A single semantic context or mutation transaction must stay within one grant root. Cross-root copy/move/batches are rejected before mutation; split them into separately reviewed operations if appropriate. Reading separately approved files with `read_files` is allowed, with all paths checked before reads begin.

The primary project owns all transaction metadata and snapshots, including external edits. No journal is written into the external directory. Reopening the same primary project with valid host authorization can undo/redo its external history. Apply, undo, redo, source-copy checks, and unchanged semantic context are reauthorized; revoked or read-only rights block mutation even if a preview or transaction was previously approved. History cannot be used to retrieve revoked source contents. Existing hashes, mode checks, snapshot integrity, rollback and protected `.ffsake` guards remain in force.

External paths reject symlink components and `..` traversal. Existing safe primary read aliases retain their prior behavior; mutation, batch reads and external reads remain strict. The filesystem guards are application checks, not a kernel sandbox against hostile concurrent replacement or native tools. Filesystem rename/device limitations still apply to tree operations; a failure is reported without claiming a completed transaction.

Git review remains at the primary repository, and `--tasks` remains a separate fixed startup execution policy. File grants do not create shell/build permissions or expand task cwd handling. Host-authorized jobs can execute project code and mutate files independently; do not interpret a read-only file grant as an OS restriction on those jobs.

## Agent guidance and verification

On denial, report the exact path, operation, required mode and requested lifetime to the host. Do not edit the export, request a broad parent grant for one file, switch to native tools to evade denial, or repeatedly retry without a scope change. After a genuine host update, retry the original request and still review a fresh preview when content changed.

Run `python3 scripts/access_smoke.py target/debug/ffsake-mcp`. It covers exact file/directory scope, read-only denial, neighbors, traversal/symlinks, external archive and real Rust/clangd analysis, compiled external rename, stale preview and history revocation, project persistence, host session isolation, durable once consumption, and live MCP root notifications. This suite tests exports; run `python3 scripts/persistent_access_smoke.py target/debug/ffsake-mcp` for persistent MCP integration. The latter exercises MCP dispatch and is not proof of a native prompt; see HOST_APPROVAL_DEMO.md for installed-host observations.
