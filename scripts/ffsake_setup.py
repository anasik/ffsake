#!/usr/bin/env python3
"""Generate reviewable host setup; never overwrite existing host configuration."""
import argparse
import json
from pathlib import Path

from agent_benchmark_suite import FFSAKE_MCP, FFSAKE_ACCESS_MUTATIONS, FFSAKE_APPROVAL_TOOLS, profile_tools, mcp_arguments


def configurations(host, root, binary, profile='all', access_approval='ask', debug_policy=None, debug_approval='ask', task_approval='ask'):
    tools = profile_tools(profile)
    args = mcp_arguments(root, profile)
    if debug_policy:
        args += ['--debug', str(Path(debug_policy).expanduser().resolve())]
    def approval(name):
        if name in ('ffsake_task_register', 'ffsake_run_task'):
            return task_approval
        if name == 'ffsake_git_mutate':
            return 'ask'
        if name == 'ffsake_debug_launch':
            return debug_approval
        return access_approval if name in FFSAKE_ACCESS_MUTATIONS else 'allow'
    if host == 'claude':
        # Default MCP permission handling asks in normal mode. Do not inject ask
        # rules: those outrank deliberate allow rules and can defeat auto mode.
        allowed = ['mcp__ffsake__' + name for name in tools
                   if approval(name) == 'allow']
        return {'.mcp.json': json.dumps({'mcpServers': {'ffsake': {
                    'command': str(binary), 'args': args}}}, indent=2) + '\n',
                '.claude/settings.json': json.dumps({'permissions': {'allow': allowed}}, indent=2) + '\n'}
    if host == 'codex':
        text = '[mcp_servers.ffsake]\ncommand = ' + json.dumps(str(binary))
        text += '\nargs = ' + json.dumps(args) + '\n'
        for name in tools:
            mode = 'prompt' if approval(name) == 'ask' else 'approve'
            text += f'\n[mcp_servers.ffsake.tools.{name}]\napproval_mode = "{mode}"\n'
        return {'.codex/config.toml': text}
    if host != 'opencode':
        raise ValueError('unsupported host: ' + host)
    return {'opencode.json': json.dumps({
        '$schema': 'https://opencode.ai/config.json',
        'mcp': {'ffsake': {'type': 'local', 'command': [str(binary), *args], 'enabled': True}},
        'permission': {'ffsake_*': 'allow', **{'ffsake_' + name: approval(name)
                       for name in FFSAKE_APPROVAL_TOOLS}},
    }, indent=2) + '\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host', choices=('claude', 'codex', 'opencode'))
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--binary', type=Path, default=FFSAKE_MCP)
    parser.add_argument('--tool-profile', choices=('all', 'core'), default='all')
    parser.add_argument('--access-approval', choices=('ask', 'allow'), default='ask',
                        help='Normal host approval defaults, or deliberate automatic allowance')
    parser.add_argument('--debug', type=Path, help='Host-controlled adapter/launch configuration')
    parser.add_argument('--debug-approval', choices=('ask', 'allow'), default='ask')
    parser.add_argument('--task-approval', choices=('ask', 'allow'), default='ask')
    parser.add_argument('--output-dir', type=Path, help='Write new setup files here; refuses existing files')
    args = parser.parse_args()
    root = args.workspace.expanduser().resolve()
    if not root.is_dir():
        parser.error('workspace must be an existing directory')
    configs = configurations(args.host, root, args.binary.expanduser().resolve(),
                             args.tool_profile, args.access_approval, args.debug, args.debug_approval, args.task_approval)
    if args.output_dir:
        targets = [(args.output_dir / name, content) for name, content in configs.items()]
        if any(path.exists() or path.is_symlink() for path, _ in targets):
            parser.error('configuration exists; print the snippets and merge with your existing policy instead')
        for path, content in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x') as stream:
                stream.write(content)
            print(path.resolve())
    else:
        for name, content in configs.items():
            print(f'# {name}\n{content}')


if __name__ == '__main__':
    main()
