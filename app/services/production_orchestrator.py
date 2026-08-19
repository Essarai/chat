"""Production facade backed exclusively by the unified pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, Iterator, List, Optional

from langsmith import traceable

from app.agents.understand import extract_year_window, regex_extract
from app.capabilities import kg_capability, rag_capability, sql_capability
from app.config import Settings, bind_corpus, get_settings
from app.pipeline.production import UnifiedProductionPipeline
from app.services.minimax_chat import MiniMaxChat
from app.services.neo4j_repo import Neo4jRepo
from app.services.sqlite_repo import SQLiteRepo


@dataclass
class AskResult:
    answer: str
    intent: str
    intents: List[str] = field(default_factory=list)
    route_reason: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    truncated: bool = False
    shown_count: int = 0
    total_count: int = 0
    has_more: bool = False


def _trace_ask_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    previous = inputs.get("previous_turn") or {}
    return {
        "question": inputs.get("question"),
        "top_k": inputs.get("top_k"),
        "history_turns": len(inputs.get("history") or []) // 2,
        "previous_turn_id": previous.get("turn_id"),
        "thread_id": inputs.get("thread_id"),
    }


def _trace_ask_output(result: AskResult) -> Dict[str, Any]:
    evidence = result.evidence or {}
    return {
        "answer": result.answer,
        "intent": evidence.get("turn_intent") or {},
        "query_plan": evidence.get("query_plan") or {},
        "operation_results": evidence.get("operation_results") or [],
        "coverage_report": evidence.get("coverage_report") or {},
        "quality_report": evidence.get("quality_report") or {},
        "result_set": evidence.get("result_set") or {},
        "stage_timings_ms": evidence.get("stage_timings_ms") or {},
        "errors": evidence.get("errors") or [],
        "truncated": result.truncated,
    }


def _reduce_stream_trace(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    done = next(
        (event for event in reversed(events) if event.get("type") == "done"),
        None,
    )
    if done:
        return {
            "answer": done.get("answer") or "",
            "evidence": done.get("evidence") or {},
            "truncated": bool(done.get("truncated")),
        }
    error = next(
        (event for event in reversed(events) if event.get("type") == "error"),
        None,
    )
    return error or {"status": "stream_closed_without_result"}


class ProductionOrchestrator:
    """Stable API facade; no dependency on the deprecated agent controller."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        chat: Optional[MiniMaxChat] = None,
        pipeline: Optional[UnifiedProductionPipeline] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.chat = chat or MiniMaxChat(self.settings)
        self.pipeline = pipeline or UnifiedProductionPipeline(
            self.settings,
            chat=self.chat,
        )
        self._db: Optional[SQLiteRepo] = None
        self._neo4j: Optional[Neo4jRepo] = None

    @property
    def db(self) -> SQLiteRepo:
        if self._db is None:
            self._db = SQLiteRepo(self.settings)
        return self._db

    @property
    def neo4j(self) -> Neo4jRepo:
        if self._neo4j is None:
            self._neo4j = Neo4jRepo(self.settings)
        return self._neo4j

    def _bind(self) -> Settings:
        return bind_corpus(self.settings.journal_id)

    def search(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        self._bind()
        result = rag_capability.invoke(
            "semantic_search",
            question=query,
            top_k=top_k,
            journal_id=self.settings.journal_id,
        )
        return (result.get("data") or {}).get("hits") or []

    def trends(self, question: str = "") -> Dict[str, Any]:
        self._bind()
        y0, y1 = extract_year_window(question or "近十年", default_last_n=10)
        result = sql_capability.invoke(
            "execute_plan",
            question=question or "近十年",
            entities={"year_start": y0, "year_end": y1},
            plan={
                "task": "generic",
                "sql_ops": ["yearly_counts", "top_keywords"],
                "year_start": y0,
                "year_end": y1,
            },
            journal_id=self.settings.journal_id,
        )
        return result.get("data") or {}

    def graph_lookup(self, question: str) -> Dict[str, Any]:
        self._bind()
        entities = regex_extract(question, [])
        result = kg_capability.invoke(
            "execute_plan",
            question=question,
            entities=entities,
            plan={
                "kg_ops": ["author_ego"]
                if entities.get("author_name")
                else ["keyword_ego"]
            },
            last_dois=[],
            journal_id=self.settings.journal_id,
        )
        return result.get("data") or {}

    def _pack_result(self, final: Dict[str, Any], answer: str) -> AskResult:
        intents = list(final.get("intents") or ["sql"])
        intent_name = "multi" if len(intents) > 1 else intents[0]
        legacy = {"sql": "trend", "kg": "graph", "rag": "rag", "multi": "multi"}
        citations = list(final.get("citations") or [])
        rag = dict(final.get("rag_evidence") or {})
        if rag.get("citations") and not citations:
            citations = list(rag["citations"])

        sql = dict(final.get("sql_evidence") or {})
        result_set = dict(final.get("result_set") or {})
        continuation_rows = [
            row
            for row in (final.get("operation_results") or [])
            if bool((row.get("result_set") or {}).get("has_more"))
        ]
        continuation = (continuation_rows[0].get("result_set") or {}) if continuation_rows else {}
        continuation_operation_id = (
            continuation_rows[0].get("op_id") if continuation_rows else None
        )
        if continuation_operation_id:
            continuation = {
                **continuation,
                "operation_id": continuation_operation_id,
            }
        shown_count = int(
            continuation.get("shown_count")
            or sql.get("shown_count")
            or len(result_set.get("items") or [])
        )
        total_count = int(
            continuation.get("total_count")
            or sql.get("total_count")
            or result_set.get("total_count")
            or shown_count
        )
        has_more = bool(continuation.get("has_more") or sql.get("has_more"))

        evidence = {
            "pipeline": final.get("pipeline") or {},
            "intents": intents,
            "route_reason": final.get("route_reason"),
            "followup_intent": final.get("followup_intent") or {},
            "turn_intent": final.get("turn_intent") or {},
            "intent_schema": final.get("intent") or {},
            "route": final.get("route") or {},
            "query_plan": final.get("query_plan") or {},
            "analysis_plan": final.get("analysis_plan") or {},
            "validation_report": final.get("validation_report") or {},
            "operation_results": final.get("operation_results") or [],
            "coverage_report": final.get("coverage_report") or {},
            "result_set": result_set,
            "continuation": continuation,
            "answer_coverage": final.get("answer_coverage") or {},
            "quality_report": final.get("quality_report") or {},
            "goal": final.get("goal"),
            "stage": final.get("stage"),
            "evidence_bundle": final.get("evidence_bundle") or [],
            "execution_trace": final.get("execution_trace") or [],
            "stage_timings_ms": final.get("stage_timings_ms") or {},
            "sql": final.get("sql_evidence") or {},
            "kg": final.get("kg_evidence") or {},
            "rag": final.get("rag_evidence") or {},
            "errors": final.get("errors") or [],
        }
        return AskResult(
            answer=answer,
            intent=legacy.get(intent_name, intent_name),
            intents=intents,
            route_reason=str(final.get("route_reason") or ""),
            citations=citations,
            evidence=evidence,
            truncated=has_more,
            shown_count=shown_count,
            total_count=total_count,
            has_more=has_more,
        )

    @traceable(
        name="journal_qa.ask",
        run_type="chain",
        tags=["journal-qa", "production", "unified-pipeline"],
        process_inputs=_trace_ask_inputs,
        process_outputs=_trace_ask_output,
    )
    def ask(
        self,
        question: str,
        top_k: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        last_dois: Optional[List[str]] = None,
        previous_turn: Optional[Dict[str, Any]] = None,
        thread_id: Optional[str] = None,
    ) -> AskResult:
        self._bind()
        started = time.monotonic()
        state = self.pipeline.prepare(
            question,
            top_k=top_k,
            history=history or [],
            last_dois=last_dois or [],
            previous_turn=previous_turn,
            thread_id=thread_id,
        )
        prepared_ms = int((time.monotonic() - started) * 1000)
        generation_started = time.monotonic()
        answer = self.pipeline.generate(state)
        timings = dict(state.get("stage_timings_ms") or {})
        timings.update(
            {
                "understand_query_retrieve": prepared_ms,
                "generate": int((time.monotonic() - generation_started) * 1000),
                "total": int((time.monotonic() - started) * 1000),
            }
        )
        state["stage_timings_ms"] = timings
        return self._pack_result(state, answer)

    @traceable(
        name="journal_qa.ask_stream",
        run_type="chain",
        tags=["journal-qa", "production", "stream", "unified-pipeline"],
        process_inputs=_trace_ask_inputs,
        reduce_fn=_reduce_stream_trace,
    )
    def ask_stream(
        self,
        question: str,
        top_k: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        last_dois: Optional[List[str]] = None,
        previous_turn: Optional[Dict[str, Any]] = None,
        thread_id: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        self._bind()
        started = time.monotonic()
        yield {"type": "status", "stage": "understand", "message": "理解问题中…"}
        yield {"type": "status", "stage": "retrieve", "message": "查询数据中…"}
        try:
            state = self.pipeline.prepare(
                question,
                top_k=top_k,
                history=history or [],
                last_dois=last_dois or [],
                previous_turn=previous_turn,
                thread_id=thread_id,
            )
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        prepared_ms = int((time.monotonic() - started) * 1000)
        yield {"type": "status", "stage": "answer", "message": "组织答案中…"}
        try:
            # generate() buffers and quality-gates the complete answer.  No
            # draft token can cross the SSE boundary before acceptance.
            answer = self.pipeline.generate(state)
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        timings = dict(state.get("stage_timings_ms") or {})
        timings.update(
            {
                "understand_query_retrieve": prepared_ms,
                "generate": int((time.monotonic() - started) * 1000) - prepared_ms,
                "total": int((time.monotonic() - started) * 1000),
            }
        )
        state["stage_timings_ms"] = timings
        result = self._pack_result(state, answer)
        for index in range(0, len(answer), 48):
            yield {"type": "delta", "text": answer[index : index + 48]}
        yield {
            "type": "done",
            "answer": result.answer,
            "citations": result.citations,
            "intent": result.intent,
            "intents": result.intents,
            "truncated": result.truncated,
            "shown_count": result.shown_count,
            "total_count": result.total_count,
            "has_more": result.has_more,
            "evidence": result.evidence,
        }

    @staticmethod
    def reset_thread(thread_id: str) -> None:
        """ConversationStore is the only state source; no graph checkpoint exists."""
        del thread_id


# Drop-in name for existing callers while production import paths migrate.
ChatOrchestrator = ProductionOrchestrator


__all__ = [
    "AskResult",
    "ProductionOrchestrator",
    "ChatOrchestrator",
    "_trace_ask_inputs",
]
