from __future__ import annotations

import unittest

from app.agents.coverage import assess_operations
from app.agents.operation_contracts import normalize_query_plan, operation_types
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
        self.assertEqual(operation_types(plan), ["topic_period_compare", "keyword_growth"])

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
