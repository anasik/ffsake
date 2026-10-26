# Host-authorized filesystem access

The host session is the permission authority. FFSAKE cannot infer the host's complete filesystem policy from a tool approval, a successful native read, MCP roots, or a model's statement that access was approved. There is no grant-access tool.

Without additional configuration, the primary project remains the default read/write scope. Absolute paths inside that project work. Relative paths, `All`, glob/regex discovery and the watched index remain relative to the primary project. External search requires explicit `Exact` file/directory targets; it never indexes an individually granted file's parent.

## Native approval investigation

Claude now documents an always-prompt marker for dedicated MCP consent tools. A real 2.1.310 terminal probe passed approval/denial checks even with auto mode, allow rules and allow hooks. This does not by itself authenticate the calling host or distinguish SDK approval. Persistent open-file/add-folder grants are not enabled; see [the verification report](NATIVE_APPROVAL_PROBE.md) for evidence and the unresolved supported-host boundary. The explicit-export workflow below remains the implemented path.

## What Claude Code communicates

Claude Code documents `roots/list` as its launch directory plus additional working directories from `--add-dir`, `/add-dir`, and settings, with `notifications/roots/list_changed` when that set changes. FFSAKE negotiates MCP 2025-11-25, requests roots after initialization when an access export is configured, and refreshes them on notifications. Root-dependent rights are suspended immediately while refresh is pending; malformed/error responses leave those rights disabled. Older replies cannot restore superseded roots.

A root contains a URI and optional name, **not** a read/write mode, native Read/Edit deny rules, one-time file approvals, approval lifetime, or the complete host policy. Stock Claude's MCP API does not expose an authoritative filesystem permission query or a native filesystem approval-and-retry API to this server. `PermissionRequest` hooks decide tool calls; elicitation collects user input. Neither is treated as a filesystem grant. We do not parse private settings/transcripts to guess approvals or install a hook that automatically approves them.

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

For stock Claude, the host/operator performs the approval in its available UI, writes/replaces the exact export, then asks the agent to retry. The running server reloads the export before each request. **Automatic native permission prompting, receipt of approval lifetime, persistence into Claude settings, and automatic retry are not implemented/supported by this integration.** Do not claim that the four options above are a new Claude permission dialog. A future authoritative host bridge may export actual decisions; it must not infer them from ordinary tool approvals.

Use atomic file replacement for updates. Remove or downgrade an entry to revoke rights. A re-added grant permits a new request only if its lifetime still allows it and stale-content checks still pass. Invalid exports block requests rather than retain old rights. Updates are observed at request boundaries; an operation already executing uses its captured authorization state. There is no atomic filesystem revocation during an in-flight transaction.

## Identity, journal and execution boundaries

Primary paths retain their relative identities; external paths retain absolute identities. Snapshots passed to Rust/clangd use safe relative names inside a disposable directory and map results back to authorized identities. A single semantic context or mutation transaction must stay within one grant root. Cross-root copy/move/batches are rejected before mutation; split them into separately reviewed operations if appropriate. Reading separately approved files with `read_files` is allowed, with all paths checked before reads begin.

The primary project owns all transaction metadata and snapshots, including external edits. No journal is written into the external directory. Reopening the same primary project with valid host authorization can undo/redo its external history. Apply, undo, redo, source-copy checks, and unchanged semantic context are reauthorized; revoked or read-only rights block mutation even if a preview or transaction was previously approved. History cannot be used to retrieve revoked source contents. Existing hashes, mode checks, snapshot integrity, rollback and protected `.ffsake` guards remain in force.

External paths reject symlink components and `..` traversal. Existing safe primary read aliases retain their prior behavior; mutation, batch reads and external reads remain strict. The filesystem guards are application checks, not a kernel sandbox against hostile concurrent replacement or native tools. Filesystem rename/device limitations still apply to tree operations; a failure is reported without claiming a completed transaction.

Git review remains at the primary repository, and `--tasks` remains a separate fixed startup execution policy. File grants do not create shell/build permissions or expand task cwd handling. Host-authorized jobs can execute project code and mutate files independently; do not interpret a read-only file grant as an OS restriction on those jobs.

## Agent guidance and verification

On denial, report the exact path, operation, required mode and requested lifetime to the host. Do not edit the export, request a broad parent grant for one file, switch to native tools to evade denial, or repeatedly retry without a scope change. After a genuine host update, retry the original request and still review a fresh preview when content changed.

Run `python3 scripts/access_smoke.py target/debug/ffsake-mcp`. It covers exact file/directory scope, read-only denial, neighbors, traversal/symlinks, external archive and real Rust/clangd analysis, compiled external rename, stale preview and history revocation, project persistence, host session isolation, durable once consumption, and live MCP root notifications. Native host approval-and-retry is explicitly reported as unsupported rather than simulated.
