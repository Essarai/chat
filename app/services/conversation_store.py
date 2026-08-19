from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import ROOT


@dataclass
class TurnRecord:
    turn_id: str
    question: str
    answer: str
    intent: Dict[str, Any] = field(default_factory=dict)
    query_plan: Dict[str, Any] = field(default_factory=dict)
    operation_results: List[Dict[str, Any]] = field(default_factory=list)
    coverage_report: Dict[str, Any] = field(default_factory=dict)
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
            "operation_results": self.operation_results,
            "coverage_report": self.coverage_report,
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
    """SQLite-backed user-facing conversation projection."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        ttl_seconds: int = 7200,
        max_sessions: int = 100,
        max_turns: int = 10,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if str(db_path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_index (
                conversation_id TEXT NOT NULL,
                journal_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (conversation_id, journal_id)
            );
            CREATE TABLE IF NOT EXISTS conversation_turns (
                conversation_id TEXT NOT NULL,
                journal_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                turn_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (conversation_id, journal_id, position),
                FOREIGN KEY (conversation_id, journal_id)
                    REFERENCES conversation_index(conversation_id, journal_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_updated
                ON conversation_index(journal_id, updated_at DESC);
            """
        )

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())

    def _cleanup(self) -> None:
        now = time.time()
        with self._conn:
            if self.ttl_seconds > 0:
                self._conn.execute(
                    "DELETE FROM conversation_index WHERE updated_at < ?",
                    (now - self.ttl_seconds,),
                )
            if self.max_sessions > 0:
                count = int(
                    self._conn.execute("SELECT COUNT(*) FROM conversation_index").fetchone()[0]
                )
                if count > self.max_sessions:
                    self._conn.execute(
                        """
                        DELETE FROM conversation_index
                        WHERE (conversation_id, journal_id) IN (
                            SELECT conversation_id, journal_id
                            FROM conversation_index
                            ORDER BY updated_at ASC
                            LIMIT ?
                        )
                        """,
                        (count - self.max_sessions,),
                    )

    def _load(self, conversation_id: str, journal_id: str) -> ConversationState:
        row = self._conn.execute(
            """
            SELECT updated_at FROM conversation_index
            WHERE conversation_id = ? AND journal_id = ?
            """,
            (conversation_id, journal_id),
        ).fetchone()
        turns = [
            TurnRecord(**json.loads(item["payload"]))
            for item in self._conn.execute(
                """
                SELECT payload FROM conversation_turns
                WHERE conversation_id = ? AND journal_id = ?
                ORDER BY position
                """,
                (conversation_id, journal_id),
            )
        ]
        return ConversationState(
            conversation_id=conversation_id,
            journal_id=journal_id,
            turns=turns,
            updated_at=float(row["updated_at"]) if row else time.time(),
        )

    def get(self, conversation_id: str, journal_id: str) -> ConversationState:
        with self._lock:
            self._cleanup()
            return self._load(conversation_id, journal_id)

    def reset(self, conversation_id: str, journal_id: str) -> ConversationState:
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM conversation_index WHERE conversation_id = ? AND journal_id = ?",
                    (conversation_id, journal_id),
                )
            return ConversationState(conversation_id=conversation_id, journal_id=journal_id)

    def append(self, conversation_id: str, journal_id: str, turn: TurnRecord) -> None:
        with self._lock:
            now = time.time()
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO conversation_index
                        (conversation_id, journal_id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(conversation_id, journal_id)
                    DO UPDATE SET updated_at = excluded.updated_at
                    """,
                    (conversation_id, journal_id, turn.question[:80] or "未命名对话", now, now),
                )
                position = int(
                    self._conn.execute(
                        """
                        SELECT COALESCE(MAX(position), -1) + 1 FROM conversation_turns
                        WHERE conversation_id = ? AND journal_id = ?
                        """,
                        (conversation_id, journal_id),
                    ).fetchone()[0]
                )
                self._conn.execute(
                    """
                    INSERT INTO conversation_turns
                        (conversation_id, journal_id, position, turn_id, payload)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        journal_id,
                        position,
                        turn.turn_id,
                        json.dumps(turn.to_dict(), ensure_ascii=False),
                    ),
                )
                if self.max_turns > 0:
                    self._conn.execute(
                        """
                        DELETE FROM conversation_turns
                        WHERE conversation_id = ? AND journal_id = ?
                          AND position NOT IN (
                              SELECT position FROM conversation_turns
                              WHERE conversation_id = ? AND journal_id = ?
                              ORDER BY position DESC LIMIT ?
                          )
                        """,
                        (
                            conversation_id,
                            journal_id,
                            conversation_id,
                            journal_id,
                            self.max_turns,
                        ),
                    )
            self._cleanup()

    def reconcile_history(
        self,
        conversation_id: str,
        journal_id: str,
        client_history: List[Dict[str, str]],
    ) -> ConversationState:
        """Keep the longest exact turn prefix represented by an edited client branch."""
        with self._lock:
            state = self._load(conversation_id, journal_id)
            if not client_history:
                return self.reset(conversation_id, journal_id)
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
            # Any mismatch marks the edited branch point. Preserve only the
            # exact complete-turn prefix before that point.
            with self._conn:
                self._conn.execute(
                    """
                    DELETE FROM conversation_turns
                    WHERE conversation_id = ? AND journal_id = ? AND position >= ?
                    """,
                    (conversation_id, journal_id, matched),
                )
                self._conn.execute(
                    """
                    UPDATE conversation_index SET updated_at = ?
                    WHERE conversation_id = ? AND journal_id = ?
                    """,
                    (time.time(), conversation_id, journal_id),
                )
            return self._load(conversation_id, journal_id)

    def last_turn(self, conversation_id: str, journal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            state = self._load(conversation_id, journal_id)
            return state.turns[-1].to_dict() if state.turns else None

    def list_conversations(self, journal_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            self._cleanup()
            return [
                {
                    "conversation_id": row["conversation_id"],
                    "journal_id": row["journal_id"],
                    "title": row["title"],
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                    "turn_count": int(row["turn_count"]),
                }
                for row in self._conn.execute(
                    """
                    SELECT i.*, COUNT(t.turn_id) AS turn_count
                    FROM conversation_index i
                    JOIN conversation_turns t
                      ON t.conversation_id = i.conversation_id
                     AND t.journal_id = i.journal_id
                    WHERE i.journal_id = ?
                    GROUP BY i.conversation_id, i.journal_id
                    ORDER BY i.updated_at DESC
                    LIMIT ?
                    """,
                    (journal_id, max(1, min(int(limit), 500))),
                )
            ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_conversation_db = os.getenv(
    "CONVERSATION_DB_PATH", str(ROOT / "data" / "conversations.db")
)
conversation_store = ConversationStore(
    _conversation_db,
    ttl_seconds=0,
    max_sessions=0,
    max_turns=10,
)


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
    canonical_continuation = dict(evidence.get("continuation") or {})
    continuation = {
        "has_more": bool(
            canonical_continuation.get("has_more") or sql.get("has_more")
        ),
        "next_offset": canonical_continuation.get(
            "next_offset", sql.get("next_offset")
        ),
        "operation_id": canonical_continuation.get(
            "operation_id", sql.get("continuation_operation_id")
        ),
        "shown_count": int(
            canonical_continuation.get("shown_count")
            or sql.get("shown_count")
            or len(items)
        ),
        "total_count": int(
            canonical_continuation.get("total_count")
            or sql.get("total_count")
            or len(items)
        ),
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
        operation_results=list(evidence.get("operation_results") or []),
        coverage_report=dict(evidence.get("coverage_report") or {}),
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
