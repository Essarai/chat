from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace

from app.services.orchestrator import _trace_ask_inputs
from eval.run_core_user_tasks import _contract_only
from eval.run_langsmith_eval import (
    answer_relevance_precision,
    build_examples,
    contract_hard_gate,
    evidence_accuracy,
    load_specs,
    required_goal_coverage,
    sync_dataset,
)


class LangSmithIntegrationTests(unittest.TestCase):
    def test_trace_inputs_exclude_history_content(self):
        traced = _trace_ask_inputs(
            {
                "question": "近五年热点",
                "top_k": 5,
                "history": [
                    {"role": "user", "content": "private"},
                    {"role": "assistant", "content": "private"},
                ],
                "previous_turn": {"turn_id": "turn-1", "answer": "private"},
            }
        )
        self.assertEqual(traced["history_turns"], 1)
        self.assertEqual(traced["previous_turn_id"], "turn-1")
        self.assertNotIn("private", str(traced))

    def test_dataset_and_evaluators_reuse_core_contract(self):
        specs = load_specs()
        examples = build_examples("test-core", specs)
        self.assertEqual(len(examples), 10)
        self.assertEqual(len({row["id"] for row in examples}), 10)

        state, answer = _contract_only(specs[0])
        outputs = {"state": state, "answer": answer}
        reference = {"contract": specs[0]}
        self.assertEqual(contract_hard_gate(outputs, reference)["score"], 1)
        self.assertGreaterEqual(answer_relevance_precision(outputs, reference)["score"], 0.9)
        self.assertGreaterEqual(required_goal_coverage(outputs, reference)["score"], 0.9)
        self.assertEqual(evidence_accuracy(outputs, reference)["score"], 1.0)

    def test_dataset_sync_updates_existing_and_creates_missing(self):
        specs = load_specs()[:2]
        examples = build_examples("test-sync", specs)

        class FakeClient:
            dataset = SimpleNamespace(id=uuid.uuid4(), name="test-sync")
            updated = []
            created = []

            def has_dataset(self, **_kwargs):
                return True

            def read_dataset(self, **_kwargs):
                return self.dataset

            def list_examples(self, **_kwargs):
                return [SimpleNamespace(id=examples[0]["id"])]

            def update_examples(self, **kwargs):
                self.updated = kwargs["updates"]

            def create_examples(self, **kwargs):
                self.created = kwargs["examples"]

        client = FakeClient()
        sync_dataset(client, "test-sync", specs)  # type: ignore[arg-type]
        self.assertEqual([row["id"] for row in client.updated], [examples[0]["id"]])
        self.assertEqual([row["id"] for row in client.created], [examples[1]["id"]])


if __name__ == "__main__":
    unittest.main()
