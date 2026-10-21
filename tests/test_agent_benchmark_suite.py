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


if __name__ == "__main__":
    unittest.main()
