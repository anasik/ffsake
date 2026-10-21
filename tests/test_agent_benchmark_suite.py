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


if __name__ == "__main__":
    unittest.main()
