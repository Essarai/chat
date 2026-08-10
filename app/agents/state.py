from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class JournalState(TypedDict, total=False):
    question: str
    question_raw: str  # original user text before follow-up rewrite
    history: List[Dict[str, str]]
    followup_intent: Dict[str, Any]
    turn_intent: Dict[str, Any]
    previous_turn: Dict[str, Any]
    top_k: int
    journal_id: str  # ZDXBNXB | ZDXBRWB — selects physically isolated corpus

    # stages: init | extracted | routed | retrieving | synthesizing | done
    stage: str

    # extract
    entities: Dict[str, Any]

    # query understanding (Intent Schema)
    intent: Dict[str, Any]

    # router
    route: Dict[str, Any]
    intents: List[str]
    route_reason: str
    query_plan: Dict[str, Any]  # lightweight plan for simple / tool hints
    analysis_plan: Dict[str, Any]  # analysis subgoals (answer-side), not sql_ops
    operation_results: List[Dict[str, Any]]
    coverage_report: Dict[str, Any]
    result_set: Dict[str, Any]

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
