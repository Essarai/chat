"""Controller Agent: Router → AnalysisPlanner → Simple|ReAct → Synthesizer."""

from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from typing import Any, Dict, List, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.agents.analysis_planner import analysis_planner_node
from app.agents.coverage import coverage_node
from app.agents.followup import (
    classify_followup_question,
    looks_like_followup,
    resolve_followup_question,
)
from app.agents.fusion import fuse_evidence_node, fusion_node
from app.agents.orchestrator_react import react_controller_node
from app.agents.query_understand import query_understand_node
from app.agents.router_v2 import extract_node, router_node
from app.agents.simple_exec import simple_exec_node
from app.agents.state import JournalState
from app.agents.turn_understand import understand_contextual_turn
from app.config import ROOT, bind_corpus, get_settings


def init_state(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    last_dois: Optional[List[str]] = None,
    journal_id: Optional[str] = None,
    previous_turn: Optional[Dict[str, Any]] = None,
) -> JournalState:
    settings = bind_corpus(journal_id or get_settings().journal_id)
    hist = history or []
    raw = (question or "").strip()
    turn_intent, locked_plan = understand_contextual_turn(raw, previous_turn)
    if turn_intent is None:
        if looks_like_followup(raw, hist):
            followup_intent = classify_followup_question(raw, hist)
            resolved = resolve_followup_question(raw, hist, analysis=followup_intent)
        else:
            followup_intent = {
                "kind": "new_question",
                "action": "query",
                "confidence": 0.9,
                "needs_clarification": False,
                "source": "standalone",
                "rewritten_question": raw,
            }
            resolved = raw
        turn_intent = followup_intent
    else:
        followup_intent = turn_intent
        resolved = raw
    return {
        "question": resolved,
        "question_raw": raw,
        "history": hist,
        "followup_intent": followup_intent,
        "turn_intent": turn_intent,
        "previous_turn": previous_turn or {},
        "top_k": top_k or settings.rag_top_k,
        "journal_id": settings.journal_id,
        "entities": {"dois": last_dois or []},
        "intent": {},
        "query_plan": locked_plan or {},
        "analysis_plan": {},
        "route": {},
        "intents": [],
        "route_reason": "",
        "operation_results": [],
        "coverage_report": {},
        "quality_report": {},
        "result_set": {},
        "sql_evidence": {},
        "kg_evidence": {},
        "rag_evidence": {},
        "errors": [],
        "stage": "init",
        "evidence_bundle": [],
        "react_trace": [],
        "goal": "",
        "evidence_text": "",
        "citations": [],
        "answer": "",
    }


def _route_after_analysis(state: JournalState) -> str:
    route = state.get("route") or {}
    if route.get("complexity") == "simple":
        return "simple"
    return "react"


def _route_after_simple(state: JournalState) -> str:
    route = state.get("route") or {}
    if route.get("escalated"):
        return "react"
    return "synthesize"


def build_full_graph():
    g = StateGraph(JournalState)
    g.add_node("extract", extract_node)
    g.add_node("query_understand", query_understand_node)
    g.add_node("router", router_node)
    g.add_node("analysis_planner", analysis_planner_node)
    g.add_node("simple_exec", simple_exec_node)
    g.add_node("react", react_controller_node)
    g.add_node("coverage", coverage_node)
    g.add_node("synthesize", fusion_node)

    g.add_edge(START, "extract")
    g.add_edge("extract", "query_understand")
    g.add_edge("query_understand", "router")
    g.add_edge("router", "analysis_planner")
    g.add_conditional_edges(
        "analysis_planner",
        _route_after_analysis,
        {"simple": "simple_exec", "react": "react"},
    )
    g.add_conditional_edges(
        "simple_exec",
        _route_after_simple,
        {"synthesize": "coverage", "react": "react"},
    )
    g.add_edge("react", "coverage")
    g.add_edge("coverage", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def build_prepare_graph(checkpointer=None):
    """Through evidence formatting only — answer streamed separately."""
    g = StateGraph(JournalState)
    g.add_node("extract", extract_node)
    g.add_node("query_understand", query_understand_node)
    g.add_node("router", router_node)
    g.add_node("analysis_planner", analysis_planner_node)
    g.add_node("simple_exec", simple_exec_node)
    g.add_node("react", react_controller_node)
    g.add_node("coverage", coverage_node)
    g.add_node("fuse_evidence", fuse_evidence_node)

    g.add_edge(START, "extract")
    g.add_edge("extract", "query_understand")
    g.add_edge("query_understand", "router")
    g.add_edge("router", "analysis_planner")
    g.add_conditional_edges(
        "analysis_planner",
        _route_after_analysis,
        {"simple": "simple_exec", "react": "react"},
    )
    g.add_conditional_edges(
        "simple_exec",
        _route_after_simple,
        {"synthesize": "coverage", "react": "react"},
    )
    g.add_edge("react", "coverage")
    g.add_edge("coverage", "fuse_evidence")
    g.add_edge("fuse_evidence", END)
    return g.compile(checkpointer=checkpointer)


@lru_cache()
def get_compiled_graph():
    return build_full_graph()


@lru_cache()
def get_prepare_graph():
    return build_prepare_graph()


@lru_cache()
def get_conversation_checkpointer() -> SqliteSaver:
    db_path = os.getenv(
        "CONVERSATION_DB_PATH", str(ROOT / "data" / "conversations.db")
    )
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


@lru_cache()
def get_persistent_prepare_graph():
    return build_prepare_graph(checkpointer=get_conversation_checkpointer())


def reset_agent_thread(thread_id: str) -> None:
    get_conversation_checkpointer().delete_thread(thread_id)


def run_journal_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    last_dois: Optional[List[str]] = None,
    journal_id: Optional[str] = None,
    previous_turn: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = init_state(question, history, top_k, last_dois, journal_id=journal_id, previous_turn=previous_turn)
    bind_corpus(state["journal_id"])
    return get_compiled_graph().invoke(state)


def prepare_journal_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    last_dois: Optional[List[str]] = None,
    journal_id: Optional[str] = None,
    previous_turn: Optional[Dict[str, Any]] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    state = init_state(question, history, top_k, last_dois, journal_id=journal_id, previous_turn=previous_turn)
    bind_corpus(state["journal_id"])
    if thread_id:
        return get_persistent_prepare_graph().invoke(
            state, {"configurable": {"thread_id": thread_id}}
        )
    return get_prepare_graph().invoke(state)


__all__ = [
    "init_state",
    "build_full_graph",
    "build_prepare_graph",
    "get_compiled_graph",
    "get_prepare_graph",
    "get_persistent_prepare_graph",
    "get_conversation_checkpointer",
    "reset_agent_thread",
    "run_journal_agent",
    "prepare_journal_agent",
]
