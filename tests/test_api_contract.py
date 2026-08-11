from __future__ import annotations

import unittest
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agents import generate
from app.api.main import app
from app.agents.controller import init_state
from app.agents.coverage import assess_operations
from app.capabilities.sql_capability import execute_plan
from app.config import get_corpus_settings
from app.services.orchestrator import ChatOrchestrator
from app.services.orchestrator import AskResult
from app.services.sqlite_repo import SQLiteRepo


class APIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = get_corpus_settings("ZDXBNXB")
        cls.repo = SQLiteRepo(cls.settings)

    def test_model_failure_degrades_to_operation_evidence(self):
        state = init_state(
            "如果策划“智慧农业”专题，适合设置哪些子方向？有哪些作者和论文可以参考？",
            journal_id="ZDXBNXB",
        )
        state["intents"] = ["sql"]
        state["sql_evidence"] = execute_plan(
            state["query_plan"], state["question"], state["entities"], self.repo
        )
        state.update(assess_operations(state))
        self.assertTrue(state["coverage_report"]["needs_llm"])

        with patch(
            "app.agents.generate.MiniMaxChat.chat", side_effect=RuntimeError("offline")
        ):
            answer = "".join(generate.stream_generate(state))

        self.assertIn("投稿适配评估", answer)
        self.assertIn("生成模型暂不可用", answer)
        self.assertTrue(
            any("generation_degraded" in error for error in state["errors"])
        )

    def test_sse_sends_only_post_gate_answer(self):
        prepared = init_state("近五年发文最多的作者", journal_id="ZDXBNXB")
        prepared["intents"] = ["sql"]
        prepared["sql_evidence"] = execute_plan(
            prepared["query_plan"],
            prepared["question"],
            prepared["entities"],
            self.repo,
        )
        prepared.update(assess_operations(prepared))
        prepared["citations"] = []

        with patch(
            "app.services.orchestrator.prepare_journal_agent",
            return_value=prepared,
        ), patch(
            "app.services.orchestrator.stream_generate",
            return_value=iter(["## 错误草稿\n\n虚构统计 999 篇"]),
        ):
            events = list(ChatOrchestrator(self.settings).ask_stream("近五年发文最多的作者"))

        visible = "".join(event.get("text") or "" for event in events if event["type"] == "delta")
        done = next(event for event in events if event["type"] == "done")
        self.assertNotIn("999", visible)
        self.assertEqual(visible, done["answer"])
        self.assertTrue(done["evidence"]["quality_report"]["hard_gate_passed"])
        self.assertEqual(
            len(done["evidence"]["operation_results"]),
            len(prepared["query_plan"]["operations"]),
        )

    def test_api_completion_contract_includes_conversation_metadata(self):
        evidence = {
            "query_plan": {"task": "clarification", "operations": []},
            "turn_intent": {"kind": "new_question", "action": "query"},
            "result_set": {"items": [], "constraints": {}},
            "operation_results": [],
            "coverage_report": {"coverage": 1.0},
            "quality_report": {"hard_gate_passed": True},
            "sql": {},
        }

        class FakeBot:
            @staticmethod
            def ask(*args, **kwargs):
                return AskResult(
                    answer="ok",
                    intent="sql",
                    intents=["sql"],
                    evidence=evidence,
                )

            @staticmethod
            def ask_stream(*args, **kwargs):
                yield {"type": "status", "stage": "understand", "message": "理解问题中…"}
                yield {"type": "delta", "text": "ok"}
                yield {
                    "type": "done",
                    "answer": "ok",
                    "citations": [],
                    "truncated": False,
                    "shown_count": 0,
                    "total_count": 0,
                    "has_more": False,
                    "evidence": evidence,
                }

        client = TestClient(app)
        conversation_id = "api-contract-test"
        with patch("app.api.main.get_bot", return_value=FakeBot()):
            response = client.post(
                "/ask",
                json={
                    "question": "测试",
                    "journal_id": "ZDXBNXB",
                    "conversation_id": conversation_id,
                    "reset": True,
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["conversation_id"], conversation_id)
            self.assertTrue(payload["turn_id"])
            self.assertIn("truncated", payload)
            self.assertIn("shown_count", payload)
            self.assertIn("total_count", payload)
            self.assertIn("has_more", payload)

            stream = client.post(
                "/ask/stream",
                json={
                    "question": "测试流",
                    "journal_id": "ZDXBNXB",
                    "conversation_id": conversation_id,
                    "reset": True,
                },
            )
        events = [
            json.loads(line[6:])
            for line in stream.text.splitlines()
            if line.startswith("data: ")
        ]
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(done["conversation_id"], conversation_id)
        self.assertTrue(done["turn_id"])
        self.assertIn("truncated", done)
        self.assertIn("shown_count", done)
        self.assertIn("total_count", done)
        self.assertIn("has_more", done)


if __name__ == "__main__":
    unittest.main()
