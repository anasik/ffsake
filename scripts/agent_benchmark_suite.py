#!/usr/bin/env python3
"""Multi-agent benchmark matrix for FFSAKE.

Runs Claude Code, Codex, and OpenCode in baseline and FFSAKE modes across
micro, medium, and app-completion tasks. Every run uses a disposable git repo.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FFSAKE_MCP = (ROOT / "target" / "debug" / "ffsake-mcp").resolve()
RESULT_DIR = ROOT / "docs" / "agent-benchmark-runs"


def git_init(root: Path):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "FFSAKE Benchmark"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ffsake-benchmark@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "benchmark fixture"], cwd=root, check=True)


def write_files(root: Path, files: dict[str, str]):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def micro_fixture(root: Path):
    files = {
        "src/lib.rs": "pub struct LegacyConfig {\n    pub retries: u32,\n}\n\nimpl LegacyConfig {\n    pub fn new() -> Self { Self { retries: 3 } }\n}\n",
        "src/runtime.rs": "use crate::LegacyConfig;\n\npub fn boot(config: LegacyConfig) -> u32 {\n    config.retries\n}\n",
        "src/parser.rs": "use crate::LegacyConfig;\n\npub fn parse() -> LegacyConfig {\n    LegacyConfig::new()\n}\n",
        "tests/config.rs": "use demo::LegacyConfig;\n\n#[test]\nfn defaults() {\n    let config = LegacyConfig::new();\n    assert_eq!(config.retries, 3);\n}\n",
        "README.md": "# demo\n\nThe public configuration type is LegacyConfig.\n",
        "notes.txt": "migration target: LegacyConfig\n",
    }
    write_files(root, files)
    git_init(root)
    return {"original": files}


def micro_verify(root: Path, state):
    want = {k: v.replace("LegacyConfig", "RuntimeConfig") for k, v in state["original"].items()}
    actual = {k: (root / k).read_text() for k in want}
    return actual == want, {"expected_files": len(want)}


def medium_fixture(root: Path):
    files = {
        "policy.py": '''"""Small request-policy engine."""\n\n\ndef normalize_method(method):\n    raise NotImplementedError("TODO_NORMALIZE_METHOD")\n\n\ndef allowed(method, path, rules):\n    raise NotImplementedError("TODO_ALLOWED")\n\n\ndef explain(method, path, rules):\n    raise NotImplementedError("TODO_EXPLAIN")\n''',
        "README.md": """# request-policy\n\nComplete the existing policy engine. Do not add or remove files.\n""",
    }
    write_files(root, files)
    git_init(root)
    return {}


def medium_verify(root: Path, state):
    try:
        spec = importlib.util.spec_from_file_location("policy_bench", root / "policy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rules = [
            {"method": "GET", "prefix": "/public", "allow": True},
            {"method": "*", "prefix": "/admin", "allow": False},
            {"method": "POST", "prefix": "/admin/invite", "allow": True},
        ]
        checks = [
            module.normalize_method(" get ") == "GET",
            module.normalize_method("Post") == "POST",
            module.allowed("GET", "/public/a", rules) is True,
            module.allowed("DELETE", "/admin/users", rules) is False,
            module.allowed("POST", "/admin/invite/7", rules) is True,
            module.allowed("PATCH", "/unknown", rules) is False,
            module.explain("POST", "/admin/invite/7", rules)
            == "POST /admin/invite/7 => allow by POST /admin/invite",
            module.explain("PATCH", "/unknown", rules)
            == "PATCH /unknown => deny by default",
        ]
        ok = all(checks)
        return ok, {"checks": len(checks), "passed": sum(bool(x) for x in checks)}
    except Exception as exc:
        return False, {"exception": repr(exc)}


def app_fixture(root: Path):
    files = {
        "issue_tracker.py": '''"""A tiny JSON-backed issue tracker."""\n\nimport json\nfrom pathlib import Path\n\n\nclass IssueTracker:\n    def __init__(self, path):\n        self.path = Path(path)\n\n    def _load(self):\n        raise NotImplementedError("TODO_LOAD")\n\n    def _save(self, issues):\n        raise NotImplementedError("TODO_SAVE")\n\n    def create(self, title, tags=None):\n        raise NotImplementedError("TODO_CREATE")\n\n    def list(self, status=None, tag=None):\n        raise NotImplementedError("TODO_LIST")\n\n    def close(self, issue_id):\n        raise NotImplementedError("TODO_CLOSE")\n\n    def stats(self):\n        raise NotImplementedError("TODO_STATS")\n''',
        "README.md": """# issue-tracker\n\nImplement the existing JSON-backed issue tracker skeleton. Do not add or remove files.\n""",
    }
    write_files(root, files)
    git_init(root)
    return {}


