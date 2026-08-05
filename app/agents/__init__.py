"""LangGraph multi-agent query routing."""

__all__ = ["build_graph", "run_journal_agent"]


def __getattr__(name: str):
    if name in {"build_graph", "run_journal_agent"}:
        from app.agents.graph import build_graph, run_journal_agent

        return {"build_graph": build_graph, "run_journal_agent": run_journal_agent}[name]
    raise AttributeError(name)
