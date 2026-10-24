"""Zero-agent regression tests for benchmark report persistence."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agent_benchmark_suite.py"
spec = importlib.util.spec_from_file_location("agent_benchmark_suite", SCRIPT)
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


class GitFixtureTests(unittest.TestCase):
    def test_fixture_disables_ambient_hooks_and_background_git_work(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            hooks = base / "hooks"
            hooks.mkdir()
            sentinel = base / "HOOK_RAN"
            hook = hooks / "post-commit"
            hook.write_text(f"#!/bin/sh\nprintf ran > '{sentinel}'\n")
            hook.chmod(0o755)
            configuration = base / "global.gitconfig"
            configuration.write_text(f'[core]\n hooksPath = "{hooks}"\n[maintenance]\n auto = true\n[gc]\n auto = 1\n autoDetach = true\n')
            root = base / "fixture"
            root.mkdir()
            (root / "file").write_text("fixture")
            with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(configuration), "GIT_CONFIG_NOSYSTEM": "1"}):
                suite.git_init(root)
                self.assertFalse(sentinel.exists())
                for key, expected in (("maintenance.auto", "false"), ("gc.auto", "0"),
                                      ("gc.autoDetach", "false"), ("core.fsmonitor", "false")):
                    actual = subprocess.check_output(["git", "config", "--local", key], cwd=root, text=True).strip()
                    self.assertEqual(actual, expected)
                self.assertEqual(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True), "")


class PersistenceTests(unittest.TestCase):
    def test_bytes_decode(self):
        self.assertEqual(suite.decode_output(b"ok" + bytes([255])), "ok�")
        self.assertEqual(suite.decode_output(None), "")
        self.assertEqual(suite.decode_output("hello"), "hello")

    def test_atomic_save_and_replace(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "results.json"
            first = {"results": [{"stdout": "ok", "stderr": "�", "timed_out": True}], "complete": False}
            suite.save_report(path, first)
            self.assertEqual(json.loads(path.read_text()), first)
            second = {"results": first["results"] * 2, "complete": True}
            suite.save_report(path, second)
            self.assertEqual(json.loads(path.read_text()), second)
            self.assertEqual(list(Path(td).glob("*.tmp")), [])

    def test_concurrent_report_paths_are_distinct(self):
        from concurrent.futures import ThreadPoolExecutor

        original_dir = suite.RESULT_DIR
        with tempfile.TemporaryDirectory() as td:
            suite.RESULT_DIR = Path(td)
            try:
                with ThreadPoolExecutor(max_workers=16) as pool:
                    paths = list(pool.map(lambda _: suite.reserve_report_path(), range(64)))
                self.assertEqual(len(paths), len(set(paths)))
                self.assertTrue(all(path.exists() for path in paths))
                for index, path in enumerate(paths):
                    suite.save_report(path, {"run": index})
                self.assertEqual(
                    sorted(json.loads(path.read_text())["run"] for path in paths),
                    list(range(64)),
                )
            finally:
                suite.RESULT_DIR = original_dir

    def test_initial_fixtures_require_work_and_verification_does_not_mutate_them(self):
        for name, task in suite.TASKS.items():
            with self.subTest(task=name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                state = task["fixture"](root)
                before = suite.fixture_files(root)
                complete, details = task["verify"](root, state)
                self.assertFalse(complete, f"initial {name} fixture already passes: {details}")
                self.assertEqual(suite.fixture_files(root), before)

    def test_verifier_reads_current_source_despite_same_size_and_timestamp_cache(self):
        import os
        import py_compile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "fixture.py"
            path.write_text("value = 1\n")
            stamp = path.stat()
            cache = Path(py_compile.compile(str(path), doraise=True,
                                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP))
            cached_bytes = cache.read_bytes()
            path.write_text("value = 2\n")
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            before = suite.fixture_files(root)
            module = suite.load_verifier_module("fixture_bench", path)
            self.assertEqual(module.value, 2)
            self.assertEqual(module.__file__, str(path))
            self.assertEqual(cache.read_bytes(), cached_bytes)
            self.assertEqual(suite.fixture_files(root), before)

    def test_medium_fixture_rule_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = suite.medium_fixture(root)
            source = (root / "policy.py").read_text()
            self.assertIn("TODO_ALLOWED", source)
            self.assertEqual(state, {})
            rules = [
                {"method": "GET", "prefix": "/public", "allow": True},
                {"method": "POST", "prefix": "/admin/invite", "allow": True},
                {"method": "*", "prefix": "/admin", "allow": False},
            ]
            self.assertEqual(next(r for r in rules if
                (r["method"] == "*" or r["method"] == "POST")
                and "/admin/invite/7".startswith(r["prefix"]))["allow"], True)

    def test_generic_metrics_do_not_infer_tool_from_text(self):
        events = [{"type": "tool_call", "message": "Use FFSAKE for this task"}]
        metrics = suite.generic_metrics("opencode", events)
        self.assertEqual(metrics["ffsake_tool_calls"], 0)

    def test_codex_invocation_uses_supported_arguments(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            with patch.object(suite, "run_process", return_value={}) as mocked:
                suite.codex_run(Path(td), "task", False, 1)
            args = mocked.call_args.args[0]
            self.assertNotIn("--ask-for-approval", args)
            self.assertEqual(args[:3], ["codex", "exec", "--json"])

    def test_agent_children_disable_bytecode_without_changing_parent_environment(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "0"}):
            root = Path(td)
            (root / "sample.py").write_text("VALUE = 7\n")
            # Model an agent launching its own Python verification command without -B.
            code = ("import os, subprocess, sys; "
                    "assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'; "
                    "subprocess.run([sys.executable, '-c', 'import sample; print(sample.VALUE)'], check=True)")
            result = suite.run_process([sys.executable, "-c", code], root, 5)
            self.assertEqual(result["exit_code"], 0, result["stderr"])
            self.assertEqual(result["stdout"].strip(), "7")
            self.assertEqual(set(suite.fixture_files(root)), {"sample.py"})
            self.assertEqual(os.environ["PYTHONDONTWRITEBYTECODE"], "0")

    def test_timeout_returns_json_safe_text(self):
        with tempfile.TemporaryDirectory() as td:
            command = [sys.executable, "-c", "import sys,time;sys.stdout.buffer.write(bytes([255]));sys.stdout.flush();time.sleep(5)"]
            result = suite.run_process(command, Path(td), timeout=1)
            self.assertTrue(result["timed_out"])
            self.assertIsInstance(result["stdout"], str)
            self.assertIsInstance(result["stderr"], str)
            json.dumps(result)



class AccountingTests(unittest.TestCase):
    def test_codex_lifecycle_is_one_call_and_messages_are_not_calls(self):
        item = {"id": "tool-1", "type": "mcp_tool_call", "server": "ffsake", "tool": "read"}
        events = [{"type": kind, "item": item} for kind in
                  ["item.started", "item.updated", "item.completed"]]
        events += [{"type": "item.completed", "item": {"id": "msg", "type": "agent_message", "text": "ffsake"}},
                   {"type": "item.completed", "item": {"id": "cmd", "type": "command_execution"}},
                   {"type": "turn.completed", "usage": {"input_tokens": 123, "output_tokens": 45}}]
        metrics = suite.generic_metrics("codex", events)
        self.assertEqual(metrics["tool_calls"], 2)
        self.assertEqual(metrics["ffsake_tool_calls"], 1)
        self.assertEqual(metrics["shell_tool_calls"], 1)
        self.assertEqual(metrics["input_tokens"], 123)
        self.assertEqual(metrics["output_tokens"], 45)

    def test_opencode_uses_part_tool_and_deduplicates(self):
        event = {"type": "tool_use", "part": {"callID": "call1", "tool": "ffsake_read"}}
        metrics = suite.generic_metrics("opencode", [event, event,
            {"type": "text", "part": {"text": "ffsake bash"}},
            {"type": "tool_use", "part": {"callID": "call2", "tool": "bash"}}])
        self.assertEqual(metrics["tool_calls"], 2)
        self.assertEqual(metrics["ffsake_tool_calls"], 1)
        self.assertEqual(metrics["shell_tool_calls"], 1)

    def test_claude_duplicate_assistant_blocks_are_one_call(self):
        event = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "1", "name": "mcp__ffsake__read"}]}}
        self.assertEqual(suite.generic_metrics("claude", [event, event])["tool_calls"], 1)

    def test_policy_detects_contamination_and_forbidden_native_tools(self):
        metrics = {"tool_names": ["mcp__ffsake__read", "file_change"]}
        baseline = suite.permission_audit("codex", False, metrics)
        restricted = suite.permission_audit("codex", True, metrics)
        self.assertEqual(baseline["observed_policy_violations"], ["mcp__ffsake__read"])
        self.assertEqual(restricted["observed_policy_violations"], ["file_change"])
        self.assertFalse(restricted["native_tool_restrictions_configured"])
        self.assertFalse(restricted["permission_isolation_verified"])
        self.assertEqual(suite.permission_audit("codex", True, {"tool_names": []})[
            "trace_policy_status"], "unverified")

    def test_unrelated_server_name_is_not_ffsake(self):
        self.assertFalse(suite.is_ffsake_tool("mcp__not_ffsake__read"))


class RunValidationTests(unittest.TestCase):
    def run_mock_agent(self, mutation, stdout=''):
        from unittest.mock import patch
        def run(root, *args):
            mutation(root)
            return {"stdout": stdout, "stderr": "", "elapsed_ms": 1,
                    "exit_code": 0, "timed_out": False}
        with patch.object(suite, "codex_run", side_effect=run):
            return suite.one("codex", "medium", "baseline", 1, None)

    def test_forbidden_changes_fail_even_when_behavior_verifier_passes(self):
        def mutation(root):
            (root / "README.md").write_text("changed")
            (root / "extra.txt").write_text("unexpected")
        from unittest.mock import patch
        with patch.dict(suite.TASKS["medium"], {"verify": lambda *_: (True, {})}):
            result = self.run_mock_agent(mutation)
        self.assertFalse(result["correct"])
        self.assertEqual(result["verification"]["unexpected_file_changes"], ["README.md", "extra.txt"])

    def test_verification_time_changes_fail_without_being_attributed_to_agent_edits(self):
        from unittest.mock import patch
        mutations = {
            "extra.txt": lambda root: (root / "extra.txt").write_text("created at runtime"),
            "README.md": lambda root: (root / "README.md").unlink(),
            "policy.py": lambda root: (root / "policy.py").write_text("changed at runtime"),
        }
        for path, mutate in mutations.items():
            for raises in (False, True):
                with self.subTest(path=path, raises=raises):
                    def verify(root, state):
                        mutate(root)
                        if raises:
                            raise ValueError("verification failed after mutation")
                        return True, {}
                    with patch.dict(suite.TASKS["medium"], {"verify": verify}):
                        result = self.run_mock_agent(lambda _: None)
                    self.assertFalse(result["correct"])
                    self.assertEqual(result["changed_files"], [])
                    self.assertEqual(result["verification"]["unexpected_file_changes"], [])
                    self.assertEqual(result["verification"]["verification_file_changes"], [path])

    def test_file_audit_failure_preserves_completed_agent_trace(self):
        from unittest.mock import patch
        original = suite.fixture_files
        for failure_call, phase in ((2, "after_agent"), (3, "after_verification")):
            with self.subTest(phase=phase):
                calls = 0
                def snapshot(root):
                    nonlocal calls
                    calls += 1
                    if calls == failure_call:
                        raise PermissionError("blocked fixture read")
                    return original(root)
                with patch.object(suite, "fixture_files", side_effect=snapshot), \
                     patch.dict(suite.TASKS["medium"], {"verify": lambda *_: (True, {})}):
                    result = self.run_mock_agent(lambda _: None, "completed agent trace")
                self.assertFalse(result["correct"])
                self.assertFalse(result["comparison_valid"])
                self.assertIn("file_audit_failed", result["comparison_invalid_reasons"])
                self.assertEqual(result["stdout"], "completed agent trace")
                self.assertFalse(result["verification"]["file_audit_complete"])
                self.assertEqual(result["verification"]["file_audit_errors"][0]["phase"], phase)
                self.assertIsNone(result["verification"]["verification_file_changes"])
                row = suite.aggregate([result])[0]
                self.assertEqual(row["comparison_valid_runs"], 0)
                self.assertIsNone(row["median_elapsed_ms"])
                if phase == "after_agent":
                    self.assertIsNone(result["changed_files"])
                    self.assertIsNone(result["verification"]["unexpected_file_changes"])

    def test_missing_required_file_is_recorded_without_losing_run(self):
        result = self.run_mock_agent(lambda root: (root / "policy.py").unlink())
        self.assertFalse(result["correct"])
        self.assertIn("policy.py", result["verification"]["unexpected_file_changes"])
        json.dumps(result)

    def test_contaminated_baseline_is_invalid(self):
        event = {"type": "item.completed", "item": {
            "id": "1", "type": "mcp_tool_call", "server": "ffsake", "tool": "read"}}
        result = self.run_mock_agent(lambda _: None, json.dumps(event))
        self.assertFalse(result["comparison_valid"])
        self.assertIn("tool_policy_violation", result["comparison_invalid_reasons"])


class AggregateTests(unittest.TestCase):
    def test_invalid_runs_do_not_contribute_performance_medians(self):
        def result(valid, elapsed):
            return {"agent": "codex", "task": "micro", "mode": "baseline",
                    "comparison_valid": valid, "correct": valid, "elapsed_ms": elapsed,
                    "exit_code": 0, "timed_out": False,
                    "trace": {"tool_calls": 1, "ffsake_tool_calls": 0,
                              "shell_tool_calls": 1, "output_tokens": None, "total_cost_usd": None}}
        row = suite.aggregate([result(False, 1), result(True, 200)])[0]
        self.assertEqual(row["median_elapsed_ms"], 200)
        self.assertEqual(row["comparison_valid_runs"], 1)
        self.assertEqual(row["runs"], 2)
        self.assertIsNone(suite.aggregate([result(False, 1)])[0]["median_elapsed_ms"])


class IsolationTests(unittest.TestCase):
    def test_claude_baseline_explicitly_has_no_mcp_servers(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, patch.object(suite, "run_process", return_value={}) as run:
            suite.claude_run(Path(td), "task", False, 1)
        args = run.call_args.args[0]
        self.assertIn("--strict-mcp-config", args)
        self.assertEqual(json.loads(args[args.index("--mcp-config") + 1]), {"mcpServers": {}})

    def test_claude_ffsake_disables_all_builtin_tools(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, patch.object(suite, "run_process", return_value={}) as run:
            suite.claude_run(Path(td), "task", True, 1)
            args = run.call_args.args[0]
            self.assertEqual(args[args.index("--tools") + 1], "")
            config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
            self.assertEqual(set(config["mcpServers"]), {"ffsake"})

    def test_opencode_allowlist_excludes_unlisted_tools_in_both_modes(self):
        from unittest.mock import patch
        import fnmatch
        def permitted(rules, name):
            action = None
            for pattern, value in rules.items():
                if fnmatch.fnmatchcase(name, pattern):
                    action = value
            return action == "allow"
        for use_ffsake in [False, True]:
            with self.subTest(use_ffsake=use_ffsake), tempfile.TemporaryDirectory() as td:
                with patch.object(suite, "run_process", return_value={}) as run:
                    suite.opencode_run(Path(td), "task", use_ffsake, 1, None)
                rules = json.loads((Path(td) / "opencode.json").read_text())["permission"]
                self.assertEqual(permitted(rules, "ffsake_ffsake_read"), use_ffsake)
                self.assertEqual(permitted(rules, "read"), not use_ffsake)
                self.assertFalse(permitted(rules, "task"))
                self.assertFalse(permitted(rules, "other_mcp_read"))
                self.assertNotIn("--dangerously-skip-permissions", run.call_args.args[0])


class PreflightTests(unittest.TestCase):
    def test_preflight_invokes_only_help_and_version(self):
        from unittest.mock import patch
        calls = []
        def run(cmd, *args):
            calls.append(cmd)
            self.assertIn(cmd[-1], ["--help", "--version"])
            return {"stdout": "--format --model --dir" if cmd[-1] == "--help" else "1.2.3",
                    "stderr": "", "exit_code": 0, "timed_out": False}
        with patch.object(suite.shutil, "which", return_value="/fake/opencode"), patch.object(suite, "run_process", side_effect=run), patch.object(suite, "opencode_workspace_preflight", return_value={"ok": True, "model_calls": 0}):
            report = suite.preflight(["opencode"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(len(calls), 2)

    def test_unsupported_cli_stops_suite_and_saves_diagnostic(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            result_dir = Path(td)
            with patch.object(suite, "RESULT_DIR", result_dir), patch.object(sys, "argv", ["suite", "--agents", "codex"]), patch.object(suite.shutil, "which", return_value=None), patch.object(suite, "one") as agent:
                with self.assertRaisesRegex(SystemExit, "no model calls"):
                    suite.main()
                agent.assert_not_called()
            report = json.loads(next(result_dir.glob("*.json")).read_text())
            self.assertEqual(report["results"], [])
            self.assertFalse(report["preflight"]["ok"])
            self.assertEqual(report["preflight"]["checks"][0]["error"], "missing_cli")


class ClaudePreflightTests(unittest.TestCase):
    HELP = "--output-format --verbose --dangerously-skip-permissions --strict-mcp-config --mcp-config --tools"

    def preflight_with_help(self, help_text):
        from unittest.mock import patch
        def run(cmd, *args):
            self.assertIn(cmd[-1], ["--help", "--version"])
            return {"stdout": help_text if cmd[-1] == "--help" else "2.1.306 (Claude Code)",
                    "stderr": "", "exit_code": 0, "timed_out": False}
        with patch.object(suite.shutil, "which", return_value="/fake/claude"), patch.object(suite, "run_process", side_effect=run):
            return suite.preflight(["claude"])

    def test_documented_print_only_flag_missing_from_help_is_not_failure(self):
        report = self.preflight_with_help(self.HELP)
        self.assertTrue(report["ok"])
        self.assertEqual(report["checks"][0]["missing_flags"], [])
        self.assertEqual(report["checks"][0]["unlisted_documented_flags"], ["--max-turns"])
        self.assertEqual(report["model_calls"], 0)

    def test_required_isolation_flag_is_still_mandatory(self):
        report = self.preflight_with_help(self.HELP.replace("--tools", ""))
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"][0]["missing_flags"], ["--tools"])

    def test_visible_turn_flag_is_not_reported_unlisted(self):
        report = self.preflight_with_help(self.HELP + " --max-turns")
        self.assertTrue(report["ok"])
        self.assertEqual(report["checks"][0]["unlisted_documented_flags"], [])

    def test_turn_cap_remains_in_both_benchmark_modes(self):
        from unittest.mock import patch
        for mode in [False, True]:
            with tempfile.TemporaryDirectory() as td, patch.object(suite, "run_process", return_value={}) as run:
                suite.claude_run(Path(td), "task", mode, 1)
                args = run.call_args.args[0]
                self.assertEqual(args[args.index("--max-turns") + 1], "40")


class CodexApprovalTests(unittest.TestCase):
    def test_only_known_workspace_mcp_tools_are_approved(self):
        import tomllib
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, patch.object(suite, "run_process", return_value={}) as run:
            root = Path(td) / 'repo with "quotes"'
            root.mkdir()
            suite.codex_run(root, "task", True, 1)
            config = tomllib.loads((root / ".codex/config.toml").read_text())
            self.assertEqual(set(config), {"mcp_servers"})
            self.assertEqual(set(config["mcp_servers"]), {"ffsake"})
            server = config["mcp_servers"]["ffsake"]
            self.assertEqual(server["command"], str(suite.FFSAKE_MCP))
            self.assertEqual(server["args"], [str(root)])
            self.assertTrue(server["required"])
            self.assertEqual(set(server["enabled_tools"]), set(suite.FFSAKE_TOOLS))
            self.assertEqual(server["tools"], {tool: {"approval_mode": "approve"} for tool in suite.FFSAKE_TOOLS})
            args = run.call_args.args[0]
            trust = tomllib.loads(args[args.index("-c") + 1])
            self.assertEqual(trust["projects"][str(root)]["trust_level"], "trusted")
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)

    def test_baseline_does_not_create_approved_mcp_config(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, patch.object(suite, "run_process", return_value={}):
            suite.codex_run(Path(td), "task", False, 1)
            self.assertFalse((Path(td) / ".codex").exists())

    def test_approval_blocked_run_is_invalid_even_with_zero_exit(self):
        from unittest.mock import patch
        event = {"type": "item.completed", "item": {"id": "1", "type": "mcp_tool_call",
            "server": "ffsake", "tool": "ffsake_search", "status": "failed", "result": None,
            "error": {"message": "MCP tool call requires approval, but approval policy is never"}}}
        output = {"stdout": json.dumps(event), "stderr": "", "exit_code": 0, "elapsed_ms": 1, "timed_out": False}
        with patch.object(suite, "codex_run", return_value=output):
            result = suite.one("codex", "micro", "ffsake", 1, None)
        self.assertFalse(result["comparison_valid"])
        self.assertIn("mcp_approval_blocked", result["comparison_invalid_reasons"])
        self.assertEqual(result["trace"]["mcp_failed_tool_calls"], 1)
        self.assertEqual(result["trace"]["mcp_approval_blocked_calls"], 1)
        self.assertEqual(result["trace"]["tool_calls"], 1)

    def test_recoverable_tool_error_is_not_approval_failure(self):
        event = {"type": "item.completed", "item": {"id": "1", "type": "mcp_tool_call",
            "server": "ffsake", "tool": "ffsake_read", "status": "completed",
            "result": {"isError": True}, "error": None}}
        metrics = suite.generic_metrics("codex", [event, event])
        self.assertEqual(metrics["mcp_failed_tool_calls"], 1)
        self.assertEqual(metrics["mcp_approval_blocked_calls"], 0)

    def test_codex_cache_usage_is_preserved(self):
        metrics = suite.generic_metrics("codex", [{"type": "turn.completed", "usage": {
            "input_tokens": 60315, "cached_input_tokens": 51584, "cache_write_input_tokens": 0, "output_tokens": 168}}])
        self.assertEqual(metrics["cache_read_input_tokens"], 51584)
        self.assertEqual(metrics["cache_creation_input_tokens"], 0)


class ModeSelectionTests(unittest.TestCase):
    def test_baseline_only_does_not_require_mcp_binary(self):
        from unittest.mock import patch
        import contextlib
        import io
        result = {"agent": "codex", "task": "micro", "mode": "baseline", "comparison_valid": True,
                  "correct": True, "exit_code": 0, "timed_out": False, "elapsed_ms": 1,
                  "trace": {"tool_calls": 1, "ffsake_tool_calls": 0, "shell_tool_calls": 1,
                            "output_tokens": 1, "total_cost_usd": None}}
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            arguments = ["suite", "--agents", "codex", "--tasks", "micro", "--modes", "baseline",
                         "--tool-profile", "core", "--repetitions", "1"]
            with patch.object(sys, "argv", arguments), patch.object(suite, "preflight", return_value={"ok": True}) as check, \
                 patch.object(suite, "FFSAKE_MCP", Path(td) / "missing"), patch.object(suite, "RESULT_DIR", Path(td)), \
                 patch.object(suite, "one", return_value=result) as run:
                suite.main()
            check.assert_called_once_with(["codex"], "all")
            run.assert_called_once_with("codex", "micro", "baseline", 900, None, "core")
            report = json.loads(next(Path(td).glob("*.json")).read_text())
            self.assertTrue(report["complete"])
            self.assertIsNone(report["provenance"]["mcp_binary_sha256"])
            arguments[arguments.index("baseline")] = "ffsake"
            with patch.object(sys, "argv", arguments), patch.object(suite, "preflight", return_value={"ok": True}), \
                 patch.object(suite, "FFSAKE_MCP", Path(td) / "missing"), patch.object(suite, "one") as run:
                with self.assertRaisesRegex(SystemExit, "build first"):
                    suite.main()
                run.assert_not_called()

    def test_empty_or_duplicate_matrix_selections_fail_before_preflight(self):
        from unittest.mock import patch
        for flag, value in (("--agents", ""), ("--tasks", ","), ("--modes", ""),
                            ("--agents", "codex,codex"), ("--tasks", "micro,micro"),
                            ("--modes", "baseline,baseline")):
            with self.subTest(flag=flag, value=value), \
                 patch.object(sys, "argv", ["suite", flag, value]), \
                 patch.object(suite, "preflight", side_effect=AssertionError("preflight must not run")) as check:
                with self.assertRaises(SystemExit):
                    suite.main()
                check.assert_not_called()

    def test_targeted_mode_does_not_rerun_baseline(self):
        from unittest.mock import patch
        import contextlib
        import io
        result = {"agent": "codex", "task": "micro", "mode": "ffsake", "comparison_valid": True,
                  "correct": True, "exit_code": 0, "timed_out": False, "elapsed_ms": 1,
                  "trace": {"tool_calls": 1, "ffsake_tool_calls": 1, "shell_tool_calls": 0,
                            "output_tokens": 1, "total_cost_usd": None}}
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            with patch.object(sys, "argv", ["suite", "--agents", "codex", "--tasks", "micro", "--modes", "ffsake", "--repetitions", "1"]), patch.object(suite, "preflight", return_value={"ok": True}), patch.object(suite, "FFSAKE_MCP", Path(__file__)), patch.object(suite, "RESULT_DIR", Path(td)), patch.object(suite, "one", return_value=result) as run:
                suite.main()
            run.assert_called_once_with("codex", "micro", "ffsake", 900, None, "all")
            report = json.loads(next(Path(td).glob("*.json")).read_text())
            self.assertTrue(report["complete"])
            self.assertEqual(report["modes"], ["ffsake"])
            self.assertEqual(len(report["results"]), 1)

    def test_invalid_mode_is_rejected_before_preflight(self):
        from unittest.mock import patch
        with patch.object(sys, "argv", ["suite", "--modes", "wrong"]), patch.object(suite, "preflight") as check:
            with self.assertRaisesRegex(SystemExit, "--modes"):
                suite.main()
            check.assert_not_called()

class WorkspaceProcessTests(unittest.TestCase):
    def test_subprocess_pwd_matches_cwd_without_changing_parent(self):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"PWD": "/wrong/parent"}):
            result = suite.run_process([sys.executable, "-c", "import os,json;print(json.dumps([os.getcwd(),os.environ['PWD']]))"], Path(td), 5)
            self.assertEqual(json.loads(result["stdout"]), [str(Path(td).resolve())] * 2)
            self.assertEqual(os.environ["PWD"], "/wrong/parent")

    @unittest.skipUnless(sys.platform != "win32", "POSIX process groups")
    def test_timeout_kills_launcher_descendants(self):
        import time
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "escaped-child.txt"
            child = "import time;from pathlib import Path;time.sleep(2);Path(" + repr(str(marker)) + ").write_text('leaked')"
            launcher = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);print('spawned',flush=True);time.sleep(30)"
            result = suite.run_process([sys.executable, "-c", launcher], root, 1)
            self.assertTrue(result["timed_out"])
            self.assertIn("spawned", result["stdout"])
            time.sleep(1.5)
            self.assertFalse(marker.exists(), "Timed-out launcher left a running child")

    def test_invalid_utf8_on_normal_exit_is_json_safe(self):
        with tempfile.TemporaryDirectory() as td:
            result = suite.run_process([sys.executable, "-c", "import sys;sys.stdout.buffer.write(bytes([255]))"], Path(td), 5)
        self.assertEqual(result["stdout"], "�")
        self.assertEqual(result["exit_code"], 0)
        json.dumps(result)


class OpenCodeWorkspaceTests(unittest.TestCase):
    def make_cli(self, folder):
        path = folder / "opencode"
        path.write_text("#!" + sys.executable + "\n" + '''import base64,json,os,sys
from pathlib import Path
args=sys.argv[1:]
root=Path(os.environ.get("PWD",os.getcwd()))
if args and args[-1]=="--help":
 print("--format --model --dir")
elif args==["--version"]:
 print("1.18.31")
elif args[:3]==["debug","file","read"]:
 print(json.dumps({"content":base64.b64encode((root/args[3]).read_bytes()).decode(),"encoding":"base64"}))
elif args==["debug","config"]:
 config=json.loads((root/"opencode.json").read_text())
 if os.environ.get("INHERIT_FFSAKE"):
  server={"type":"local","command":["inherited-global-server"],"enabled":True}
  server.update(config.get("mcp",{}).get("ffsake",{}))
  config.setdefault("mcp",{})["ffsake"]=server
 config["provider"]={"private":"do-not-persist-this"}
 if os.environ.get("BAD_POLICY"): config["permission"]["*"]="allow"
 if os.environ.get("BAD_ORDER"): config["permission"]["*"]=config["permission"].pop("*")
 print(json.dumps(config))
elif args and args[0]=="run":
 root=Path(args[args.index("--dir")+1])
 print(json.dumps({"directory":str(root),"pwd":os.environ["PWD"],"config":json.loads((root/"opencode.json").read_text())}))
else:
 raise SystemExit("Unexpected command: " + repr(args))
''')
        path.chmod(0o755)
        return path

    @unittest.skipIf(sys.platform == "win32", "Executable shebang fixture")
    def test_actual_launcher_binds_fixture_with_wrong_inherited_pwd(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            self.make_cli(folder)
            root = folder / "fixture"
            root.mkdir()
            with patch.dict(os.environ, {"PATH": str(folder) + os.pathsep + os.environ["PATH"], "PWD": "/wrong/real-checkout"}):
                result = suite.opencode_run(root, "task", True, 5, None)
                data = json.loads(result["stdout"])
                self.assertEqual(data["directory"], str(root.resolve()))
                self.assertEqual(data["pwd"], str(root.resolve()))
                self.assertEqual(data["config"]["mcp"]["ffsake"]["command"][-1], str(root.resolve()))
                self.assertEqual(os.environ["PWD"], "/wrong/real-checkout")

    @unittest.skipIf(sys.platform == "win32", "Executable shebang fixture")
    def test_model_free_probe_verifies_both_configs_without_saving_credentials(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            executable = self.make_cli(Path(td))
            with patch.dict(os.environ, {"PWD": "/wrong/real-checkout"}):
                report = suite.opencode_workspace_preflight(str(executable))
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["checks"]), 2)
        self.assertEqual(report["model_calls"], 0)
        self.assertNotIn("do-not-persist-this", json.dumps(report))

    @unittest.skipIf(sys.platform == "win32", "Executable shebang fixture")
    def test_probe_overrides_inherited_enabled_ffsake_server(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            executable = self.make_cli(Path(td))
            with patch.dict(os.environ, {"INHERIT_FFSAKE": "1"}):
                report = suite.opencode_workspace_preflight(str(executable))
        self.assertTrue(report["ok"])
        self.assertTrue(all(c["mcp_configuration_matches"] for c in report["checks"]))
        self.assertEqual(report["model_calls"], 0)

    @unittest.skipIf(sys.platform == "win32", "Executable shebang fixture")
    def test_model_free_probe_rejects_wrong_effective_permissions(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            executable = self.make_cli(Path(td))
            with patch.dict(os.environ, {"BAD_POLICY": "1"}):
                report = suite.opencode_workspace_preflight(str(executable))
        self.assertFalse(report["ok"])
        self.assertTrue(all(c["fixture_read_matches"] for c in report["checks"]))
        self.assertTrue(all(not c["permissions_match"] for c in report["checks"]))

    @unittest.skipIf(sys.platform == "win32", "Executable shebang fixture")
    def test_probe_rejects_reordered_wildcard_permissions(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            executable = self.make_cli(Path(td))
            with patch.dict(os.environ, {"BAD_ORDER": "1"}):
                report = suite.opencode_workspace_preflight(str(executable))
        self.assertFalse(report["ok"])
        self.assertTrue(all(not c["permissions_match"] for c in report["checks"]))

    def test_outside_file_read_invalidates_comparison(self):
        from unittest.mock import patch
        event = {"type": "tool_use", "part": {"tool": "read", "callID": "outside",
                 "state": {"input": {"filePath": "/outside-real-checkout/script.py"}, "status": "completed"}}}
        result = {"exit_code": 0, "elapsed_ms": 1, "stdout": json.dumps(event), "stderr": "", "timed_out": False}
        with patch.object(suite, "opencode_run", return_value=result):
            run = suite.one("opencode", "micro", "baseline", 1, None)
        self.assertFalse(run["comparison_valid"])
        self.assertIn("outside_workspace_path", run["comparison_invalid_reasons"])
        self.assertIn("workspace", run)



class ProviderErrorTests(unittest.TestCase):
    ERROR = {"type": "error", "error": {"name": "APIError", "data": {
        "message": "OpenCode's free tier can only be used from within OpenCode.",
        "statusCode": 403, "isRetryable": False,
        "responseHeaders": {"private": "do-not-copy"}}}}

    def test_provider_error_invalidates_run_even_after_tool_calls(self):
        from unittest.mock import patch
        events = [{"type": "tool_use", "part": {"callID": "1", "tool": "read"}}, self.ERROR]
        output = {"stdout": "\n".join(map(json.dumps, events)), "stderr": "",
                  "exit_code": 1, "elapsed_ms": 1, "timed_out": False}
        with patch.object(suite, "opencode_run", return_value=output):
            result = suite.one("opencode", "micro", "baseline", 1, None)
        self.assertFalse(result["comparison_valid"])
        self.assertIn("provider_api_error", result["comparison_invalid_reasons"])
        self.assertEqual(result["provider_errors"][0]["statusCode"], 403)
        self.assertNotIn("do-not-copy", json.dumps(result["provider_errors"]))

    def test_tool_output_and_other_agents_are_not_provider_errors(self):
        self.assertEqual(suite.provider_errors("codex", [self.ERROR]), [])
        self.assertEqual(suite.provider_errors("opencode", [
            {"type": "text", "error": self.ERROR["error"]},
            {"type": "error", "error": {"name": "ToolError", "data": self.ERROR["error"]["data"]}},
            {"type": "error", "error": None}]), [])

    def test_non_retryable_error_saves_report_and_stops_remaining_runs(self):
        from unittest.mock import patch
        import contextlib
        import io
        output = {"stdout": json.dumps(self.ERROR), "stderr": "",
                  "exit_code": 1, "elapsed_ms": 1, "timed_out": False}
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()) as console:
            with patch.object(sys, "argv", ["suite", "--agents", "opencode", "--tasks", "micro"]), patch.object(suite, "preflight", return_value={"ok": True}), patch.object(suite, "FFSAKE_MCP", Path(__file__)), patch.object(suite, "RESULT_DIR", Path(td)), patch.object(suite, "opencode_run", return_value=output) as run:
                with self.assertRaisesRegex(SystemExit, "remaining runs stopped"):
                    suite.main()
            run.assert_called_once()
            report = json.loads(next(Path(td).glob("*.json")).read_text())
        self.assertFalse(report["complete"])
        self.assertEqual(len(report["results"]), 1)
        self.assertEqual(report["stopped_reason"], "non_retryable_provider_error")
        self.assertIn("free tier", console.getvalue())


class ProjectTaskTests(unittest.TestCase):
    STORAGE = '''import json
from pathlib import Path

def load(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else []

def save(path, items):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items))
'''
    CLI = '''import argparse
import json
from .storage import load, save

parser = argparse.ArgumentParser()
parser.add_argument("--db", required=True)
sub = parser.add_subparsers(dest="command", required=True)
create = sub.add_parser("create")
create.add_argument("title")
create.add_argument("--tag", action="append", default=[])
listing = sub.add_parser("list")
listing.add_argument("--status")
listing.add_argument("--tag")
close = sub.add_parser("close")
close.add_argument("id", type=int)
sub.add_parser("stats")
a = parser.parse_args()
items = load(a.db)
if a.command == "create":
    result = dict(id=max((i["id"] for i in items), default=0)+1,
                  title=a.title, tags=a.tag, status="open")
    items.append(result)
    save(a.db, items)
elif a.command == "list":
    result = [i for i in sorted(items, key=lambda i: i["id"])
              if (a.status is None or i["status"] == a.status)
              and (a.tag is None or a.tag in i["tags"])]
elif a.command == "close":
    result = next((i for i in items if i["id"] == a.id), None)
    if result is None:
        parser.error("unknown issue")
    result["status"] = "closed"
    save(a.db, items)
else:
    result = dict(open=sum(i["status"] == "open" for i in items),
                  closed=sum(i["status"] == "closed" for i in items), total=len(items))
print(json.dumps(result))
'''

    def implementation(self, root):
        suite.write_files(root, {"tracker/__init__.py": "",
                                "tracker/storage.py": self.STORAGE,
                                "tracker/__main__.py": self.CLI})

    def test_fixture_starts_without_implementation_and_verifier_requires_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            suite.project_fixture(root)
            self.assertEqual(set(suite.fixture_files(root)), {"README.md"})
            ok, details = suite.project_verify(root, {})
            self.assertFalse(ok)
            self.assertEqual(set(details["missing_files"]), suite.PROJECT_FILES)

    def test_cli_persistence_and_filters_pass_across_processes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            suite.project_fixture(root)
            self.implementation(root)
            before = suite.fixture_files(root)
            ok, details = suite.project_verify(root, {})
            self.assertTrue(ok, details)
            self.assertEqual(details["passed"], 15)
            self.assertEqual(suite.fixture_files(root), before)

    def test_verifier_rejects_broken_persistence_and_filtering(self):
        variants = [
            ("tracker/storage.py", self.STORAGE.replace('p.write_text(json.dumps(items))', 'pass')),
            ("tracker/__main__.py", self.CLI.replace('and (a.tag is None or a.tag in i["tags"])', 'and True')),
            ("tracker/__main__.py", self.CLI.replace('result["status"] = "closed"', 'result["status"] = "open"')),
        ]
        for path, content in variants:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                self.implementation(root)
                (root / path).write_text(content)
                self.assertFalse(suite.project_verify(root, {})[0])

    def test_project_allows_required_creations_but_rejects_extra_files_and_readme_changes(self):
        from unittest.mock import patch
        for forbidden in [None, "extra.py", "README.md"]:
            with self.subTest(forbidden=forbidden):
                def run(root, *args):
                    self.implementation(root)
                    if forbidden:
                        (root / forbidden).write_text("unexpected")
                    return {"stdout": "", "stderr": "", "exit_code": 0,
                            "elapsed_ms": 1, "timed_out": False}
                with patch.object(suite, "codex_run", side_effect=run):
                    result = suite.one("codex", "project", "baseline", 1, None)
                self.assertEqual(result["correct"], forbidden is None)
                self.assertEqual(result["verification"]["unexpected_file_changes"],
                                 [] if forbidden is None else [forbidden])


class MaintenanceTaskTests(unittest.TestCase):
    CONFIG = '''import json
from pathlib import Path


def load_config(path):
    source = Path(path)
    data = json.loads(source.read_text()) if source.exists() else {}
    result = {"request_timeout_ms": data.get("request_timeout_ms", 5000),
              "retries": data.get("retries", 3)}
    for key, minimum in [("request_timeout_ms", 1), ("retries", 0)]:
        if type(result[key]) is not int or result[key] < minimum:
            raise ValueError(key)
    return result
'''

    def implementation(self, root):
        (root / "dispatch/config.py").write_text(self.CONFIG)
        (root / "config/defaults.json").write_text(json.dumps(
            {"request_timeout_ms": 2500, "retries": 2, "region": "eu"}))
        readme = root / "README.md"
        readme.write_bytes(readme.read_bytes().replace(b"Config schema: v1", b"Config schema: v2"))
        (root / "docs/config-v1.md").rename(root / "docs/config-v2.md")
        (root / "examples/request.snapshot.json").write_bytes((root / "examples/request.json").read_bytes())
        (root / "scripts/legacy_config.py").unlink()
        (root / "docs/MIGRATION.md").write_text(
            "# Config v2\nUse request_timeout_ms instead of timeout_ms.\n")

    def test_fixture_is_runnable_but_does_not_satisfy_migration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = suite.maintenance_fixture(root)
            result = suite.run_process([sys.executable, "-B", "-m", "dispatch",
                                        "config/defaults.json"], root, 5)
            self.assertEqual(json.loads(result["stdout"]), {"timeout_ms": 2500, "retries": 2})
            ok, details = suite.maintenance_verify(root, state)
            self.assertFalse(ok)
            self.assertIn("new_key", details["failed_checks"])

    def test_reference_migration_passes_without_verifier_mutations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = suite.maintenance_fixture(root)
            self.implementation(root)
            before = suite.fixture_files(root)
            ok, details = suite.maintenance_verify(root, state)
            self.assertTrue(ok, details)
            self.assertEqual(details["checks"], 26)
            self.assertEqual(suite.fixture_files(root), before)

    def test_verifier_rejects_missing_moves_copies_deletions_and_unrelated_changes(self):
        variants = ["docs/config-v1.md", "scripts/legacy_config.py", "examples/request.snapshot.json",
                    "assets/keep.txt", "dispatch/__main__.py", "extra.txt", "README.md", "docs/MIGRATION.md",
                    "dispatch/__pycache__/unexpected.pyc"]
        for name in variants:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                state = suite.maintenance_fixture(root)
                self.implementation(root)
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("incorrect")
                self.assertFalse(suite.maintenance_verify(root, state)[0])

    def test_verifier_rejects_legacy_key_and_boolean_validation_bugs(self):
        variants = [self.CONFIG.replace('data.get("request_timeout_ms", 5000)', 'data.get("timeout_ms", 5000)'),
                    self.CONFIG.replace('type(result[key]) is not int', 'not isinstance(result[key], int)')]
        for config in variants:
            with self.subTest(config=config), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                state = suite.maintenance_fixture(root)
                self.implementation(root)
                (root / "dispatch/config.py").write_text(config)
                self.assertFalse(suite.maintenance_verify(root, state)[0])

    def test_matrix_allows_only_declared_deletions(self):
        from unittest.mock import patch
        for remove in [None, "assets/keep.txt", "dispatch/config.py"]:
            with self.subTest(remove=remove):
                def run(root, *args):
                    self.implementation(root)
                    if remove:
                        (root / remove).unlink()
                    return {"stdout": "", "stderr": "", "exit_code": 0,
                            "elapsed_ms": 1, "timed_out": False}
                with patch.object(suite, "codex_run", side_effect=run):
                    result = suite.one("codex", "maintenance", "baseline", 1, None)
                self.assertEqual(result["correct"], remove is None, result["verification"])
                self.assertEqual(result["verification"]["unexpected_file_changes"],
                                 [] if remove is None else [remove])


class ClaudePilotTests(unittest.TestCase):
    LAUNCHER = SCRIPT.with_name("ffsake_claude.py")

    def test_dry_run_scopes_config_and_approvals_to_known_workspace_tools(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'repo with "quotes"'
            root.mkdir()
            result = subprocess.run([sys.executable, str(self.LAUNCHER), str(root),
                                     "--binary", sys.executable, "--claude", sys.executable,
                                     "--dry-run"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["mcp_config"], {"mcpServers": {"ffsake": {
                "command": str(Path(sys.executable).resolve()), "args": [str(root.resolve())]}}})
            self.assertEqual(data["approved_tools"],
                             ["mcp__ffsake__" + tool for tool in suite.FFSAKE_TOOLS])
            command = data["command"]
            self.assertEqual(command[command.index("--tools") + 1], "")
            self.assertIn("--strict-mcp-config", command)
            self.assertNotIn("--dangerously-skip-permissions", command)
            self.assertFalse(Path(command[command.index("--mcp-config") + 1]).exists())
            self.assertEqual(list(root.iterdir()), [])

    @unittest.skipIf(sys.platform == "win32", "Executable shebang fixture")
    def test_interactive_launcher_keeps_config_alive_and_preserves_exit_code(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            root = folder / "repo"
            root.mkdir()
            executable = folder / "fake claude"
            executable.write_text("#!" + sys.executable + "\n" + '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
path=Path(args[args.index("--mcp-config")+1])
print(json.dumps({"cwd":os.getcwd(),"pwd":os.environ["PWD"],"config":json.loads(path.read_text()),"config_path":str(path)}))
raise SystemExit(7)
''')
            executable.chmod(0o755)
            env = {**os.environ, "PWD": "/incorrect-parent"}
            result = subprocess.run([sys.executable, str(self.LAUNCHER), str(root),
                                     "--binary", sys.executable, "--claude", str(executable)],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 7, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["cwd"], str(root.resolve()))
            self.assertEqual(data["pwd"], str(root.resolve()))
            self.assertFalse(Path(data["config_path"]).exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_core_dry_run_uses_only_core_config_approvals_and_guidance(self):
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run([sys.executable, str(self.LAUNCHER), td,
                                     "--binary", sys.executable, "--claude", sys.executable,
                                     "--tool-profile", "core", "--dry-run"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["tool_profile"], "core")
            self.assertEqual(data["mcp_config"]["mcpServers"]["ffsake"]["args"],
                             [str(Path(td).resolve()), "--profile", "core"])
            self.assertEqual(data["approved_tools"], ["mcp__ffsake__" + tool for tool in suite.FFSAKE_CORE_TOOLS])
            command = data["command"]
            self.assertEqual(command[command.index("--append-system-prompt") + 1], suite.ffsake_prompt("", "core").strip())
            self.assertEqual(list(Path(td).iterdir()), [])

    @unittest.skipIf(sys.platform == "win32", "Executable shebang fixture")
    def test_core_preflight_checks_custom_binary_before_fake_cli_launch(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            root = folder / "workspace"
            root.mkdir()
            marker = folder / "launched.json"
            cli = folder / "fake-claude"
            cli.write_text("#!" + sys.executable + "\n" +
                "import json,os,sys\nfrom pathlib import Path\n" +
                "args=sys.argv[1:]\nconfig=Path(args[args.index('--mcp-config')+1])\n" +
                f"Path({str(marker)!r}).write_text(json.dumps({{'cwd':os.getcwd(),'pwd':os.environ['PWD'],'config':json.loads(config.read_text()),'config_path':str(config),'args':args}}))\n" +
                "raise SystemExit(7)\n")
            cli.chmod(0o755)
            binary = folder / "fake-mcp"
            for valid in (False, True):
                tools = [{"name": name} for name in suite.FFSAKE_CORE_TOOLS] if valid else []
                response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
                binary.write_text("#!" + sys.executable + "\nimport sys\n" +
                    "assert sys.argv[2:] == ['--profile', 'core']\n" + f"print({json.dumps(response)!r})\n")
                binary.chmod(0o755)
                run = subprocess.run([sys.executable, str(self.LAUNCHER), str(root), "--binary", str(binary),
                                      "--claude", str(cli), "--tool-profile", "core"], capture_output=True, text=True,
                                     env={**os.environ, "PWD": "/wrong"})
                self.assertEqual(run.returncode, 7 if valid else 2, run.stderr)
                if not valid:
                    self.assertIn("mcp_catalog_mismatch", run.stderr)
                    self.assertFalse(marker.exists())
            data = json.loads(marker.read_text())
            self.assertEqual(data["cwd"], str(root.resolve()))
            self.assertEqual(data["pwd"], str(root.resolve()))
            self.assertEqual(data["config"]["mcpServers"]["ffsake"],
                             {"command": str(binary.resolve()), "args": [str(root.resolve()), "--profile", "core"]})
            self.assertEqual(data["args"][data["args"].index("--allowedTools") + 1],
                             ",".join("mcp__ffsake__" + name for name in suite.FFSAKE_CORE_TOOLS))
            self.assertFalse(Path(data["config_path"]).exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_invalid_workspace_or_missing_binary_fails_without_launch(self):
        with tempfile.TemporaryDirectory() as td:
            for workspace, binary in [(str(Path(td) / "missing"), sys.executable),
                                       (td, str(Path(td) / "missing-binary"))]:
                result = subprocess.run([sys.executable, str(self.LAUNCHER), workspace,
                                         "--binary", binary, "--claude", sys.executable],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")




class ToolProfileTests(unittest.TestCase):
    def test_core_guidance_omits_disabled_tools_and_default_stays_full(self):
        self.assertEqual(suite.ffsake_prompt("task"), suite.ffsake_prompt("task", "all"))
        core = suite.ffsake_prompt("task", "core")
        self.assertIn("ffsake_read_files", core)
        self.assertIn("ffsake_preview_files", core)
        for tool in set(suite.FFSAKE_TOOLS) - set(suite.FFSAKE_CORE_TOOLS):
            self.assertNotIn(tool, core)
        self.assertLess(len(core), len(suite.ffsake_prompt("task")))
        with self.assertRaises(ValueError):
            suite.ffsake_prompt("task", "unknown")

    def test_core_codex_config_scopes_catalog_and_approval_to_core(self):
        import tomllib
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, patch.object(suite, "run_process", return_value={}) as run:
            root = Path(td)
            suite.codex_run(root, "task", True, 1, "core")
            server = tomllib.loads((root / ".codex/config.toml").read_text())["mcp_servers"]["ffsake"]
            self.assertEqual(server["args"], [str(root), "--profile", "core"])
            self.assertEqual(set(server["enabled_tools"]), set(suite.FFSAKE_CORE_TOOLS))
            self.assertEqual(server["tools"], {name: {"approval_mode": "approve"} for name in suite.FFSAKE_CORE_TOOLS})
            self.assertEqual(run.call_args.args[0][-1], suite.ffsake_prompt("task", "core"))

    def test_claude_and_opencode_core_launch_configuration(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, patch.object(suite, "run_process", return_value={}) as run:
            root = Path(td)
            suite.claude_run(root, "task", True, 1, "core")
            server = json.loads((root / ".ffsake-claude-mcp.json").read_text())["mcpServers"]["ffsake"]
            self.assertEqual(server["args"], [str(root), "--profile", "core"])
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("-p") + 1], suite.ffsake_prompt("task", "core"))
            self.assertEqual(command[command.index("--tools") + 1], "")
            config = suite.configure_opencode(root, True, "core")
            self.assertEqual(config["mcp"]["ffsake"]["command"], [str(suite.FFSAKE_MCP), str(root), "--profile", "core"])
            self.assertEqual(suite.configure_opencode(root, False, "core"), suite.configure_opencode(root, False))

    def test_profile_does_not_change_baseline_commands(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, patch.object(suite, "run_process", return_value={}) as run:
            for runner in (suite.claude_run, suite.codex_run):
                runner(Path(td), "task", False, 1)
                default = run.call_args.args[0]
                runner(Path(td), "task", False, 1, "core")
                self.assertEqual(run.call_args.args[0], default)

    def test_core_preflight_reads_only_catalog_and_rejects_mismatch(self):
        from unittest.mock import patch
        from subprocess import CompletedProcess
        response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": name} for name in suite.FFSAKE_CORE_TOOLS]}}
        with patch.object(suite, "FFSAKE_MCP", Path(__file__)), patch.object(suite.subprocess, "run",
                return_value=CompletedProcess([], 0, json.dumps(response), "")) as run:
            checked = suite.mcp_profile_preflight("core")
            self.assertTrue(checked["ok"])
            self.assertEqual(checked["tool_count"], len(suite.FFSAKE_CORE_TOOLS))
            self.assertEqual(checked["model_calls"], 0)
            self.assertEqual(json.loads(run.call_args.kwargs["input"])["method"], "tools/list")
            self.assertEqual(run.call_args.args[0][-2:], ["--profile", "core"])
            response["result"]["tools"].append({"name": "ffsake_rust_hover"})
            run.return_value = CompletedProcess([], 0, json.dumps(response), "")
            self.assertEqual(suite.mcp_profile_preflight("core")["error"], "mcp_catalog_mismatch")
            run.return_value = CompletedProcess([], 2, "", "usage")
            self.assertEqual(suite.mcp_profile_preflight("core")["error"], "mcp_launch_failed")

    def test_core_trace_audit_recognizes_aliases_and_disabled_attempts(self):
        names = ["mcp__ffsake__read", "mcp__ffsake__ffsake_read", "ffsake_ffsake_read", "ffsake.read"]
        self.assertEqual(suite.permission_audit("codex", True, {"tool_names": names}, "core")["observed_profile_violations"], [])
        names.append("mcp__ffsake__ffsake_rust_hover")
        audit = suite.permission_audit("codex", True, {"tool_names": names}, "core")
        self.assertEqual(audit["observed_profile_violations"], [names[-1]])
        self.assertEqual(audit["trace_policy_status"], "violation")
        self.assertFalse(audit["permission_isolation_verified"])

    def test_results_record_profile_and_invalidate_disabled_attempts(self):
        from unittest.mock import patch
        event = {"type": "item.completed", "item": {"id": "1", "type": "mcp_tool_call",
                 "server": "ffsake", "tool": "ffsake_rust_hover"}}
        output = {"stdout": json.dumps(event), "stderr": "", "exit_code": 0, "elapsed_ms": 1, "timed_out": False}
        with patch.object(suite, "codex_run", return_value=output):
            result = suite.one("codex", "micro", "ffsake", 1, None, "core")
        self.assertEqual(result["ffsake_tool_profile"], "core")
        self.assertIn("tool_profile_violation", result["comparison_invalid_reasons"])
        self.assertFalse(result["comparison_valid"])

    def test_aggregate_keeps_profiles_separate_and_defaults_legacy_to_all(self):
        result = {"agent": "codex", "task": "micro", "mode": "ffsake", "comparison_valid": True,
                  "correct": True, "elapsed_ms": 100, "exit_code": 0, "timed_out": False,
                  "trace": {"tool_calls": 1, "ffsake_tool_calls": 1, "shell_tool_calls": 0,
                            "output_tokens": 1, "total_cost_usd": None}}
        rows = suite.aggregate([result, {**result, "ffsake_tool_profile": "core", "elapsed_ms": 200}])
        self.assertEqual({row["ffsake_tool_profile"]: row["median_elapsed_ms"] for row in rows}, {"all": 100, "core": 200})

    def test_core_cli_forwards_profile_and_saves_it_before_model_runs(self):
        import contextlib
        import io
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            with patch.object(sys, "argv", ["suite", "--agents", "codex", "--tool-profile", "core", "--preflight-only"]), \
                 patch.object(suite, "preflight", return_value={"ok": True, "model_calls": 0}) as check, \
                 patch.object(suite, "RESULT_DIR", Path(td)), patch.object(suite, "one") as run:
                suite.main()
            check.assert_called_once_with(["codex"], "core")
            run.assert_not_called()
            report = json.loads(next(Path(td).glob("*.json")).read_text())
            self.assertEqual(report["ffsake_tool_profile"], "core")
            self.assertFalse(report["complete"])

    def test_core_run_records_profile_in_report_and_aggregate(self):
        import contextlib
        import io
        from unittest.mock import patch
        result = {"agent": "codex", "task": "micro", "mode": "ffsake", "ffsake_tool_profile": "core",
                  "comparison_valid": True, "correct": True, "exit_code": 0, "timed_out": False, "elapsed_ms": 1,
                  "trace": {"tool_calls": 1, "ffsake_tool_calls": 1, "shell_tool_calls": 0,
                            "output_tokens": 1, "total_cost_usd": None}}
        arguments = ["suite", "--agents", "codex", "--tasks", "micro", "--modes", "ffsake",
                     "--tool-profile", "core", "--repetitions", "1"]
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            with patch.object(sys, "argv", arguments), patch.object(suite, "preflight", return_value={"ok": True}), \
                 patch.object(suite, "FFSAKE_MCP", Path(__file__)), patch.object(suite, "RESULT_DIR", Path(td)), \
                 patch.object(suite, "one", return_value=result) as run:
                suite.main()
            run.assert_called_once_with("codex", "micro", "ffsake", 900, None, "core")
            report = json.loads(next(Path(td).glob("*.json")).read_text())
            self.assertEqual(report["schema_version"], 4)
            self.assertEqual(report["ffsake_tool_profile"], "core")
            self.assertEqual(report["aggregate"][0]["ffsake_tool_profile"], "core")



class ProvenanceTests(unittest.TestCase):
    def test_fixture_identity_is_root_independent_but_tracks_bytes_paths_modes_and_prompt(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            roots = [Path(first), Path(second)]
            for root in roots:
                (root / "a").write_bytes(b"hello\r\n")
                (root / "a").chmod(0o644)
            def identity(root, prompt="task"):
                return suite.task_provenance(root, suite.fixture_files(root), prompt)
            original = identity(roots[0])
            self.assertEqual(original, identity(roots[1]))
            self.assertNotEqual(original["prompt_sha256"], identity(roots[0], "changed")["prompt_sha256"])
            (roots[1] / "a").chmod(0o755)
            self.assertNotEqual(original["fixture_manifest_sha256"], identity(roots[1])["fixture_manifest_sha256"])
            (roots[1] / "a").chmod(0o644)
            (roots[1] / "a").write_bytes(b"hello\n")
            self.assertNotEqual(original["fixture_manifest_sha256"], identity(roots[1])["fixture_manifest_sha256"])
            (roots[1] / "a").write_bytes(b"hello\r\n")
            (roots[1] / "a").rename(roots[1] / "b")
            self.assertNotEqual(original["fixture_manifest_sha256"], identity(roots[1])["fixture_manifest_sha256"])

    def test_symlink_identity_does_not_follow_external_contents(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
            root = Path(td)
            target = Path(outside) / "secret"
            target.write_text("private contents")
            (root / "link").symlink_to(target)
            original = suite.task_provenance(root, suite.fixture_files(root), "task")
            target.write_text("different private contents")
            self.assertEqual(original, suite.task_provenance(root, suite.fixture_files(root), "task"))
            self.assertEqual(original["fixture_files"][0]["kind"], "symlink")
            self.assertNotIn(str(target), json.dumps(original))

    def test_binary_and_harness_hashes_distinguish_local_changes_without_claiming_build_identity(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "scripts").mkdir()
            for name in ("agent_benchmark_suite.py", "agent_benchmark.py"):
                (root / "scripts" / name).write_text(name)
            binary = root / "binary"
            binary.write_bytes(b"first binary")
            with patch.object(suite, "ROOT", root), patch.object(suite, "FFSAKE_MCP", binary):
                original = suite.benchmark_provenance()
                self.assertIsNone(original["source_revision"])
                self.assertFalse(original["binary_matches_source_verified"])
                binary.write_bytes(b"second binary")
                (root / "scripts/agent_benchmark.py").write_text("local edit")
                changed = suite.benchmark_provenance()
                self.assertNotEqual(original["mcp_binary_sha256"], changed["mcp_binary_sha256"])
                self.assertNotEqual(original["harness_sha256"], changed["harness_sha256"])
                binary.unlink()
                self.assertIsNone(suite.benchmark_provenance()["mcp_binary_sha256"])

    def test_result_identity_precedes_runner_mutation_and_uses_profile_prompt(self):
        import hashlib
        from unittest.mock import patch
        def fixture(root):
            (root / "a").write_text("before")
        def runner(root, *args):
            (root / "a").write_text("after")
            return {"stdout": "", "stderr": "", "exit_code": 0, "elapsed_ms": 1, "timed_out": False}
        task = {"fixture": fixture, "prompt": "do task", "verify": lambda *_: (True, {}), "allowed_changes": {"a"}}
        with patch.dict(suite.TASKS, {"provenance": task}), patch.object(suite, "codex_run", side_effect=runner):
            result = suite.one("codex", "provenance", "ffsake", 1, None, "core")
        identity = result["provenance"]
        self.assertEqual(identity["fixture_files"][0]["sha256"], hashlib.sha256(b"before").hexdigest())
        self.assertEqual(identity["prompt_sha256"], hashlib.sha256(suite.ffsake_prompt("do task", "core").encode()).hexdigest())



class LayoutTaskTests(unittest.TestCase):
    @staticmethod
    def implement(root):
        import shutil
        (root / "service_old").rename(root / "service_next")
        shutil.copytree(root / "templates", root / "templates_snapshot")
        shutil.rmtree(root / "retired")
        (root / "reports").mkdir()
        path = root / "README.md"
        path.write_bytes(path.read_bytes().replace(b"python3 -B -m service_old", b"python3 -B -m service_next"))

    def test_reference_layout_passes_and_verification_preserves_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = suite.layout_fixture(root)
            self.assertFalse(suite.layout_verify(root, state)[0])
            self.implement(root)
            before = suite.fixture_files(root)
            ok, details = suite.layout_verify(root, state)
            self.assertTrue(ok, details)
            self.assertEqual(suite.fixture_files(root), before)
            task = suite.TASKS["layout"]
            self.assertEqual(set(state["original"]) - set(before), task["allowed_deletions"])
            self.assertLessEqual(set(before) - set(state["original"]), task["allowed_changes"])

    def test_layout_rejects_binary_directory_permission_and_runtime_damage(self):
        import os
        for variant in ["binary", "empty", "extra", "runtime", "sentinel"] + (["mode"] if os.name == "posix" else []):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                state = suite.layout_fixture(root)
                self.implement(root)
                if variant == "binary":
                    (root / "templates_snapshot/sample.bin").write_bytes(b"changed")
                elif variant == "empty":
                    (root / "templates_snapshot/empty").rmdir()
                elif variant == "extra":
                    (root / "unexpected-empty").mkdir()
                elif variant == "runtime":
                    (root / "service_next/__main__.py").write_text("raise RuntimeError('broken')")
                elif variant == "sentinel":
                    (root / "assets/keep.bin").write_bytes(b"changed")
                else:
                    (root / "templates_snapshot/start.sh").chmod(0o644)
                self.assertFalse(suite.layout_verify(root, state)[0])

    def test_layout_checks_empty_directories_after_runtime_verification(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = suite.layout_fixture(root)
            self.implement(root)
            def changed(*args, **kwargs):
                (root / "runtime-side-effect").mkdir()
                return {"timed_out": False, "exit_code": 0, "stdout": '{"message":"café","retries":2}'}
            with patch.object(suite, "run_process", side_effect=changed):
                ok, details = suite.layout_verify(root, state)
            self.assertFalse(ok)
            self.assertIn("exact_directories", details["failed_checks"])


if __name__ == "__main__":
    unittest.main()
