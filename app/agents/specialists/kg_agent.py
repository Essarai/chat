from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents.state import JournalState
from app.capabilities import kg_capability


def run_kg_agent(
    question: str,
    entities: Dict[str, Any] | None = None,
    last_dois: Optional[List[str]] = None,
    query_plan: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    result = kg_capability.invoke(
        "execute_plan",
        question=question,
        entities=entities,
        plan=query_plan or {},
        last_dois=last_dois,
    )
    if not result.get("ok"):
        return {"error": result.get("error"), "source": "kg_capability"}
    return result.get("data") or {}


def kg_agent_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    plan = state.get("query_plan") or {}
    if "kg" not in intents and "kg" not in (plan.get("sources") or []):
        return {}
    try:
        last_dois = (state.get("entities") or {}).get("dois") or []
        data = run_kg_agent(
            state.get("question") or "",
            state.get("entities"),
            last_dois,
            plan,
        )
        return {"kg_evidence": data}
    except Exception as e:
        errors = list(state.get("errors") or [])
        errors.append(f"kg_agent: {e}")
        return {"kg_evidence": {"error": str(e)}, "errors": errors}
