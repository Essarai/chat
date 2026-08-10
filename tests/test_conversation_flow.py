from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.agents.controller import init_state
from app.agents.capability_planner import plan_from_intent
from app.agents.turn_understand import understand_contextual_turn
from app.api.main import _validated_journal_id
from app.capabilities.sql_capability import execute_plan
from app.config import get_corpus_settings
from app.services.conversation_store import ConversationStore, build_turn_record
from app.services.sqlite_repo import SQLiteRepo


class ConversationFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = SQLiteRepo(get_corpus_settings("ZDXBNXB"))

    def _top_turn(self):
        state = init_state("近五年发文最多的作者", journal_id="ZDXBNXB")
        self.assertTrue(state["query_plan"]["locked"])
        self.assertEqual(state["query_plan"]["task"], "top_authors")
        sql = execute_plan(state["query_plan"], db=self.repo)
        result = SimpleNamespace(
            answer="top authors",
            citations=[],
            evidence={
                "query_plan": state["query_plan"],
                "turn_intent": state["turn_intent"],
                "sql": sql,
            },
        )
        return build_turn_record("近五年发文最多的作者", result).to_dict()

    def test_expand_all_inherits_author_ids_and_years(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("展开其具体的论文", previous)
        self.assertEqual(intent["kind"], "followup")
        self.assertEqual(plan["task"], "authors_papers")
        self.assertEqual(plan["year_start"], 2022)
        self.assertEqual(plan["year_end"], 2026)
        expected_ids = [row["id"] for row in previous["result_set"]["items"]]
        self.assertEqual(plan["author_ids"], expected_ids)
        sql = execute_plan(plan, db=self.repo)
        self.assertEqual(sql["scope"], "authors_papers")
        self.assertEqual(sql["author_ids"], expected_ids)
        for author in sql["authors"]:
            for paper in author["papers"]:
                self.assertGreaterEqual(int(paper["year"]), 2022)
                self.assertLessEqual(int(paper["year"]), 2026)

    def test_natural_collective_wording_expands_all_authors(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("他们各自发文列一下", previous)
        self.assertEqual(intent["kind"], "followup")
        self.assertEqual(intent["selector"], {"scope": "all"})
        self.assertEqual(plan["task"], "authors_papers")
        self.assertEqual(
            plan["author_ids"],
            [row["id"] for row in previous["result_set"]["items"]],
        )
        self.assertEqual((plan["year_start"], plan["year_end"]), (2022, 2026))

    def test_ordinal_selects_stable_third_author_id(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("展开第 3 位作者", previous)
        self.assertEqual(intent["selector"], {"ordinal": 3})
        self.assertEqual(plan["author_ids"], [previous["result_set"]["items"][2]["id"]])

    def test_ambiguous_singular_pronoun_requests_clarification(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("展开他的论文", previous)
        self.assertTrue(intent["needs_clarification"])
        self.assertEqual(plan["task"], "clarification")

    def test_new_institution_question_does_not_inherit_author_scope(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("近十年机构排名", previous)
        self.assertEqual(intent["kind"], "new_question")
        self.assertIsNone(plan)

    def test_sort_preserves_previous_dois(self):
        previous = {
            "turn_id": "t1",
            "query_plan": {"task": "authors_papers"},
            "result_set": {
                "constraints": {"year_start": 2022, "year_end": 2026},
                "items": [
                    {"type": "paper", "doi": "d1", "year": 2023, "title": "B"},
                    {"type": "paper", "doi": "d2", "year": 2025, "title": "A"},
                ],
            },
            "continuation": {},
        }
        intent, plan = understand_contextual_turn("这些论文按年份排序", previous)
        self.assertEqual(intent["action"], "sort")
        self.assertEqual([p["doi"] for p in plan["targets"]], ["d2", "d1"])

    def test_continue_uses_cursor_without_restarting(self):
        previous = {
            "turn_id": "t1",
            "query_plan": {"task": "authors_papers", "offset": 0},
            "result_set": {"constraints": {}, "items": []},
            "continuation": {"has_more": True, "next_offset": 100},
        }
        intent, plan = understand_contextual_turn("继续", previous)
        self.assertEqual(intent["action"], "continue")
        self.assertEqual(plan["offset"], 100)

    def test_answer_capacity_sets_continuation_cursor(self):
        class FakeRepo:
            def papers_for_authors(self, author_ids, start_year, end_year):
                return [
                    {
                        "author_id": author_ids[0],
                        "name_zh": "测试作者",
                        "papers": [
                            {"doi": f"doi-{i}", "title_zh": f"论文{i}", "year": 2026}
                            for i in range(125)
                        ],
                    }
                ]

        plan = {
            "task": "authors_papers",
            "sql_ops": ["papers_for_authors"],
            "author_ids": ["a1"],
            "max_items": 100,
            "max_chars": 24000,
        }
        result = execute_plan(plan, db=FakeRepo())
        self.assertEqual(result["shown_count"], 100)
        self.assertEqual(result["total_count"], 125)
        self.assertEqual(result["next_offset"], 100)
        self.assertTrue(result["has_more"])

    def test_store_isolation_reset_and_edit_truncation(self):
        store = ConversationStore()
        first = build_turn_record(
            "q1", SimpleNamespace(answer="a1", citations=[], evidence={})
        )
        second = build_turn_record(
            "q2", SimpleNamespace(answer="a2", citations=[], evidence={})
        )
        store.append("c1", "ZDXBNXB", first)
        store.append("c1", "ZDXBNXB", second)
        store.append("c2", "ZDXBNXB", first)
        state = store.reconcile_history(
            "c1",
            "ZDXBNXB",
            [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}],
        )
        self.assertEqual(len(state.turns), 1)
        self.assertEqual(len(store.get("c2", "ZDXBNXB").turns), 1)
        self.assertEqual(len(store.get("c1", "ZDXBRWB").turns), 0)
        self.assertEqual(len(store.reset("c1", "ZDXBNXB").turns), 0)

    def test_invalid_journal_is_400(self):
        with self.assertRaises(HTTPException) as caught:
            _validated_journal_id("DOES_NOT_EXIST")
        self.assertEqual(caught.exception.status_code, 400)

    def test_journal_theme_change_is_not_publication_growth(self):
        intent = {
            "entity": "journal",
            "operation": "trend",
            "goal": "trend",
            "topics": [],
            "sources": ["sql"],
            "confidence": 0.95,
        }
        plan = plan_from_intent(intent, "期刊主题变化")
        self.assertEqual(plan["task"], "hotspot_compare")
        self.assertEqual(plan["sql_ops"], ["hotspot_compare"])
        self.assertNotIn("yearly_growth", plan["task"])

    def test_journal_accepted_fields_change_maps_to_theme_comparison(self):
        intent = {
            "entity": "journal",
            "operation": "trend",
            "goal": "research_analysis",
            "topic": [],
            "sources": ["sql"],
            "confidence": 0.95,
        }
        plan = plan_from_intent(intent, "期刊接受文章的领域变化")
        self.assertEqual(plan["task"], "hotspot_compare")

    def test_hotspot_inventory_does_not_search_literal_hotspot(self):
        intent = {
            "entity": "topic",
            "operation": "trend",
            "goal": "inventory",
            "topic": ["热点"],
            "time_range": {"start": 2022, "end": 2026},
            "sources": ["sql"],
            "confidence": 0.95,
        }
        plan = plan_from_intent(intent, "近五年有哪些热点")
        self.assertEqual(plan["task"], "top_directions_with_papers")
        self.assertEqual(plan["keywords"], [])
        self.assertEqual((plan["year_start"], plan["year_end"]), (2022, 2026))


if __name__ == "__main__":
    unittest.main()
