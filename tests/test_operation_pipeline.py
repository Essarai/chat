from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agents.capability_planner import plan_from_intent
from app.agents.coverage import assess_operations
from app.agents.operation_contracts import normalize_query_plan, operation_types
from app.agents.query_understand import query_understand_node
from app.agents.router_v2 import extract_node, router_node
from app.capabilities.sql_capability import execute_plan
from app.config import get_corpus_settings
from app.services.sqlite_repo import SQLiteRepo


class OperationPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = SQLiteRepo(get_corpus_settings("ZDXBNXB"))

    def test_meta_keyword_is_not_literal_topic(self):
        plan = normalize_query_plan(
            {
                "task": "topic_evolution",
                "sql_ops": ["topic_keyword_counts", "topic_yearly"],
                "keywords": ["热门关键词"],
                "year_start": 2017,
                "year_end": 2026,
                "sources": ["sql"],
            },
            "近十年热门关键词有哪些？",
        )
        self.assertEqual(plan["keywords"], [])
        self.assertEqual(operation_types(plan), ["top_keywords", "keyword_growth"])

    def test_unlocked_question_uses_one_understanding_call(self):
        state = {"question": "介绍一下本刊研究特色", "entities": {}, "query_plan": {}}
        extracted = extract_node(state)  # type: ignore[arg-type]
        state.update(extracted)
        with patch(
            "app.agents.query_understand.llm_fill_intent",
            return_value={
                "entity": "journal",
                "operation": "summarize",
                "requested_operations": [{"type": "journal_overview", "required": True}],
                "goal": "research_analysis",
                "sources": ["sql"],
                "topic": [],
                "confidence": 0.9,
            },
        ) as understand:
            query_understand_node(state)  # type: ignore[arg-type]
        understand.assert_called_once()
        self.assertFalse(state["entities"]["extract_meta"]["llm_confirmed"])

    def test_invalid_locked_rule_candidate_falls_back_to_llm(self):
        state = {
            "question": "帮我分析一下刊物近年来关注内容的转向",
            "entities": {"keywords": []},
            "turn_intent": {"source": "deterministic-core"},
            "query_plan": {
                "task": "generic",
                "sql_ops": [],
                "operations": [],
                "locked": True,
                "plan_source": "deterministic",
            },
        }
        with patch(
            "app.agents.query_understand.llm_fill_intent",
            return_value={
                "entity": "journal",
                "operation": "trend",
                "requested_operations": [
                    {"type": "top_keywords", "required": True},
                    {"type": "keyword_growth", "required": True},
                    {"type": "topic_period_compare", "required": True},
                ],
                "goal": "research_analysis",
                "sources": ["sql"],
                "topic": [],
                "confidence": 0.93,
            },
        ) as understand:
            result = query_understand_node(state)  # type: ignore[arg-type]
        understand.assert_called_once()
        self.assertEqual(result["query_plan"], {})
        self.assertTrue(result["entities"]["intent_meta"]["rule_candidate_rejected"])
        state.update(result)
        routed = router_node(state)  # type: ignore[arg-type]
        self.assertEqual(routed["route"]["plan_source"], "schema")
        self.assertEqual(routed["query_plan"]["task"], "hotspot_compare")
        self.assertEqual(
            operation_types(routed["query_plan"]),
            ["top_keywords", "keyword_growth", "topic_period_compare"],
        )

    def test_valid_locked_rule_candidate_skips_llm(self):
        state = {
            "question": "10年内接受论文的领域变化",
            "entities": {"keywords": []},
            "turn_intent": {"source": "deterministic-core"},
            "query_plan": {
                "task": "field_evolution",
                "sql_ops": ["top_keywords", "keyword_growth", "topic_period_compare"],
                "operations": [
                    {"type": "top_keywords"},
                    {"type": "keyword_growth"},
                    {"type": "topic_period_compare"},
                ],
                "locked": True,
                "plan_source": "deterministic",
            },
        }
        with patch("app.agents.query_understand.llm_fill_intent") as understand:
            query_understand_node(state)  # type: ignore[arg-type]
        understand.assert_not_called()

    def test_low_confidence_broad_rule_is_confirmed_by_llm(self):
        state = {
            "question": "围绕期刊内容做个综合判断",
            "entities": {},
            "turn_intent": {"source": "deterministic-core", "confidence": 0.55},
            "query_plan": {
                "task": "journal_overview",
                "sql_ops": ["journal_overview"],
                "operations": [{"type": "journal_overview"}],
                "locked": True,
                "plan_source": "deterministic",
                "rule_confidence": 0.55,
            },
        }
        with patch(
            "app.agents.query_understand.llm_fill_intent",
            return_value={
                "entity": "journal", "operation": "summarize",
                "requested_operations": [{"type": "journal_overview", "required": True}],
                "goal": "research_analysis", "sources": ["sql"],
                "topic": [], "confidence": 0.9,
            },
        ) as understand:
            result = query_understand_node(state)  # type: ignore[arg-type]
        understand.assert_called_once()
        self.assertEqual(result["query_plan"], {})

    def test_locked_context_resolution_skips_llm(self):
        state = {
            "question": "展开第3位作者的论文",
            "entities": {},
            "turn_intent": {"source": "context"},
            "query_plan": {
                "task": "authors_papers",
                "sql_ops": ["papers_for_authors"],
                "operations": [{"type": "papers_for_authors"}],
                "author_ids": ["author-3"],
                "locked": True,
                "plan_source": "conversation",
            },
        }
        with patch("app.agents.query_understand.llm_fill_intent") as understand:
            query_understand_node(state)  # type: ignore[arg-type]
        understand.assert_not_called()

    def test_llm_topic_trend_without_topic_is_reconciled(self):
        plan = plan_from_intent(
            {
                "entity": "topic",
                "operation": "trend",
                "requested_operations": [{"type": "topic_yearly", "required": True}],
                "goal": "research_analysis",
                "sources": ["sql"],
                "topic": [],
                "time_range": {"start": 2017, "end": 2026, "last_n": 10},
                "metric": "keyword_freq",
                "confidence": 0.9,
            },
            "想看看这十年刊物选题版图是怎么迁移的",
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["task"], "hotspot_compare")
        self.assertEqual(
            plan["sql_ops"],
            [
                "top_keywords", "keyword_growth", "topic_period_compare",
                "representative_papers_by_topic",
            ],
        )
        self.assertEqual((plan["year_start"], plan["year_end"]), (2017, 2026))

    def test_author_ranking_and_papers_are_two_operations(self):
        plan = normalize_query_plan(
            {"task": "top_authors", "sql_ops": ["top_authors"], "top_n": 10, "year_start": 2017, "year_end": 2026},
            "近10年发文最多的作者和其发文情况",
        )
        self.assertEqual(operation_types(plan), ["top_authors", "papers_for_authors"])
        sql = execute_plan(plan, "近10年发文最多的作者和其发文情况", db=self.repo)
        by_op = sql["operation_data"]
        self.assertEqual(len(by_op), 2)
        papers = next(v for v in by_op.values() if v.get("operation") == "papers_for_authors")
        self.assertTrue(papers["author_ids"])
        self.assertTrue(papers["authors"])

    def test_named_theme_comparison_keeps_both_targets_and_growth_context(self):
        plan = normalize_query_plan(
            {"task": "hotspot_compare", "sql_ops": ["hotspot_compare"], "keywords": ["传统农业", "智能农业"]},
            "分析研究方向从传统农业到智能农业的变化",
        )
        self.assertEqual(plan["keywords"], ["传统农业", "智能农业"])
        self.assertEqual(
            operation_types(plan),
            ["topic_period_compare", "topic_yearly", "representative_papers_by_topic"],
        )

    def test_unknown_llm_operation_is_never_marked_complete(self):
        plan = normalize_query_plan(
            {"task": "generic", "sql_ops": []},
            "分析一下期刊",
            {"requested_operations": [{"type": "invented_magic_analysis", "required": True}]},
        )
        self.assertIn("invented_magic_analysis", operation_types(plan))
        result = assess_operations({"query_plan": plan})  # type: ignore[arg-type]
        unknown = next(
            row for row in result["operation_results"]
            if row["operation"] == "invented_magic_analysis"
        )
        self.assertEqual(unknown["status"], "unsupported")

    def test_author_evolution_has_three_periods_and_stable_author(self):
        data = self.repo.author_direction_evolution("徐建明")
        self.assertEqual(data["author"]["author_id"], "auth_6044e9318b18")
        self.assertEqual(len(data["periods"]), 3)
        self.assertGreaterEqual(data["total_papers"], 3)

    def test_current_year_excluded_from_growth_scoring(self):
        data = self.repo.keyword_growth(10, 2017, 2026)
        self.assertEqual(data["end_year"], 2026)
        self.assertEqual(data["score_end_year"], 2025)
        self.assertEqual(data["partial_year"], 2026)
        self.assertEqual(len(data["periods"]), 2)

    def test_institution_stability_uses_complete_ten_year_window(self):
        data = self.repo.institution_stability(10)
        self.assertEqual((data["start_year"], data["end_year"]), (2016, 2025))
        self.assertEqual(len(data["years"]), 10)
        self.assertTrue(data["institutions"])
        for row in data["institutions"]:
            self.assertIn("stable_high_output", row)

    def test_sparse_evolution_is_partial(self):
        plan = normalize_query_plan(
            {"task": "author_profile", "sql_ops": ["author_direction_evolution"], "author_name": "不存在的作者"},
            "不存在的作者研究方向有什么变化",
        )
        sql = execute_plan(plan, "不存在的作者研究方向有什么变化", db=self.repo)
        state = {"query_plan": plan, "sql_evidence": sql}
        assessed = assess_operations(state)  # type: ignore[arg-type]
        self.assertEqual(assessed["operation_results"][0]["status"], "partial")
        self.assertTrue(assessed["coverage_report"]["needs_llm"])


if __name__ == "__main__":
    unittest.main()
