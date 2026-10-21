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
    blob = "\n".join(flatten_strings(events))
    lower = blob.lower()
    return {
        "events": len(events),
        "ffsake_tool_mentions": lower.count("ffsake_"),
        "shell_mentions": sum(
            lower.count(token)
            for token in ["shell", "bash", "command_execution", "exec_command", "terminal"]
        ),
        "grep_mentions": lower.count("grep"),
        "sed_mentions": lower.count("sed"),
        "cat_mentions": lower.count("cat "),
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
                "ffsake_tool_mentions": item["trace"]["ffsake_tool_mentions"],
                "shell_mentions": item["trace"]["shell_mentions"],
                "grep_mentions": item["trace"]["grep_mentions"],
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