def app_verify(root: Path, state):
    try:
        with tempfile.TemporaryDirectory(prefix="ffsake-app-verify-") as td:
            db = Path(td) / "issues.json"
            spec = importlib.util.spec_from_file_location("issue_tracker_bench", root / "issue_tracker.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            tracker = module.IssueTracker(db)

            a = tracker.create("first", ["bug", "api"])
            b = tracker.create("second", ["feature"])
            c = tracker.create("third", ["bug"])
            checks = [
                a["id"] == 1 and b["id"] == 2 and c["id"] == 3,
                a["status"] == "open",
                [x["id"] for x in tracker.list()] == [1, 2, 3],
                [x["id"] for x in tracker.list(tag="bug")] == [1, 3],
                tracker.close(2)["status"] == "closed",
                [x["id"] for x in tracker.list(status="open")] == [1, 3],
                tracker.stats() == {"open": 2, "closed": 1, "total": 3},
                module.IssueTracker(db).stats() == {"open": 2, "closed": 1, "total": 3},
            ]
            try:
                tracker.close(999)
                checks.append(False)
            except (KeyError, ValueError):
                checks.append(True)
            ok = all(checks)
            return ok, {"checks": len(checks), "passed": sum(bool(x) for x in checks)}
    except Exception as exc:
        return False, {"exception": repr(exc)}


TASKS = {
    "micro": {
        "fixture": micro_fixture,
        "verify": micro_verify,
        "prompt": "Rename the identifier LegacyConfig to RuntimeConfig everywhere in this repository. Do not change any other text. When complete, stop.",
    },
    "medium": {
        "fixture": medium_fixture,
        "verify": medium_verify,
        "prompt": """Complete policy.py so it behaves as follows:
- normalize_method strips whitespace and uppercases.
- rules are checked in list order; each rule has method, prefix, allow.
- method '*' matches any method.
- a rule matches when the normalized method matches and path starts with prefix.
- allowed returns the matching rule's allow value, or False by default.
- explain returns exactly '<METHOD> <path> => allow|deny by <rule-method> <prefix>' for a matched rule, or '<METHOD> <path> => deny by default'.
Do not add/remove files. Do not change README.md. Stop when implementation is complete.""",
    },
    "app": {
        "fixture": app_fixture,
        "verify": app_verify,
        "prompt": """Complete the existing issue_tracker.py application.
Requirements:
- JSON persistence at the path passed to IssueTracker.
- missing database means an empty list.
- create(title, tags=None) appends an open issue with monotonically increasing integer id; tags defaults to [] and returns the created issue.
- list(status=None, tag=None) returns issues in id order, filtering by status and/or tag when supplied.
- close(issue_id) marks that issue closed and returns it; raise KeyError or ValueError if absent.
- stats() returns {'open': N, 'closed': N, 'total': N}.
- writes must leave valid JSON.
Do not add/remove files. Do not change README.md. Stop when implementation is complete.""",
    },
}


def ffsake_prompt(task_prompt: str):
    return (
        task_prompt
        + "\n\nUse FFSAKE MCP for repository reading, search, preview, mutation, and verification. "
        "Do not use normal shell/filesystem read/edit/search tools for repository manipulation. "
        "Preview mutations before applying them. If FFSAKE lacks a capability required to finish, "
        "state the missing capability rather than falling back silently."
    )


def run_process(cmd, cwd: Path, timeout: int):
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "exit_code": proc.returncode,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "stdout": decode_output(exc.stdout),
            "stderr": decode_output(exc.stderr),
            "timed_out": True,
        }


