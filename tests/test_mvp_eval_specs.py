from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "eval" / "mvp_eval_specs_v1.json"
RUNNER = ROOT / "eval" / "run_mvp_eval.py"


class MVPEvalSpecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(SPEC.read_text(encoding="utf-8"))
        cls.cases = cls.payload["cases"]

    def test_balanced_roles_and_unique_ids(self):
        self.assertEqual(len(self.cases), 18)
        self.assertEqual(len({case["id"] for case in self.cases}), 18)
        self.assertEqual(
            {role: sum(case["role"] == role for case in self.cases) for role in ("author", "editor")},
            {"author": 9, "editor": 9},
        )

    def test_each_role_covers_a1_through_a8(self):
        expected = {f"A{index}" for index in range(1, 9)}
        for role in ("author", "editor"):
            actual = {
                capability
                for case in self.cases
                if case["role"] == role
                for capability in case["capabilities"]
            }
            self.assertEqual(actual, expected, role)

    def test_all_five_execution_statuses_are_present(self):
        self.assertEqual(
            {case["expected_status"] for case in self.cases},
            {"EXECUTE", "CLARIFY", "PARTIAL", "REJECT", "ASSUME"},
        )

    def test_runner_dry_run_validates_dataset(self):
        completed = subprocess.run(
            [sys.executable, str(RUNNER), "--dry-run"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        report = json.loads(completed.stdout)
        self.assertTrue(report["valid"])
        self.assertEqual(report["summary"]["total_cases"], 18)


if __name__ == "__main__":
    unittest.main()
