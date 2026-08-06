from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict

from app.agents.specialists.kg_agent import kg_agent_node
from app.agents.specialists.rag_agent import rag_agent_node
from app.agents.specialists.sql_agent import sql_agent_node
from app.agents.state import JournalState

_AGENT_FNS: Dict[str, Callable[[JournalState], Dict[str, Any]]] = {
    "sql": sql_agent_node,
    "kg": kg_agent_node,
    "rag": rag_agent_node,
}


def dispatch_node(state: JournalState) -> Dict[str, Any]:
    """Controller-driven parallel dispatch of specialist agents."""
    plan = state.get("query_plan") or {}
    intents = state.get("intents") or plan.get("sources") or ["rag"]
    selected = [i for i in intents if i in _AGENT_FNS]
    if not selected:
        selected = ["rag"]

    updates: Dict[str, Any] = {"stage": "retrieved"}
    errors = list(state.get("errors") or [])

    def _run(name: str) -> Dict[str, Any]:
        return _AGENT_FNS[name](state)

    if len(selected) == 1:
        part = _run(selected[0])
        updates.update(part or {})
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = {pool.submit(_run, name): name for name in selected}
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    part = fut.result()
                    if part:
                        if "entities" in part and "entities" in updates:
                            merged = dict(updates["entities"])
                            merged.update(part["entities"] or {})
                            part = dict(part)
                            part["entities"] = merged
                        if "errors" in part:
                            errors.extend(part.get("errors") or [])
                            part = {k: v for k, v in part.items() if k != "errors"}
                        updates.update(part)
                except Exception as e:
                    errors.append(f"{name}: {e}")

    if errors:
        updates["errors"] = errors
    return updates
