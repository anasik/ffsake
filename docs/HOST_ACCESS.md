# Host-authorized filesystem access

The host session is the permission authority. FFSAKE supports two explicitly selected authorization modes: persistent grants approved by a trusted Claude host, or explicit host exports. Without either, only the primary project is accessible. Absolute paths inside authorized scopes work; default indexed discovery remains primary-only.

See [real-Claude acceptance and reproduction](PERSISTENT_ACCESS_DEMO.md).

## Persistent open-file / add-folder workflow (trusted Claude)

Enable once in the MCP server's startup configuration:

```sh
ffsake-mcp /absolute/project --trusted-host claude-code
# Or use the interactive launcher:
python3 scripts/ffsake_claude.py /absolute/project --trusted-host
```

Use Claude Code 2.1.310 (the tested version), or a version whose documented native approval enforcement supports the marker. Reconnect the MCP server after updating the binary and its startup arguments; an existing chat can keep its conversation. No `/add-dir`, authorization JSON edits or per-file shell helpers are needed.

Ask the agent to open the external file or add a folder. On a denied read, it calls `ffsake_open_access` with an **absolute** `path`, `kind: "file"` or `"directory"`, and `mode: "read_only"` or `"read_write"`. Claude shows its native Yes/No prompt. Yes persists exactly that scope for this primary project; the agent retries the original operation. No dispatches nothing and creates no grant. A file grant never includes its parent or neighbors. An existing file grant can be expanded to read/write only through another approved call. Calling open-access with read-only also allows an approved downgrade.

`ffsake_list_access` lists the grants. Ask the agent to revoke a path with `ffsake_remove_access`, then approve the native prompt. Removal changes authorization only: it neither deletes files nor reverses edits. No ordinary editing tool can change the store. Both open and remove advertise Boolean `_meta["anthropic/requiresUserInteraction"]: true`; ordinary tools keep their normal approval behavior. Every open/remove call prompts, including idempotent requests. These three tools appear in either profile only when the host enables this mode; the JSONL interface cannot create persistent grants.

### Explicit trust boundary

The operator **trusts Claude to enforce approval** when enabling this flag. This is a host-controlled declaration, not authentication from clientInfo and not a server-verifiable approval receipt. The flag must not be enabled for clients that ignore the marker, old Claude versions, or SDK applications that automatically approve callbacks. The official SDK can dispatch marked tools without a human; see the [real probe and negative control](NATIVE_APPROVAL_PROBE.md). Ordinary MCP invocation is not proof of approval outside this explicitly trusted mode. Agents cannot enable the mode through any FFSAKE tool or an `approved` argument.

This mode grants FFSAKE access independently; it **does not synchronize Claude's native Read/Edit permissions, deny rules or `/add-dir`**. The host/operator must not enable it where it would contradict an enforced host policy. `--trusted-host` and `--access` are mutually exclusive and startup rejects their combination. Existing exports are never silently merged or overridden. Other clients may continue using default scope or exports; persistent grants are not loaded unless trusted mode is enabled.

### Persistence and revocation

The canonical primary project owns `.ffsake/native-access/grants.json` and its OS lock. The journal remains under the same primary project. Grants survive server/session restart and are durable on disk (file and parent-directory sync after atomic replacement); no session token expires them. Instances using the same canonical primary project share this store, while a different primary project has a separate store. Copying a store to another project is rejected. This is not portable identity across moving/renaming the primary directory.

The protected `.ffsake` path is unavailable through FFSAKE reads, edits, searches, archives and history. Writes use a private temporary file, validation before replacement, and an OS lock to avoid lost concurrent updates. A crash releases the lock. Existing filesystem/symlink safeguards still apply; native OS access remains a separate host capability, not sandboxed by this store.

Running instances reload before every operation. Revocation prevents subsequent reads, previews, apply, undo and redo, including cached handles. It cannot erase contents already returned to a conversation or interrupt an operation already executing. Grants remain disjoint, including from the primary root; overlapping grants and cross-root transactions remain unsupported and reject before mutation. Revoke a narrower grant before deliberately approving a broader folder. Search added folders with explicit Exact targets; the primary index and journal anchor do not move.

For noninteractive execution, the tested CLI denies marked calls. This workflow is persistent project access, not an allow-once/session chooser. Use explicit exports below for host-managed lifetimes.

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

Run `python3 scripts/access_smoke.py target/debug/ffsake-mcp`. It covers exact file/directory scope, read-only denial, neighbors, traversal/symlinks, external archive and real Rust/clangd analysis, compiled external rename, stale preview and history revocation, project persistence, host session isolation, durable once consumption, and live MCP root notifications. This suite tests exports; run `python3 scripts/persistent_access_smoke.py target/debug/ffsake-mcp` for persistent MCP integration. The latter simulates trusted dispatch and is not proof of a native prompt; the separate real-Claude acceptance report supplies that evidence.
