#!/usr/bin/env python3
"""Re-evaluate retained rc_benchmark results after a verifier correction.

Only transient build artifacts (bytecode and tool caches) are filtered, and
task verdicts are recomputed from the recorded program/test exits plus the
recorded stdout. No model calls are made.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_benchmark_suite import transient_artifact  # noqa: E402


def filtered(values):
    return [value for value in values or [] if not transient_artifact(value)]


def verifier_passed(task, verification):
    if verification.get("skipped") or verification.get("exception"):
        return False
    if verification.get("program_exit") != 0 or verification.get("test_exit") != 0:
        return False
    stdout = verification.get("stdout") or ""
    actual = [line for line in stdout.splitlines() if line.startswith("actual=")]
    expected = [line for line in stdout.splitlines() if line.startswith("expected=")]
    if actual and expected and actual[0].partition("=")[2] != expected[0].partition("=")[2]:
        return False
    return True


def recompute(result):
    verification = result.get("verification") or {}
    unexpected = filtered(verification.get("unexpected_file_changes"))
    verification_changes = filtered(verification.get("verification_file_changes"))
    verification["unexpected_file_changes"] = unexpected
    verification["verification_file_changes"] = verification_changes
    passed = (verifier_passed(result.get("task", ""), verification)
              and not unexpected
              and not verification_changes
              and verification.get("file_audit_complete") is not False)
    evidence = result.get("workflow_evidence") or {}
    result["artifact_correct"] = passed
    result["correct"] = bool(passed and evidence.get("passed", True))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = json.loads(Path(args.input).read_text())
    before = sum(1 for item in report["results"] if item.get("correct"))
    for item in report["results"]:
        recompute(item)
    after = sum(1 for item in report["results"] if item.get("correct"))
    Path(args.output).write_text(json.dumps(report, separators=(",", ":")))
    print(json.dumps({
        "input": args.input,
        "output": args.output,
        "attempts": len(report["results"]),
        "correct_before": before,
        "correct_after": after,
        "model_calls": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
