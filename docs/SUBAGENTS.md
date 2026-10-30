# Forks, subagents and worktrees

An FFSAKE MCP process is bound to its primary project when it starts. Sharing that connection with a child does not change its root when the child changes directory or enters another worktree. MCP requests do not identify the caller's current directory, so the server cannot enforce that the child is using its intended checkout. Before editing or running tasks, a child should call `ffsake_workspace_info` and compare `root` with its host-provided working directory. If they differ, stop and connect a server started for the correct worktree. This is guidance, not a security boundary against a client that ignores it.

For a copied project-local configuration in a linked worktree, the installer can review and change only the FFSAKE root argument. For example, with the source checkout and installation already available:

```sh
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" configure --host codex --project /absolute/child-worktree --rebind-from /absolute/parent-worktree --guidance
python3 "$FFSAKE_SOURCE/scripts/ffsake_install.py" configure --host codex --project /absolute/child-worktree --rebind-from /absolute/parent-worktree --guidance --apply
```

Choose `claude`, `codex`, or `opencode` as appropriate. Review the preview before applying. The two paths must be roots of distinct worktrees in the same Git repository, and the child must already contain a compatible project-local FFSAKE entry. A conflicting user-level entry, disabled server, unexpected command or extra OpenCode configuration requires manual review. The operation preserves other arguments and permission settings; it does not add access grants or approve tools. In particular, an explicit `--access` export or `--tasks` policy remains unchanged and must be checked by its host owner for the new root. A root-bound access export may reject the new server. Start a new child-local host session or reconnect the relevant server; changing a configuration file cannot retarget a running process. Confirm the root again after connecting.

Host behavior and limitations:

- [Claude Code](https://code.claude.com/docs/en/sub-agents) supports sharing a parent's MCP connection by server name or using an inline subagent server configuration. A shared connection remains parent-rooted even if Claude isolates a child in a worktree; use a separately configured child-rooted server and verify it. Parent permissions and host policy still apply. Local interactive child testing was unavailable because Claude was not authenticated.
- [Codex](https://developers.openai.com/codex/subagents/) documents inherited sandbox and approval settings for subagents, subject to configuration and host policy. A child in the same checkout can use an available parent connection; a different worktree requires a separately rooted connection. A real child used FFSAKE to verify its root, read and edit a linked worktree, and run a test, with fixture-specific approvals set on its parent. A separate denial was respected. The parent agent was started in the child worktree; automatic retargeting from a parent agent in another worktree was not demonstrated. See the [evidence](demo-runs/20261030-subagent/REPORT.md).
- [OpenCode](https://opencode.ai/docs/agents/) has agent-specific permissions and rules that can override defaults. Do not assume a child receives the parent's permissions or a worktree-specific MCP connection. Configure and verify the child session explicitly. Real child inheritance has not been validated here.

None of these instructions widens approval exceptions, synchronizes host filesystem permissions, or grants access to neighboring paths. Native fallback must never be used to evade access denial. A host may restrict MCP tools, omit project instructions, or require trust/approval; consult its configuration and policy when this occurs.
