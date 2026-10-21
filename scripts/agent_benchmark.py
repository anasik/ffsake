#!/usr/bin/env python3
"""Compare a normal coding-agent run with the same agent constrained to FFSAKE MCP.

The benchmark uses disposable fixture repositories. It never points an agent at the
ffsake repository or another user repository.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FFSAKE_MCP = (ROOT / "target" / "debug" / "ffsake-mcp").resolve()
RESULT_DIR = ROOT / "docs" / "agent-benchmark-runs"

TASK = """Rename the identifier LegacyConfig to RuntimeConfig everywhere in this repository.
Do not change any other text. When the rename is complete, stop.
"""

FFSAKE_TASK = """Rename the identifier LegacyConfig to RuntimeConfig everywhere in this repository.
Use the FFSAKE MCP tools for repository reading, search, preview, mutation, and verification.
Do not use shell commands or built-in filesystem search/edit tools for the repository task.
Preview the replacement before applying it. When the rename is complete, stop.
"""


def fixture(root: Path) -> dict[str, bytes]:
    files = {
        "src/lib.rs": b"pub struct LegacyConfig {\n    pub retries: u32,\n}\n\nimpl LegacyConfig {\n    pub fn new() -> Self { Self { retries: 3 } }\n}\n",
        "src/runtime.rs": b"use crate::LegacyConfig;\n\npub fn boot(config: LegacyConfig) -> u32 {\n    config.retries\n}\n",
        "src/parser.rs": b"use crate::LegacyConfig;\n\npub fn parse() -> LegacyConfig {\n    LegacyConfig::new()\n}\n",
        "tests/config.rs": b"use demo::LegacyConfig;\n\n#[test]\nfn defaults() {\n    let config = LegacyConfig::new();\n    assert_eq!(config.retries, 3);\n}\n",
        "README.md": b"# demo\n\nThe public configuration type is LegacyConfig.\n",
        "notes.txt": b"migration target: LegacyConfig\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "FFSAKE Benchmark"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ffsake-benchmark@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "benchmark fixture"],
        cwd=root,
        check=True,
    )
    return files


def expected(original: dict[str, bytes]) -> dict[str, bytes]:
    return {
        name: content.replace(b"LegacyConfig", b"RuntimeConfig")
        for name, content in original.items()
    }


def snapshot(root: Path, names) -> dict[str, bytes]:
    return {name: (root / name).read_bytes() for name in names}


def run(cmd, cwd: Path, timeout=600):
    started = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "command": cmd,
        "exit_code": proc.returncode,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def parse_jsonl(text):
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def flatten_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from flatten_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from flatten_strings(item)


def trace_metrics(events):
    tool_calls = []
    result_event = None

    for event in events:
        if event.get("type") == "result":
            result_event = event
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "name": block.get("name", ""),
                        "input": block.get("input"),
                    }
                )

    names = [call["name"] for call in tool_calls]
    bash_calls = [call for call in tool_calls if call["name"] == "Bash"]
    ffsake_calls = [
        call
        for call in tool_calls
        if "ffsake" in call["name"].lower()
        and call["name"] != "ToolSearch"
    ]

    commands = [
        str(call.get("input", {}).get("command", ""))
        for call in bash_calls
        if isinstance(call.get("input"), dict)
    ]
    command_blob = "\n".join(commands).lower()

    usage = (result_event or {}).get("usage", {})
    model_usage = (result_event or {}).get("modelUsage", {})

    return {
        "events": len(events),
        "tool_calls": len(tool_calls),
        "tool_names": names,
        "ffsake_tool_calls": len(ffsake_calls),
        "bash_tool_calls": len(bash_calls),
        "grep_commands": sum("grep" in command for command in commands),
        "sed_commands": sum("sed" in command for command in commands),
        "cat_commands": sum("cat " in command for command in commands),
        "num_turns": (result_event or {}).get("num_turns"),
        "total_cost_usd": (result_event or {}).get("total_cost_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "model_usage": model_usage,
        "shell_command_text": command_blob,
    }


def claude_run(workdir: Path, use_ffsake: bool):
    cmd = [
        "claude",
        "-p",
        FFSAKE_TASK if use_ffsake else TASK,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        "20",
        "--dangerously-skip-permissions",
    ]
    if use_ffsake:
        config = workdir / ".ffsake-benchmark-mcp.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "ffsake": {
                            "command": str(FFSAKE_MCP),
                            "args": [str(workdir)],
                        }
                    }
                }
            )
        )
        cmd += [
            "--mcp-config",
            str(config),
            "--disallowedTools",
            "Bash,Read,Write,Edit,Glob,Grep",
        ]
    result = run(cmd, workdir)
    result["events"] = parse_jsonl(result["stdout"])
    return result


def codex_config(workdir: Path):
    config_dir = workdir / ".codex"
    config_dir.mkdir(exist_ok=True)
    command = str(FFSAKE_MCP).replace("\\", "\\\\").replace('"', '\\"')
    workspace = str(workdir).replace("\\", "\\\\").replace('"', '\\"')
    (config_dir / "config.toml").write_text(
        "[mcp_servers.ffsake]\n"
        f'command = "{command}"\n'
        f'args = ["{workspace}"]\n'
    )


def codex_run(workdir: Path, use_ffsake: bool):
    if use_ffsake:
        codex_config(workdir)
    cmd = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        FFSAKE_TASK if use_ffsake else TASK,
    ]
    result = run(cmd, workdir)
    result["events"] = parse_jsonl(result["stdout"])
    return result


def agent_available(agent):
    return shutil.which(agent) is not None


def clean_control_files(root: Path):
    for path in [root / ".ffsake-benchmark-mcp.json", root / ".codex"]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def one(agent: str, use_ffsake: bool):
    with tempfile.TemporaryDirectory(prefix=f"ffsake-agent-{agent}-") as temp:
        root = Path(temp)
        original = fixture(root)
        want = expected(original)

        if agent == "claude":
            result = claude_run(root, use_ffsake)
        elif agent == "codex":
            result = codex_run(root, use_ffsake)
        else:
            raise ValueError(agent)

        clean_control_files(root)
        actual = snapshot(root, original.keys())
        correct = actual == want
        unchanged_count = sum(actual[name] == original[name] for name in original)
        metrics = trace_metrics(result["events"])

        return {
            "agent": agent,
            "mode": "ffsake" if use_ffsake else "baseline",
            "exit_code": result["exit_code"],
            "elapsed_ms": result["elapsed_ms"],
            "correct": correct,
            "unchanged_files": unchanged_count,
            "trace": metrics,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }


def summarize(runs):
    rows = []
    for item in runs:
        rows.append(
            {
                "agent": item["agent"],
                "mode": item["mode"],
                "correct": item["correct"],
                "exit_code": item["exit_code"],
                "elapsed_ms": item["elapsed_ms"],
                "events": item["trace"]["events"],
                "tool_calls": item["trace"]["tool_calls"],
                "ffsake_tool_calls": item["trace"]["ffsake_tool_calls"],
                "bash_tool_calls": item["trace"]["bash_tool_calls"],
                "grep_commands": item["trace"]["grep_commands"],
                "sed_commands": item["trace"]["sed_commands"],
                "num_turns": item["trace"]["num_turns"],
                "total_cost_usd": item["trace"]["total_cost_usd"],
                "input_tokens": item["trace"]["input_tokens"],
                "output_tokens": item["trace"]["output_tokens"],
                "cache_read_input_tokens": item["trace"]["cache_read_input_tokens"],
                "cache_creation_input_tokens": item["trace"]["cache_creation_input_tokens"],
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("agent", choices=["claude", "codex", "all"])
    args = parser.parse_args()

    agents = ["claude", "codex"] if args.agent == "all" else [args.agent]
    missing = [a for a in agents if not agent_available(a)]
    if missing:
        raise SystemExit(
            "Missing CLI(s): "
            + ", ".join(missing)
            + ". Install/sign in first, then rerun this command."
        )
    if not FFSAKE_MCP.exists():
        raise SystemExit(
            f"Missing {FFSAKE_MCP}. Run: cargo build --locked --bin ffsake-mcp"
        )

    runs = []
    for agent in agents:
        print(f"[{agent}] baseline", flush=True)
        runs.append(one(agent, False))
        print(f"[{agent}] FFSAKE", flush=True)
        runs.append(one(agent, True))

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = RESULT_DIR / f"{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "task": TASK.strip(),
                "results": runs,
                "summary": summarize(runs),
            },
            indent=2,
        )
    )

    print("\nRESULT")
    for row in summarize(runs):
        print(json.dumps(row, sort_keys=True))
    print(f"\nFull trace: {out}")


if __name__ == "__main__":
    main()
