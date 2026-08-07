from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from app.agents.controller import prepare_journal_agent, run_journal_agent
from app.agents.fusion import finalize_answer, stream_generate
from app.agents.understand import extract_year_window, regex_extract
from app.capabilities import kg_capability, rag_capability, sql_capability
from app.config import Settings, get_settings
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


class ChatOrchestrator:
    """Facade over Controller Agent pipeline + capability helpers."""

    def __init__(
        self,
        settings: Settings | None = None,
        chat: MiniMaxChat | None = None,
    ):
        self.settings = settings or get_settings()
        self.chat = chat or MiniMaxChat(self.settings)
        self.history: List[Dict[str, str]] = []
        self.last_dois: List[str] = []
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

    def reset(self) -> None:
        self.history.clear()
        self.last_dois.clear()

    def search(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        result = rag_capability.invoke(
            "semantic_search", question=query, top_k=top_k
        )
        data = result.get("data") or {}
        hits = data.get("hits") or []
        self.last_dois = [h["doi"] for h in hits if h.get("doi")]
        return hits

    def trends(self, question: str = "") -> Dict[str, Any]:
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
        )
        return result.get("data") or {}

    def graph_lookup(self, question: str) -> Dict[str, Any]:
        ents = regex_extract(question, self.last_dois)
        result = kg_capability.invoke(
            "execute_plan",
            question=question,
            entities=ents,
            plan={"kg_ops": ["author_ego"] if ents.get("author_name") else ["keyword_ego"]},
            last_dois=self.last_dois,
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
        dois = (final.get("entities") or {}).get("dois") or [
            c.get("doi") for c in citations if c.get("doi")
        ]
        self.last_dois = [d for d in dois if d]

        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})
        max_h = self.settings.chat_max_history
        if len(self.history) > max_h * 2:
            self.history = self.history[-max_h * 2 :]

        return AskResult(
            answer=answer,
            intent=legacy.get(intent, intent),
            intents=intents,
            route_reason=final.get("route_reason") or "",
            citations=citations,
            evidence={
                "intents": intents,
                "route_reason": final.get("route_reason"),
                "route": final.get("route"),
                "query_plan": final.get("query_plan"),
                "analysis_plan": final.get("analysis_plan"),
                "goal": final.get("goal"),
                "stage": final.get("stage"),
                "evidence_bundle": final.get("evidence_bundle"),
                "react_trace": final.get("react_trace"),
                "sql": final.get("sql_evidence"),
                "kg": final.get("kg_evidence"),
                "rag": final.get("rag_evidence"),
                "errors": final.get("errors") or [],
            },
        )

    def ask(self, question: str, top_k: Optional[int] = None) -> AskResult:
        final = run_journal_agent(
            question,
            history=self.history,
            top_k=top_k,
            last_dois=self.last_dois,
        )
        return self._pack_result(question, final, final.get("answer") or "")

    def ask_stream(self, question: str, top_k: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Yield SSE-friendly events: status → delta* → done | error."""
        yield {"type": "status", "message": "检索与分析中…"}
        try:
            prepared = prepare_journal_agent(
                question,
                history=self.history,
                top_k=top_k,
                last_dois=self.last_dois,
            )
        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        intents = prepared.get("intents") or ["rag"]
        yield {"type": "status", "message": "生成回答中…"}

        chunks: List[str] = []
        try:
            for delta in stream_generate(prepared):
                chunks.append(delta)
                yield {"type": "delta", "text": delta}
        except Exception as e:
            if chunks:
                answer = finalize_answer("".join(chunks), intents, prepared)
                result = self._pack_result(question, prepared, answer)
                yield {
                    "type": "done",
                    "answer": result.answer,
                    "citations": result.citations,
                    "partial": True,
                    "error": str(e),
                }
            else:
                yield {"type": "error", "message": str(e)}
            return

        answer = finalize_answer("".join(chunks), intents, prepared)
        result = self._pack_result(question, prepared, answer)
        yield {
            "type": "done",
            "answer": result.answer,
            "citations": result.citations,
            "intent": result.intent,
            "intents": result.intents,
        }
