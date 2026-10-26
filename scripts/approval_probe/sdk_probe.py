"""Harmless negative control: SDK approval without a human prompt."""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow

out = Path(os.environ['FFSAKE_PROBE_OUTPUT']).resolve()
out.mkdir(parents=True, exist_ok=True)

async def allow(name, arguments, context):
    # Test fixture only. This deliberately does NOT represent human approval.
    with (out / 'sdk-callbacks.jsonl').open('a') as stream:
        stream.write(json.dumps({'tool': name, 'arguments': arguments, 'human_prompt': False}) + '\n')
    return PermissionResultAllow(updated_input=arguments)

async def main():
    with tempfile.TemporaryDirectory(prefix='ffsake-sdk-approval-') as root:
        options = ClaudeAgentOptions(
            cli_path=shutil.which('claude'), cwd=root, tools=[], setting_sources=[],
            strict_mcp_config=True, permission_mode='auto', max_turns=3, max_budget_usd=1,
            mcp_servers={'approvalprobe': {'command': sys.executable,
                'args': [str(Path(__file__).with_name('probe_server.py'))],
                'env': {'FFSAKE_PROBE_OUTPUT': str(out)}}},
            can_use_tool=allow,
        )
        async with ClaudeSDKClient(options=options) as client:
            await client.query('Call ffsake_access_probe exactly once with path /tmp/synthetic-approved.txt, kind file, mode read_only. Report the actual result. Do not retry.')
            async for message in client.receive_response():
                print(json.dumps(asdict(message), default=str), flush=True)

asyncio.run(main())
