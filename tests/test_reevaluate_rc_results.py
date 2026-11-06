import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import reevaluate_rc_results as reevaluate


def result(unexpected, verification_changes=None, exits=(0, 0), passed=True):
    return {
        "task": "edit_units",
        "workflow_evidence": {"passed": passed},
        "verification": {
            "program_exit": exits[0],
            "test_exit": exits[1],
            "stdout": "actual=[1]\nexpected=[1]\n",
            "unexpected_file_changes": unexpected,
            "verification_file_changes": verification_changes or [],
            "file_audit_complete": True,
        },
    }


class ReevaluateTests(unittest.TestCase):
    def test_bytecode_only_changes_are_transient(self):
        item = result(["tests/__pycache__/x.cpython-314.pyc"])
        reevaluate.recompute(item)
        self.assertTrue(item["artifact_correct"])
        self.assertTrue(item["correct"])

    def test_real_source_change_still_fails(self):
        item = result(["README.md"])
        reevaluate.recompute(item)
        self.assertFalse(item["artifact_correct"])
        self.assertFalse(item["correct"])

    def test_failed_exit_still_fails(self):
        item = result([], exits=(1, 0))
        reevaluate.recompute(item)
        self.assertFalse(item["artifact_correct"])

    def test_workflow_evidence_still_gates_correctness(self):
        item = result([], passed=False)
        reevaluate.recompute(item)
        self.assertTrue(item["artifact_correct"])
        self.assertFalse(item["correct"])


if __name__ == "__main__":
    unittest.main()
