from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, Iterator, List, Optional

from app.agents.controller import prepare_journal_agent, run_journal_agent
from app.agents.fusion import finalize_answer, stream_generate
from app.agents.understand import extract_year_window, regex_extract
from app.capabilities import kg_capability, rag_capability, sql_capability
from app.config import Settings, bind_corpus, get_settings
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


class ChatOrchestrator:
    """Facade over Controller Agent pipeline + capability helpers."""

    def __init__(
        self,
        settings: Settings | None = None,
        chat: MiniMaxChat | None = None,
    ):
        self.settings = settings or get_settings()
        self.chat = chat or MiniMaxChat(self.settings)
        self._db: SQLiteRepo | None = None
        self._neo4j: Neo4jRepo | None = None

    @property
    def db(self) -> SQLiteRepo:
        """SQLite repo for /trends APIs (lazy)."""
        if self._db is None:
            self._db = SQLiteRepo(self.settings)
        return self._db

    @property
    def neo4j(self) -> Neo4jRepo:
        """Neo4j repo for /graph APIs (lazy)."""
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
        data = result.get("data") or {}
        hits = data.get("hits") or []
        return hits

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
        ents = regex_extract(question, [])
        result = kg_capability.invoke(
            "execute_plan",
            question=question,
            entities=ents,
            plan={"kg_ops": ["author_ego"] if ents.get("author_name") else ["keyword_ego"]},
            last_dois=[],
            journal_id=self.settings.journal_id,
        )
        return result.get("data") or {}

    def _pack_result(self, question: str, final: Dict[str, Any], answer: str) -> AskResult:
        intents = final.get("intents") or ["rag"]
        intent = "multi" if len(intents) > 1 else intents[0]
        legacy = {"sql": "trend", "kg": "graph", "rag": "rag", "multi": "multi"}
        citations = final.get("citations") or []
        rag = final.get("rag_evidence") or {}
        if rag.get("citations") and not citations:
            citations = rag["citations"]
        sql = final.get("sql_evidence") or {}
        result_set = final.get("result_set") or {}
        shown_count = int(sql.get("shown_count") or 0)
        if not shown_count:
            shown_count = len(result_set.get("items") or [])
        if not shown_count:
            for key in ("authors", "institutions", "papers", "keywords", "yearly"):
                if isinstance(sql.get(key), list):
                    shown_count = len(sql[key])
                    if shown_count:
                        break
        total_count = int(sql.get("total_count") or shown_count)
        has_more = bool(sql.get("has_more"))

        return AskResult(
            answer=answer,
            intent=legacy.get(intent, intent),
            intents=intents,
            route_reason=final.get("route_reason") or "",
            citations=citations,
            evidence={
                "intents": intents,
                "route_reason": final.get("route_reason"),
                "followup_intent": final.get("followup_intent"),
                "turn_intent": final.get("turn_intent"),
                "intent_schema": final.get("intent") or {},
                "route": final.get("route"),
                "query_plan": final.get("query_plan"),
                "analysis_plan": final.get("analysis_plan"),
                "operation_results": final.get("operation_results") or [],
                "coverage_report": final.get("coverage_report") or {},
                "result_set": result_set,
                "answer_coverage": final.get("answer_coverage") or {},
                "goal": final.get("goal"),
                "stage": final.get("stage"),
                "evidence_bundle": final.get("evidence_bundle"),
                "react_trace": final.get("react_trace"),
                "sql": final.get("sql_evidence"),
                "kg": final.get("kg_evidence"),
                "rag": final.get("rag_evidence"),
                "errors": final.get("errors") or [],
            },
            truncated=has_more,
            shown_count=shown_count,
            total_count=total_count,
            has_more=has_more,
        )

    def ask(
        self,
        question: str,
        top_k: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        last_dois: Optional[List[str]] = None,
        previous_turn: Optional[Dict[str, Any]] = None,
    ) -> AskResult:
        self._bind()
        started = time.monotonic()
        prepared = prepare_journal_agent(
            question,
            history=history or [],
            top_k=top_k,
            last_dois=last_dois or [],
            journal_id=self.settings.journal_id,
            previous_turn=previous_turn,
        )
        prepared_ms = int((time.monotonic() - started) * 1000)
        generation_started = time.monotonic()
        answer = finalize_answer("".join(stream_generate(prepared)), prepared.get("intents") or [], prepared)
        from app.agents.coverage import assess_answer_coverage
        prepared["answer_coverage"] = assess_answer_coverage(answer, prepared.get("operation_results") or [])
        prepared["stage_timings_ms"] = {
            "understand_query_retrieve": prepared_ms,
            "generate": int((time.monotonic() - generation_started) * 1000),
            "total": int((time.monotonic() - started) * 1000),
        }
        result = self._pack_result(question, prepared, answer)
        result.evidence["stage_timings_ms"] = prepared["stage_timings_ms"]
        return result

    def ask_stream(
        self,
        question: str,
        top_k: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        last_dois: Optional[List[str]] = None,
        previous_turn: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield SSE-friendly events: status → delta* → done | error."""
        self._bind()
        started = time.monotonic()
        yield {"type": "status", "stage": "understand", "message": "理解问题中…"}
        yield {"type": "status", "stage": "retrieve", "message": "查询数据中…"}
        try:
            prepared = prepare_journal_agent(
                question,
                history=history or [],
                top_k=top_k,
                last_dois=last_dois or [],
                journal_id=self.settings.journal_id,
                previous_turn=previous_turn,
            )
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        intents = prepared.get("intents") or ["rag"]
        prepared_ms = int((time.monotonic() - started) * 1000)
        yield {"type": "status", "stage": "answer", "message": "组织答案中…"}

        chunks: List[str] = []
        try:
            for delta in stream_generate(prepared):
                chunks.append(delta)
                yield {"type": "delta", "text": delta}
        except Exception as e:
            if chunks:
                answer = finalize_answer("".join(chunks), intents, prepared)
                from app.agents.coverage import assess_answer_coverage
                prepared["answer_coverage"] = assess_answer_coverage(answer, prepared.get("operation_results") or [])
                result = self._pack_result(question, prepared, answer)
                yield {
                    "type": "done",
                    "answer": result.answer,
                    "citations": result.citations,
                    "partial": True,
                    "error": str(e),
                    "truncated": result.truncated,
                    "shown_count": result.shown_count,
                    "total_count": result.total_count,
                    "has_more": result.has_more,
                    "evidence": result.evidence,
                }
            else:
                yield {"type": "error", "message": str(e)}
            return

        answer = finalize_answer("".join(chunks), intents, prepared)
        from app.agents.coverage import assess_answer_coverage
        prepared["answer_coverage"] = assess_answer_coverage(answer, prepared.get("operation_results") or [])
        prepared["stage_timings_ms"] = {
            "understand_query_retrieve": prepared_ms,
            "generate": int((time.monotonic() - started) * 1000) - prepared_ms,
            "total": int((time.monotonic() - started) * 1000),
        }
        result = self._pack_result(question, prepared, answer)
        result.evidence["stage_timings_ms"] = prepared["stage_timings_ms"]
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
