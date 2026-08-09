from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class JournalState(TypedDict, total=False):
    question: str
    history: List[Dict[str, str]]
    top_k: int
    journal_id: str  # ZDXBNXB | ZDXBRWB — selects physically isolated corpus

    # stages: init | extracted | routed | retrieving | synthesizing | done
    stage: str

    # extract
    entities: Dict[str, Any]

    # router
    route: Dict[str, Any]
    intents: List[str]
    route_reason: str
    query_plan: Dict[str, Any]  # lightweight plan for simple / tool hints
    analysis_plan: Dict[str, Any]  # analysis subgoals (answer-side), not sql_ops

    # specialist evidence
    sql_evidence: Dict[str, Any]
    kg_evidence: Dict[str, Any]
    rag_evidence: Dict[str, Any]

    # react
    evidence_bundle: List[Dict[str, Any]]
    react_trace: List[Dict[str, Any]]
    goal: str

    # fusion
    evidence_text: str
    citations: List[Dict[str, Any]]
    answer: str
    errors: List[str]
