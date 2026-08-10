from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TurnRecord:
    turn_id: str
    question: str
    answer: str
    intent: Dict[str, Any] = field(default_factory=dict)
    query_plan: Dict[str, Any] = field(default_factory=dict)
    result_set: Dict[str, Any] = field(default_factory=dict)
    continuation: Dict[str, Any] = field(default_factory=dict)
    citations: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "question": self.question,
            "answer": self.answer,
            "intent": self.intent,
            "query_plan": self.query_plan,
            "result_set": self.result_set,
            "continuation": self.continuation,
            "citations": self.citations,
            "created_at": self.created_at,
        }


@dataclass
class ConversationState:
    conversation_id: str
    journal_id: str
    turns: List[TurnRecord] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def history(self) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for turn in self.turns:
            rows.append({"role": "user", "content": turn.question})
            rows.append({"role": "assistant", "content": turn.answer})
        return rows


class ConversationStore:
    """Small in-memory store; replaceable by Redis without changing agents."""

    def __init__(self, ttl_seconds: int = 7200, max_sessions: int = 100, max_turns: int = 10):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self._items: Dict[Tuple[str, str], ConversationState] = {}
        self._lock = threading.RLock()

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())

    def _cleanup(self) -> None:
        now = time.time()
        expired = [k for k, v in self._items.items() if now - v.updated_at > self.ttl_seconds]
        for key in expired:
            self._items.pop(key, None)
        if len(self._items) > self.max_sessions:
            oldest = sorted(self._items.items(), key=lambda row: row[1].updated_at)
            for key, _ in oldest[: len(self._items) - self.max_sessions]:
                self._items.pop(key, None)

    def get(self, conversation_id: str, journal_id: str) -> ConversationState:
        key = (conversation_id, journal_id)
        with self._lock:
            self._cleanup()
            state = self._items.get(key)
            if state is None:
                state = ConversationState(conversation_id=conversation_id, journal_id=journal_id)
                self._items[key] = state
            state.updated_at = time.time()
            return state

    def reset(self, conversation_id: str, journal_id: str) -> ConversationState:
        key = (conversation_id, journal_id)
        with self._lock:
            state = ConversationState(conversation_id=conversation_id, journal_id=journal_id)
            self._items[key] = state
            return state

    def append(self, conversation_id: str, journal_id: str, turn: TurnRecord) -> None:
        with self._lock:
            state = self.get(conversation_id, journal_id)
            state.turns.append(turn)
            state.turns = state.turns[-self.max_turns :]
            state.updated_at = time.time()

    def reconcile_history(
        self,
        conversation_id: str,
        journal_id: str,
        client_history: List[Dict[str, str]],
    ) -> ConversationState:
        """Keep the longest exact turn prefix represented by an edited client branch."""
        with self._lock:
            state = self.get(conversation_id, journal_id)
            if not client_history:
                state.turns = []
                state.updated_at = time.time()
                return state
            matched = 0
            for index, turn in enumerate(state.turns):
                pair = [
                    {"role": "user", "content": turn.question},
                    {"role": "assistant", "content": turn.answer},
                ]
                start = index * 2
                if client_history[start : start + 2] != pair:
                    break
                matched += 1
            if matched * 2 != len(client_history):
                state.turns = []
            else:
                state.turns = state.turns[:matched]
            state.updated_at = time.time()
            return state

    def last_turn(self, conversation_id: str, journal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self.get(conversation_id, journal_id)
            return state.turns[-1].to_dict() if state.turns else None


conversation_store = ConversationStore()


def build_turn_record(question: str, result: Any) -> TurnRecord:
    evidence = dict(getattr(result, "evidence", {}) or {})
    sql = dict(evidence.get("sql") or {})
    plan = dict(evidence.get("query_plan") or {})
    canonical_result_set = dict(evidence.get("result_set") or {})
    result_type = sql.get("scope") or sql.get("task") or "answer"
    items: List[Dict[str, Any]] = []
    if result_type in {"top_authors", "keyword_authors"}:
        items = [
            {
                "type": "author",
                "id": a.get("author_id"),
                "name": a.get("name_zh") or a.get("name_en"),
                "paper_count": a.get("paper_count"),
                "position": idx,
            }
            for idx, a in enumerate(sql.get("authors") or [], 1)
        ]
    elif result_type == "authors_papers":
        for author in sql.get("authors") or []:
            for paper in author.get("papers") or []:
                items.append(
                    {
                        "type": "paper",
                        "id": paper.get("doi"),
                        "doi": paper.get("doi"),
                        "title": paper.get("title_zh"),
                        "year": paper.get("year"),
                        "author_id": author.get("author_id"),
                        "author_name": author.get("name_zh"),
                    }
                )
    else:
        items = [
            {
                "type": "paper",
                "id": c.get("doi"),
                "doi": c.get("doi"),
                "title": c.get("title"),
                "year": c.get("year"),
            }
            for c in getattr(result, "citations", []) or []
            if c.get("doi")
        ]
    continuation = {
        "has_more": bool(sql.get("has_more")),
        "next_offset": sql.get("next_offset"),
        "shown_count": int(sql.get("shown_count") or len(items)),
        "total_count": int(sql.get("total_count") or len(items)),
    }
    if canonical_result_set:
        canonical_result_set.setdefault("constraints", {
            "year_start": plan.get("year_start"),
            "year_end": plan.get("year_end"),
            "keywords": list(plan.get("keywords") or []),
        })
        items = list(canonical_result_set.get("items") or [])
    return TurnRecord(
        turn_id=str(uuid.uuid4()),
        question=question,
        answer=getattr(result, "answer", "") or "",
        intent=dict(evidence.get("turn_intent") or evidence.get("followup_intent") or {}),
        query_plan=plan,
        result_set=canonical_result_set or {
            "type": result_type,
            "items": items,
            "total_count": int(sql.get("total_count") or len(items)),
            "constraints": {
                "year_start": plan.get("year_start"),
                "year_end": plan.get("year_end"),
                "keywords": list(plan.get("keywords") or []),
            },
        },
        continuation=continuation,
        citations=list(getattr(result, "citations", []) or []),
    )
