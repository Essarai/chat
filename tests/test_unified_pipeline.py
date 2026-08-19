from __future__ import annotations

import json
import unittest

from app.config import get_corpus_settings
from app.pipeline.compiler import PlanCompiler
from app.pipeline.context import ContextResolver
from app.pipeline.production import UnifiedProductionPipeline
from app.pipeline.semantic import SemanticRouter
from app.pipeline.validator import PlanValidator
from app.services.production_orchestrator import ProductionOrchestrator


FIELD_OPERATIONS = [
    "yearly_counts",
    "top_keywords",
    "keyword_growth",
    "topic_period_compare",
    "representative_papers_by_topic",
]


class FakeChat:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class StaticRouter:
    def __init__(self, intent):
        self.intent = intent

    def route(self, *args, **kwargs):
        return dict(self.intent)


def field_intent():
    return {
        "entity": "journal",
        "operation": "compare",
        "goal": "research_analysis",
        "metric": "keyword_freq",
        "topics": [],
        "author_name": None,
        "author_name_b": None,
        "institution": None,
        "time_range": {"start": 2017, "end": 2026, "last_n": 10},
        "top_n": 5,
        "requested_operations": list(FIELD_OPERATIONS),
        "confidence": 0.98,
        "clarification": None,
        "source": "llm",
    }


class UnifiedPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = get_corpus_settings("ZDXBNXB")

    def test_semantic_router_retries_schema_once(self):
        good = json.dumps(field_intent(), ensure_ascii=False)
        chat = FakeChat(["not-json", good])
        intent = SemanticRouter(chat).route("近十年本刊研究领域如何变化")
        self.assertEqual(chat.calls, 2)
        self.assertEqual(intent["requested_operations"], FIELD_OPERATIONS)
        self.assertEqual(intent["topics"], [])

    def test_semantic_router_failure_terminates_as_clarification(self):
        chat = FakeChat([RuntimeError("offline"), RuntimeError("offline")])
        intent = SemanticRouter(chat).route("帮我分析一下")
        self.assertEqual(intent["source"], "fallback")
        self.assertEqual(intent["requested_operations"], ["clarification"])
        self.assertEqual(intent["action"], "clarify")

    def test_compiler_and_validator_keep_one_canonical_plan(self):
        compiler = PlanCompiler()
        plan = compiler.compile(field_intent())
        report = PlanValidator(compiler).validate(plan)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual([op["type"] for op in plan["operations"]], FIELD_OPERATIONS)
        self.assertEqual(plan["sources"], ["sql"])
        self.assertNotIn("locked", plan)
        self.assertNotIn("plan_source", plan)

    def test_compiler_fills_semantic_goal_coverage_without_reading_question(self):
        intent = {
            **field_intent(),
            "requested_operations": ["yearly_counts"],
        }
        plan = PlanCompiler().compile(intent)
        self.assertEqual([op["type"] for op in plan["operations"]], FIELD_OPERATIONS)

    def test_last_n_is_compiled_to_concrete_year_window(self):
        intent = {
            **field_intent(),
            "time_range": {"start": None, "end": 2026, "last_n": 10},
        }
        plan = PlanCompiler().compile(intent)
        self.assertEqual((plan["year_start"], plan["year_end"]), (2017, 2026))

    def test_submission_without_topic_clarifies_instead_of_field_repair(self):
        intent = {
            **field_intent(),
            "entity": "topic",
            "operation": "coverage",
            "goal": "submission_fit",
            "metric": "coverage",
            "requested_operations": ["submission_fit", "submission_guidance"],
        }
        compiler = PlanCompiler()
        plan, report = PlanValidator(compiler).validate_and_repair(
            compiler.compile(intent), intent
        )
        self.assertEqual([op["type"] for op in plan["operations"]], ["clarification"])
        self.assertTrue(report["repaired"])

    def test_context_author_selection_uses_persisted_ids(self):
        previous = {
            "turn_id": "turn-1",
            "query_plan": {"year_start": 2020, "year_end": 2025},
            "result_set": {
                "items": [
                    {"type": "author", "id": "a1", "name": "作者甲"},
                    {"type": "author", "id": "a2", "name": "作者乙"},
                ],
                "constraints": {"year_start": 2020, "year_end": 2025, "keywords": []},
            },
            "continuation": {"has_more": False},
        }
        intent = ContextResolver().resolve("列出第2位作者的论文", previous)
        self.assertIsNotNone(intent)
        self.assertEqual(intent["author_ids"], ["a2"])
        plan = PlanCompiler().compile(
            UnifiedProductionPipeline._normalize_context_intent(intent),
            previous,
        )
        self.assertEqual(plan["year_start"], 2020)
        self.assertEqual(plan["year_end"], 2025)
        self.assertEqual(plan["author_ids"], ["a2"])

    def test_real_sql_field_flow_has_per_operation_evidence_and_quality_gate(self):
        pipeline = UnifiedProductionPipeline(
            self.settings,
            semantic_router=StaticRouter(field_intent()),
        )
        state = pipeline.prepare("近十年本刊接受论文的领域变化")
        self.assertEqual(
            [row["operation"] for row in state["operation_results"]],
            FIELD_OPERATIONS,
        )
        self.assertEqual(len(state["operation_evidence"]), len(FIELD_OPERATIONS))
        self.assertEqual(state["coverage_report"]["coverage"], 1.0)
        answer = pipeline.generate(state)
        self.assertIn("领域变化", answer)
        self.assertTrue(state["quality_report"]["hard_gate_passed"])

    def test_stream_emits_only_gated_final_answer(self):
        pipeline = UnifiedProductionPipeline(
            self.settings,
            semantic_router=StaticRouter(field_intent()),
        )
        orchestrator = ProductionOrchestrator(self.settings, pipeline=pipeline)
        events = list(orchestrator.ask_stream("近十年本刊接受论文的领域变化"))
        visible = "".join(
            event.get("text") or "" for event in events if event["type"] == "delta"
        )
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(visible, done["answer"])
        self.assertTrue(done["evidence"]["quality_report"]["hard_gate_passed"])
        self.assertEqual(
            done["evidence"]["pipeline"]["version"],
            "unified-pipeline/v1",
        )


if __name__ == "__main__":
    unittest.main()
