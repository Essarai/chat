from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph

from app.agents.dispatch import dispatch_node
from app.agents.generate import generate_node
from app.agents.merge import merge_node
from app.agents.router import router_node
from app.agents.state import JournalState
from app.agents.understand import understand_node
from app.config import get_settings


def _base_nodes(g: StateGraph) -> None:
    g.add_node("understand", understand_node)
    g.add_node("router", router_node)
    g.add_node("dispatch", dispatch_node)
    g.add_node("merge", merge_node)
    g.add_edge(START, "understand")
    g.add_edge("understand", "router")
    g.add_edge("router", "dispatch")
    g.add_edge("dispatch", "merge")


def build_graph():
    g = StateGraph(JournalState)
    _base_nodes(g)
    g.add_node("generate", generate_node)
    g.add_edge("merge", "generate")
    g.add_edge("generate", END)
    return g.compile()


def build_prepare_graph():
    """Run pipeline through merge only — generation is streamed separately."""
    g = StateGraph(JournalState)
    _base_nodes(g)
    g.add_edge("merge", END)
    return g.compile()


@lru_cache()
def get_compiled_graph():
    return build_graph()


@lru_cache()
def get_prepare_graph():
    return build_prepare_graph()


def _init_state(
    question: str,
    history: Optional[List[Dict[str, str]]],
    top_k: Optional[int],
    last_dois: Optional[List[str]],
) -> JournalState:
    settings = get_settings()
    return {
        "question": question,
        "history": history or [],
        "top_k": top_k or settings.rag_top_k,
        "entities": {"dois": last_dois or []},
        "errors": [],
    }


def run_journal_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    last_dois: Optional[List[str]] = None,
) -> Dict[str, Any]:
    graph = get_compiled_graph()
    return graph.invoke(_init_state(question, history, top_k, last_dois))


def prepare_journal_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: Optional[int] = None,
    last_dois: Optional[List[str]] = None,
) -> Dict[str, Any]:
    graph = get_prepare_graph()
    return graph.invoke(_init_state(question, history, top_k, last_dois))
