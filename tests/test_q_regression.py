from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.agents.controller import init_state
from app.agents.coverage import assess_answer_coverage, assess_operations, assess_quality
from app.agents.generate import ensure_answer_operation_coverage, try_author_template_answer, try_operation_plan_answer
from app.capabilities.sql_capability import execute_plan
from app.config import get_corpus_settings
from app.services.sqlite_repo import SQLiteRepo


ROOT = Path(__file__).resolve().parents[1]


def load_questions() -> list[str]:
    questions = []
    for line in (ROOT / "Q.md").read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("##"):
            if questions and value.startswith("##"):
                break
            continue
        questions.append(value)
    return questions


class QRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = load_questions()
        cls.specs = json.loads((ROOT / "eval" / "q_specs.json").read_text(encoding="utf-8"))
        cls.repo = SQLiteRepo(get_corpus_settings("ZDXBNXB"))

    def test_q_md_answers_remain_relevant(self):
        self.assertEqual(len(self.questions), 20)
        self.assertEqual(len(self.specs), 20)
        for question, spec in zip(self.questions, self.specs):
            with self.subTest(case=spec["id"]):
                state = init_state(question, journal_id="ZDXBNXB")
                plan = state["query_plan"]
                self.assertTrue(plan.get("locked"), plan)
                if spec.get("task"):
                    self.assertEqual(plan.get("task"), spec["task"])
                operations = [row.get("type") for row in plan.get("operations") or []]
                for required in spec.get("required_operations") or []:
                    self.assertIn(required, operations)
                for author in spec.get("forbidden_authors") or []:
                    self.assertNotEqual(plan.get("author_name"), author)
                for topic in spec.get("topics") or []:
                    self.assertIn(topic, plan.get("keywords") or [])
                for key in ("top_n", "max_items"):
                    if key in spec:
                        self.assertEqual(plan.get(key), spec[key])

                state["intents"] = ["sql"]
                state["sql_evidence"] = execute_plan(plan, question, state["entities"], self.repo)
                state.update(assess_operations(state))  # type: ignore[arg-type]
                allowed = set(spec.get("allowed_statuses") or ["complete"])
                self.assertTrue(
                    all(row.get("status") in allowed for row in state["operation_results"]),
                    state["operation_results"],
                )
                answer = (
                    try_operation_plan_answer(state)
                    or try_author_template_answer(state)
                    or ensure_answer_operation_coverage("", state)
                )
                self.assertTrue(answer.strip())
                for token in spec.get("answer_all") or []:
                    self.assertIn(token, answer)
                for token in spec.get("answer_none") or []:
                    self.assertNotIn(token, answer)
                self.assertEqual(
                    assess_answer_coverage(answer, state["operation_results"])["coverage"],
                    1.0,
                )
                self.assertTrue(assess_quality(answer, state)["hard_gate_passed"])


if __name__ == "__main__":
    unittest.main()
