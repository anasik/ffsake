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
        self.assertEqual(suite.decode_output(b"ok\\xff"), "ok�")
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

    def test_timeout_returns_json_safe_text(self):
        with tempfile.TemporaryDirectory() as td:
            command = [sys.executable, "-c", "import sys,time;sys.stdout.buffer.write(b'\\xff');sys.stdout.flush();time.sleep(5)"]
            result = suite.run_process(command, Path(td), timeout=1)
            self.assertTrue(result["timed_out"])
            self.assertIsInstance(result["stdout"], str)
            self.assertIsInstance(result["stderr"], str)
            json.dumps(result)


if __name__ == "__main__":
    unittest.main()
