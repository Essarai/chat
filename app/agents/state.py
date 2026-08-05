from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class JournalState(TypedDict, total=False):
    question: str
    history: List[Dict[str, str]]
    top_k: int

    # understand
    entities: Dict[str, Any]

    # router
    intents: List[str]
    route_reason: str

    # agent evidence
    sql_evidence: Dict[str, Any]
    kg_evidence: Dict[str, Any]
    rag_evidence: Dict[str, Any]

    # merge + answer
    evidence_text: str
    citations: List[Dict[str, Any]]
    answer: str
    errors: List[str]
