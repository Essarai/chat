"""Controller Agent: Router → Simple|ReAct → Synthesizer."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph

from app.agents.fusion import fuse_evidence_node, fusion_node
from app.agents.orchestrator_react import react_controller_node
from app.agents.router_v2 import extract_node, router_node
from app.agents.simple_exec import simple_exec_node
from app.agents.state import JournalState
from app.config import get_settings


def init_state(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    last_dois: Optional[List[str]] = None,
) -> JournalState:
    settings = get_settings()
    return {
        "question": question,
        "history": history or [],
        "top_k": top_k or settings.rag_top_k,
        "entities": {"dois": last_dois or []},
        "errors": [],
        "stage": "init",
        "evidence_bundle": [],
        "react_trace": [],
    }


def _route_after_router(state: JournalState) -> str:
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
    g.add_node("router", router_node)
    g.add_node("simple_exec", simple_exec_node)
    g.add_node("react", react_controller_node)
    g.add_node("synthesize", fusion_node)

    g.add_edge(START, "extract")
    g.add_edge("extract", "router")
    g.add_conditional_edges(
        "router",
        _route_after_router,
        {"simple": "simple_exec", "react": "react"},
    )
    g.add_conditional_edges(
        "simple_exec",
        _route_after_simple,
        {"synthesize": "synthesize", "react": "react"},
    )
    g.add_edge("react", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def build_prepare_graph():
    """Through evidence formatting only — answer streamed separately."""
    g = StateGraph(JournalState)
    g.add_node("extract", extract_node)
    g.add_node("router", router_node)
    g.add_node("simple_exec", simple_exec_node)
    g.add_node("react", react_controller_node)
    g.add_node("fuse_evidence", fuse_evidence_node)

    g.add_edge(START, "extract")
    g.add_edge("extract", "router")
    g.add_conditional_edges(
        "router",
        _route_after_router,
        {"simple": "simple_exec", "react": "react"},
    )
    g.add_conditional_edges(
        "simple_exec",
        _route_after_simple,
        {"synthesize": "fuse_evidence", "react": "react"},
    )
    g.add_edge("react", "fuse_evidence")
    g.add_edge("fuse_evidence", END)
    return g.compile()


@lru_cache()
def get_compiled_graph():
    return build_full_graph()


@lru_cache()
def get_prepare_graph():
    return build_prepare_graph()


def run_journal_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    last_dois: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return get_compiled_graph().invoke(init_state(question, history, top_k, last_dois))


def prepare_journal_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    last_dois: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return get_prepare_graph().invoke(init_state(question, history, top_k, last_dois))


__all__ = [
    "init_state",
    "build_full_graph",
    "build_prepare_graph",
    "get_compiled_graph",
    "get_prepare_graph",
    "run_journal_agent",
    "prepare_journal_agent",
]
