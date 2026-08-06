"""Multi-agent journal QA: Controller + Planner + Specialists + Fusion."""

__all__ = ["build_graph", "run_journal_agent", "prepare_journal_agent"]


def __getattr__(name: str):
    if name in {"build_graph", "run_journal_agent", "prepare_journal_agent"}:
        from app.agents.controller import (
            build_full_graph,
            prepare_journal_agent,
            run_journal_agent,
        )

        mapping = {
            "build_graph": build_full_graph,
            "run_journal_agent": run_journal_agent,
            "prepare_journal_agent": prepare_journal_agent,
        }
        return mapping[name]
    raise AttributeError(name)
