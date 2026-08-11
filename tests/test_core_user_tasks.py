from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.agents.controller import init_state
from app.agents.coverage import assess_answer_coverage, assess_operations, assess_quality
from app.agents.generate import ensure_answer_operation_coverage, try_operation_plan_answer
from app.agents.eval_contracts import evaluate_core_contract
from app.agents.operation_contracts import operation_types
from app.capabilities.sql_capability import execute_plan
from app.config import get_corpus_settings
from app.services.sqlite_repo import SQLiteRepo


ROOT = Path(__file__).resolve().parents[1]


class CoreUserTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs = json.loads(
            (ROOT / "eval" / "core_user_tasks_specs.json").read_text(encoding="utf-8")
        )
        cls.repos = {
            journal_id: SQLiteRepo(get_corpus_settings(journal_id))
            for journal_id in {spec.get("journal_id", "ZDXBNXB") for spec in cls.specs}
        }

    def test_all_core_task_contracts(self):
        for spec in self.specs:
            with self.subTest(task=spec["id"]):
                question = spec["example_question"]
                state = init_state(question, journal_id=spec["journal_id"])
                plan = state["query_plan"]
                draft = state["entities"]
                self.assertTrue(plan.get("locked"), plan)
                self.assertEqual(
                    (state.get("turn_intent") or {}).get("source"),
                    "deterministic-core",
                )
                for key, expected in spec.get("expected_intent", {}).items():
                    self.assertEqual((state.get("turn_intent") or {}).get(key), expected)
                actual_operations = operation_types(plan)
                for required in spec["required_operations"]:
                    self.assertIn(required, actual_operations)
                self.assertFalse(
                    set(plan.get("keywords") or [])
                    & set(spec.get("forbidden_literal_topics") or [])
                )

                sql = execute_plan(
                    plan,
                    question,
                    draft,
                    self.repos[spec.get("journal_id", "ZDXBNXB")],
                )
                state = {
                    "query_plan": plan,
                    "sql_evidence": sql,
                    "entities": draft,
                    "intents": ["sql"],
                    "turn_intent": state["turn_intent"],
                }
                state.update(assess_operations(state))  # type: ignore[arg-type]
                results = state["operation_results"]
                self.assertEqual(len(results), len(actual_operations))
                self.assertTrue(all(row.get("status") for row in results))
                self.assertFalse(any(row["status"] in {"unsupported", "error"} for row in results))
                self.assertGreaterEqual(
                    state["coverage_report"]["coverage"],
                    float(spec.get("minimum_operation_coverage", 1.0)),
                )

                answer = try_operation_plan_answer(state)  # type: ignore[arg-type]
                if not answer:
                    answer = ensure_answer_operation_coverage("", state)  # type: ignore[arg-type]
                report = assess_answer_coverage(answer or "", results)
                self.assertEqual(report["coverage"], 1.0, report)
                quality = assess_quality(answer or "", state)  # type: ignore[arg-type]
                self.assertGreaterEqual(quality["answer_relevance_precision"], 0.9)
                self.assertGreaterEqual(
                    quality["required_goal_coverage"],
                    float(spec.get("minimum_operation_coverage", 1.0)),
                )
                self.assertEqual(quality["evidence_accuracy"], 1.0)
                self.assertTrue(quality["hard_gate_passed"], quality)
                contract = evaluate_core_contract(spec, state, answer or "")
                self.assertTrue(contract["passed"], contract["failures"])


if __name__ == "__main__":
    unittest.main()