def decode_output(value):
    """TimeoutExpired may return bytes even when text=True."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def save_report(path: Path, report: dict):
    """Atomically persist the completed runs before starting another agent."""
    payload = json.dumps(report, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=path.name + ".", suffix=".tmp", delete=False,
    ) as stream:
        tmp_path = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def reserve_report_path():
    """Reserve a unique report filename even for concurrent suite processes."""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f"suite-{time.strftime('%Y%m%d-%H%M%S')}-",
        suffix=".json",
        dir=RESULT_DIR,
    )
    os.close(fd)
    return Path(name)


def parse_jsonl(text):
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def recursive_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for x in value.values():
            yield from recursive_strings(x)
    elif isinstance(value, list):
        for x in value:
            yield from recursive_strings(x)


def generic_metrics(agent, events):
    names = []
    ffsake = 0
    shell = 0
    for event in events:
        if agent == "claude":
            message = event.get("message") if isinstance(event, dict) else None
            if isinstance(message, dict) and message.get("role") == "assistant":
                for block in message.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = str(block.get("name", ""))
                        names.append(name)
        else:
            # Codex/OpenCode JSON formats evolve; preserve raw trace and extract
            # likely tool identifiers conservatively.
            if not isinstance(event, dict):
                continue
            typ = str(event.get("type", ""))
            if "tool" in typ or "command" in typ or "item." in typ:
                blob = list(recursive_strings(event))
                candidate = next(
                    (x for x in blob if "ffsake" in x.lower()),
                    typ,
                )
                names.append(candidate)

    for name in names:
        low = name.lower()
        if "ffsake" in low:
            ffsake += 1
        if any(x in low for x in ["bash", "shell", "command_execution", "exec_command"]):
            shell += 1

    blob = "\n".join(recursive_strings(events)).lower()
    result_event = next(
        (e for e in reversed(events) if isinstance(e, dict) and e.get("type") == "result"),
        {},
    )
    usage = result_event.get("usage", {}) if isinstance(result_event, dict) else {}

    return {
        "events": len(events),
        "tool_calls": len(names),
        "tool_names": names,
        "ffsake_tool_calls": ffsake,
        "shell_tool_calls": shell,
        "grep_mentions": blob.count("grep"),
        "sed_mentions": blob.count("sed"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "total_cost_usd": result_event.get("total_cost_usd") if isinstance(result_event, dict) else None,
        "num_turns": result_event.get("num_turns") if isinstance(result_event, dict) else None,
    }


def claude_run(root: Path, prompt: str, use_ffsake: bool, timeout: int):
    cmd = [
        "claude", "-p", ffsake_prompt(prompt) if use_ffsake else prompt,
        "--output-format", "stream-json", "--verbose",
        "--max-turns", "40", "--dangerously-skip-permissions",
    ]
    if use_ffsake:
        cfg = root / ".ffsake-claude-mcp.json"
        cfg.write_text(json.dumps({
            "mcpServers": {"ffsake": {"command": str(FFSAKE_MCP), "args": [str(root)]}}
        }))
        cmd += ["--mcp-config", str(cfg), "--disallowedTools", "Bash,Read,Write,Edit,Glob,Grep"]
    return run_process(cmd, root, timeout)


def codex_run(root: Path, prompt: str, use_ffsake: bool, timeout: int):
    if use_ffsake:
        cfgdir = root / ".codex"
        cfgdir.mkdir(exist_ok=True)
        command = str(FFSAKE_MCP).replace("\\", "\\\\").replace('"', '\\"')
        workspace = str(root).replace("\\", "\\\\").replace('"', '\\"')
        (cfgdir / "config.toml").write_text(
            "[mcp_servers.ffsake]\n"
            f'command = "{command}"\n'
            f'args = ["{workspace}"]\n'
        )
    cmd = [
        "codex", "exec", "--json", "--sandbox", "workspace-write",
        "--ask-for-approval", "never",
    ]
    if use_ffsake:
        # Codex only loads project .codex/config.toml when the repo is trusted.
        # Keep the trust override process-local instead of mutating user config.
        project = str(root).replace("\\\\", "\\\\\\\\").replace('"', '\\\\"')
        cmd += ["-c", f'projects={{"{project}"={{trust_level="trusted"}}}}']
    cmd += [ffsake_prompt(prompt) if use_ffsake else prompt]
    return run_process(cmd, root, timeout)


def opencode_run(root: Path, prompt: str, use_ffsake: bool, timeout: int, model: str | None):
    if use_ffsake:
        (root / "opencode.json").write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "ffsake": {
                    "type": "local",
                    "command": [str(FFSAKE_MCP), str(root)],
                    "enabled": True,
                }
            },
            "tools": {
                "bash": False,
                "read": False,
                "edit": False,
                "glob": False,
                "grep": False,
            },
        }, indent=2))
    cmd = ["opencode", "run", "--format", "json", "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", model]
    cmd += [ffsake_prompt(prompt) if use_ffsake else prompt]
    return run_process(cmd, root, timeout)


def clean_control_files(root: Path):
    for path in [
        root / ".ffsake-claude-mcp.json",
        root / ".codex",
        root / "opencode.json",
        root / ".ffsake",
    ]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def git_changed_files(root: Path):
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line[3:] for line in proc.stdout.splitlines() if len(line) >= 4]


def one(agent, task_name, mode, timeout, opencode_model):
    task = TASKS[task_name]
    use_ffsake = mode == "ffsake"
    with tempfile.TemporaryDirectory(prefix=f"ffsake-suite-{agent}-{task_name}-") as td:
        root = Path(td)
        state = task["fixture"](root)

        if agent == "claude":
            result = claude_run(root, task["prompt"], use_ffsake, timeout)
        elif agent == "codex":
            result = codex_run(root, task["prompt"], use_ffsake, timeout)
        elif agent == "opencode":
            result = opencode_run(root, task["prompt"], use_ffsake, timeout, opencode_model)
        else:
            raise ValueError(agent)

        events = parse_jsonl(result["stdout"])
        metrics = generic_metrics(agent, events)
        clean_control_files(root)
        correct, verification = task["verify"](root, state)
        changed = git_changed_files(root)

        return {
            "agent": agent,
            "task": task_name,
            "mode": mode,
            "correct": correct,
            "verification": verification,
            "changed_files": changed,
            "exit_code": result["exit_code"],
            "elapsed_ms": result["elapsed_ms"],
            "timed_out": result["timed_out"],
            "trace": metrics,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }


def median_or_none(values):
    values = [x for x in values if isinstance(x, (int, float))]
    return statistics.median(values) if values else None


def aggregate(results):
    groups = {}
    for result in results:
        key = (result["agent"], result["task"], result["mode"])
        groups.setdefault(key, []).append(result)

    rows = []
    for (agent, task, mode), items in sorted(groups.items()):
        rows.append({
            "agent": agent,
            "task": task,
            "mode": mode,
            "runs": len(items),
            "correct_runs": sum(bool(x["correct"]) for x in items),
            "success_rate": sum(bool(x["correct"]) for x in items) / len(items),
            "median_elapsed_ms": median_or_none([x["elapsed_ms"] for x in items]),
            "median_tool_calls": median_or_none([x["trace"]["tool_calls"] for x in items]),
            "median_ffsake_tool_calls": median_or_none([x["trace"]["ffsake_tool_calls"] for x in items]),
            "median_shell_tool_calls": median_or_none([x["trace"]["shell_tool_calls"] for x in items]),
            "median_output_tokens": median_or_none([x["trace"]["output_tokens"] for x in items]),
            "median_cost_usd": median_or_none([x["trace"]["total_cost_usd"] for x in items]),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", default="claude,codex,opencode")
    parser.add_argument("--tasks", default="micro,medium,app")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--opencode-model")
    args = parser.parse_args()

    agents = [x.strip() for x in args.agents.split(",") if x.strip()]
    tasks = [x.strip() for x in args.tasks.split(",") if x.strip()]
    for agent in agents:
        if agent not in {"claude", "codex", "opencode"}:
            raise SystemExit(f"unknown agent: {agent}")
        if shutil.which(agent) is None:
            raise SystemExit(f"missing CLI: {agent}")
    for task in tasks:
        if task not in TASKS:
            raise SystemExit(f"unknown task: {task}")
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be >= 1")
    if not FFSAKE_MCP.exists():
        raise SystemExit("build first: cargo build --locked --bin ffsake-mcp")

    results = []
    out = reserve_report_path()
    report = {
        "agents": agents,
        "tasks": tasks,
        "repetitions": args.repetitions,
        "results": results,
        "aggregate": [],
        "complete": False,
        "known_limitations": [
            "The app task completes an existing scaffold because FFSAKE MCP does not yet expose create-file/write-new-file primitives.",
            "FFSAKE mode has no build/test runtime adapter; correctness is verified externally by this harness.",
            "Codex and OpenCode tool-event schemas differ from Claude, so non-Claude tool-count extraction is conservative; raw traces are retained.",
            "OpenCode uses its configured default model unless --opencode-model is supplied.",
        ],
    }
    save_report(out, report)
    print(f"Saving results after every run to: {out}", flush=True)
    total = len(agents) * len(tasks) * 2 * args.repetitions
    index = 0
    for agent in agents:
        for task in tasks:
            for repetition in range(1, args.repetitions + 1):
                for mode in ["baseline", "ffsake"]:
                    index += 1
                    print(f"[{index}/{total}] {agent} {task} {mode} run {repetition}", flush=True)
                    result = one(agent, task, mode, args.timeout, args.opencode_model)
                    result["repetition"] = repetition
                    results.append(result)
                    report["aggregate"] = aggregate(results)
                    save_report(out, report)
                    print(json.dumps({
                        "correct": result["correct"],
                        "elapsed_ms": result["elapsed_ms"],
                        "tool_calls": result["trace"]["tool_calls"],
                        "ffsake_tool_calls": result["trace"]["ffsake_tool_calls"],
                    }), flush=True)

    report["complete"] = True
    save_report(out, report)

    print("\nAGGREGATE")
    for row in report["aggregate"]:
        print(json.dumps(row, sort_keys=True))
    print(f"\nFull suite trace: {out}")


if __name__ == "__main__":
    main()
