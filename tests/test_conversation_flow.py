from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fastapi import HTTPException

from app.agents.controller import init_state
from app.agents.capability_planner import plan_from_intent
from app.agents.coverage import assess_operations
from app.agents.generate import try_operation_plan_answer
from app.agents.operation_contracts import normalize_query_plan
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

    def test_collection_selectors_use_previous_order_only(self):
        previous = self._top_turn()
        _, first_two = understand_contextual_turn("列出前两位作者的论文", previous)
        self.assertEqual(
            first_two["author_ids"],
            [row["id"] for row in previous["result_set"]["items"][:2]],
        )
        _, last = understand_contextual_turn("列出最后一位作者的论文", previous)
        self.assertEqual(last["author_ids"], [previous["result_set"]["items"][-1]["id"]])
        _, most = understand_contextual_turn("列出其中发文最多的作者论文", previous)
        self.assertEqual(most["author_ids"], [previous["result_set"]["items"][0]["id"]])

    def test_natural_top_three_followup_expands_previous_authors(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("前三的作者有哪些发文？", previous)
        self.assertEqual(intent["kind"], "followup")
        self.assertEqual(intent["selector"], {"first": 3})
        self.assertEqual(plan["task"], "authors_papers")
        self.assertEqual(
            plan["author_ids"],
            [row["id"] for row in previous["result_set"]["items"][:3]],
        )
        self.assertEqual((plan["year_start"], plan["year_end"]), (2022, 2026))
        normalized = normalize_query_plan(plan, "前三的作者有哪些发文？", intent)
        covered = assess_operations(
            {
                "query_plan": normalized,
                "sql_evidence": execute_plan(normalized, db=self.repo),
                "kg_evidence": {},
                "rag_evidence": {},
            }
        )["result_set"]
        self.assertEqual(
            {item["author_id"] for item in covered["items"]},
            set(normalized["author_ids"]),
        )
        self.assertEqual(covered["total_count"], len(covered["items"]))

    def test_followup_explicit_time_overrides_inherited_time(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("列出这些作者近三年的论文", previous)
        self.assertEqual((plan["year_start"], plan["year_end"]), (2024, 2026))
        self.assertEqual(
            intent["explicit_constraints"],
            {"year_start": 2024, "year_end": 2026},
        )

    def test_out_of_range_selector_requests_clarification(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("展开第 99 位作者", previous)
        self.assertTrue(intent["needs_clarification"])
        self.assertEqual(plan["task"], "clarification")

    def test_context_dependent_question_without_result_set_requests_clarification(self):
        intent, plan = understand_contextual_turn("展开第 3 位作者", None)
        self.assertTrue(intent["needs_clarification"])
        self.assertEqual(plan["task"], "clarification")

    def test_ambiguous_singular_pronoun_requests_clarification(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("展开他的论文", previous)
        self.assertTrue(intent["needs_clarification"])
        self.assertEqual(plan["task"], "clarification")

    def test_new_institution_question_does_not_inherit_author_scope(self):
        previous = self._top_turn()
        intent, plan = understand_contextual_turn("近十年机构排名", previous)
        self.assertEqual(intent["kind"], "new_question")
        self.assertEqual(plan["operations"][0]["type"], "top_institutions")
        self.assertEqual((plan["year_start"], plan["year_end"]), (2017, 2026))
        self.assertNotIn("author_ids", plan)

    def test_author_ranking_question_words_are_not_person_names(self):
        intent, plan = understand_contextual_turn(
            "最近3年发文量前10的作者有哪些", None
        )
        self.assertEqual(intent["target_entity"], "author")
        self.assertEqual(plan["main_task"], "top_authors")
        self.assertEqual(
            [operation["type"] for operation in plan["operations"]],
            ["top_authors"],
        )
        self.assertIsNone(plan["author_name"])
        self.assertEqual((plan["year_start"], plan["year_end"]), (2024, 2026))

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

    def test_paper_set_topic_similarity_uses_previous_dois(self):
        dois = [
            "10.3785/j.issn.1008-9209.2023.12.184",
            "10.3785/j.issn.1008-9209.2024.01.101",
            "10.3785/j.issn.1008-9209.2024.09.191",
            "10.3785/j.issn.1008-9209.2024.10.171",
        ]
        previous = {
            "turn_id": "papers-turn",
            "query_plan": {"task": "authors_papers"},
            "result_set": {
                "constraints": {"year_start": 2024, "year_end": 2026, "keywords": []},
                "items": [
                    {"type": "paper", "doi": doi, "author_id": "a1", "author_name": "杨怡"}
                    for doi in dois
                ]
                + [{"type": "paper", "doi": dois[0], "author_id": "a2", "author_name": "陈学秋"}],
            },
            "continuation": {},
        }
        intent, plan = understand_contextual_turn(
            "这些发文的主题有什么相似性", previous
        )
        self.assertEqual(intent["kind"], "followup")
        self.assertEqual(intent["action"], "summarize")
        self.assertEqual(plan["dois"], dois)
        self.assertEqual(plan["source_record_count"], 5)
        normalized = normalize_query_plan(plan, "这些发文的主题有什么相似性", intent)
        self.assertEqual(
            [operation["type"] for operation in normalized["operations"]],
            ["paper_set_topic_summary"],
        )
        self.assertEqual(normalized["keywords"], [])
        self.assertEqual(normalized["constraints"]["keywords"], [])
        sql = execute_plan(normalized, db=self.repo)
        covered = assess_operations(
            {
                "query_plan": normalized,
                "sql_evidence": sql,
                "kg_evidence": {},
                "rag_evidence": {},
            }
        )
        answer = try_operation_plan_answer(
            {
                "query_plan": normalized,
                "operation_results": covered["operation_results"],
                "coverage_report": covered["coverage_report"],
            }
        )
        self.assertEqual(sql["paper_count"], 4)
        self.assertIn("刚地弓形虫", answer)
        self.assertNotIn("None", answer)

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

    def test_continue_without_cursor_requests_clarification(self):
        previous = {
            "turn_id": "t1",
            "query_plan": {"task": "topic_papers"},
            "result_set": {"constraints": {}, "items": []},
            "continuation": {"has_more": False},
        }
        intent, plan = understand_contextual_turn("继续", previous)
        self.assertTrue(intent["needs_clarification"])
        self.assertEqual(plan["task"], "clarification")

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

    def test_topic_papers_list_all_then_continue_without_duplicates(self):
        class FakeRepo:
            @staticmethod
            def papers_by_topics(topics, limit_per_topic, start_year, end_year):
                papers = [
                    {
                        "doi": f"doi-{i}",
                        "title_zh": f"论文{i}",
                        "year": 2026,
                        "authors": ["测试作者"],
                    }
                    for i in range(125)
                ]
                return {
                    "scope": "topic_papers",
                    "topics": [{"topic": topics[0], "paper_count": 125, "papers": papers}],
                    "papers": papers,
                    "total_count": 125,
                    "start_year": start_year,
                    "end_year": end_year,
                    "source": "sqlite",
                }

            @staticmethod
            def related_keywords_for_topic(topic, limit, start_year, end_year):
                return []

        state = init_state(
            "近五年该刊关于马克思的研究有哪些？主要关注什么问题？",
            journal_id="ZDXBRWB",
        )
        first = execute_plan(state["query_plan"], db=FakeRepo())
        self.assertEqual(first["shown_count"], 100)
        self.assertEqual(first["total_count"], 125)
        self.assertTrue(first["has_more"])
        self.assertEqual(len(first["papers"]), 100)

        result = SimpleNamespace(
            answer="first",
            citations=[],
            evidence={
                "query_plan": state["query_plan"],
                "turn_intent": state["turn_intent"],
                "sql": first,
            },
        )
        previous = build_turn_record("主题论文", result).to_dict()
        intent, next_plan = understand_contextual_turn("继续", previous)
        self.assertEqual(intent["action"], "continue")
        self.assertEqual(next_plan["offset"], 100)
        second = execute_plan(next_plan, db=FakeRepo())
        self.assertEqual(second["shown_count"], 25)
        self.assertFalse(second["has_more"])
        self.assertFalse(
            {paper["doi"] for paper in first["papers"]}
            & {paper["doi"] for paper in second["papers"]}
        )

    def test_multi_operation_paper_list_continues_only_truncated_operation(self):
        question = "近十年核心作者和研究团队有哪些？他们的研究方向发生了什么变化？"
        state = init_state(question, journal_id="ZDXBNXB")
        first = execute_plan(state["query_plan"], question, state["entities"], self.repo)
        paper_op = first["operation_data"]["op_2_papers_for_authors"]
        self.assertTrue(paper_op["has_more"])
        result = SimpleNamespace(
            answer="first",
            citations=[],
            evidence={
                "query_plan": state["query_plan"],
                "turn_intent": state["turn_intent"],
                "sql": first,
            },
        )
        previous = build_turn_record(question, result).to_dict()
        _, next_plan = understand_contextual_turn("继续", previous)
        self.assertEqual(
            [op["type"] for op in next_plan["operations"]],
            ["papers_for_authors"],
        )
        second = execute_plan(next_plan, "继续", {}, self.repo)
        self.assertFalse(second["has_more"])
        first_rows = {
            (group["author_id"], paper["doi"])
            for group in paper_op["authors"]
            for paper in group["papers"]
        }
        second_rows = {
            (group["author_id"], paper["doi"])
            for group in second["authors"]
            for paper in group["papers"]
        }
        self.assertFalse(first_rows & second_rows)

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

    def test_edit_with_unanswered_user_turn_keeps_exact_prefix(self):
        store = ConversationStore()
        first = build_turn_record("q1", SimpleNamespace(answer="a1", citations=[], evidence={}))
        second = build_turn_record("q2", SimpleNamespace(answer="a2", citations=[], evidence={}))
        store.append("c1", "ZDXBNXB", first)
        store.append("c1", "ZDXBNXB", second)
        state = store.reconcile_history(
            "c1",
            "ZDXBNXB",
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2 edited"},
            ],
        )
        self.assertEqual([turn.question for turn in state.turns], ["q1"])

    def test_store_enforces_session_and_turn_caps(self):
        store = ConversationStore(max_sessions=2, max_turns=2)
        for index in range(3):
            for turn_index in range(3):
                store.append(
                    f"c{index}",
                    "ZDXBNXB",
                    build_turn_record(
                        f"q{turn_index}",
                        SimpleNamespace(answer=f"a{turn_index}", citations=[], evidence={}),
                    ),
                )
        self.assertLessEqual(len(store.list_conversations("ZDXBNXB")), 2)
        self.assertLessEqual(len(store.get("c2", "ZDXBNXB").turns), 2)

    def test_store_survives_reopen(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversations.db"
            first = ConversationStore(path, ttl_seconds=0, max_sessions=0)
            first.append(
                "persistent",
                "ZDXBNXB",
                build_turn_record(
                    "第一轮",
                    SimpleNamespace(answer="回答", citations=[], evidence={}),
                ),
            )
            first.close()

            reopened = ConversationStore(path, ttl_seconds=0, max_sessions=0)
            state = reopened.get("persistent", "ZDXBNXB")
            self.assertEqual([(turn.question, turn.answer) for turn in state.turns], [("第一轮", "回答")])
            self.assertEqual(reopened.list_conversations("ZDXBNXB")[0]["turn_count"], 1)
            reopened.close()

    def test_turn_record_preserves_operation_evidence(self):
        result = SimpleNamespace(
            answer="a",
            citations=[],
            evidence={
                "operation_results": [{"op_id": "op_1", "status": "complete"}],
                "coverage_report": {"coverage": 1.0},
            },
        )
        turn = build_turn_record("q", result).to_dict()
        self.assertEqual(turn["operation_results"][0]["op_id"], "op_1")
        self.assertEqual(turn["coverage_report"]["coverage"], 1.0)

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

    def test_ten_years_within_accepted_paper_fields_is_not_an_author(self):
        state = init_state("10年内接受论文的领域变化", journal_id="ZDXBNXB")
        plan = state["query_plan"]
        self.assertEqual(plan["task"], "field_evolution")
        self.assertIsNone(plan.get("author_name"))
        self.assertEqual((plan.get("year_start"), plan.get("year_end")), (2017, 2026))
        self.assertEqual(
            [row.get("type") for row in plan.get("operations") or []],
            [
                "yearly_counts", "top_keywords", "keyword_growth",
                "topic_period_compare", "representative_papers_by_topic",
            ],
        )

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
