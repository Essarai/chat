from __future__ import annotations

from typing import Any, Dict, Optional

from app.agents.state import JournalState
from app.capabilities import sql_capability


def run_sql_agent(
    question: str,
    entities: Dict[str, Any] | None = None,
    query_plan: Dict[str, Any] | None = None,
    journal_id: Optional[str] = None,
) -> Dict[str, Any]:
    plan = query_plan or {}
    kwargs = dict(
        question=question,
        entities=entities,
        plan=plan,
        journal_id=journal_id,
    )
    if plan.get("sql_ops") or plan.get("task") in {
        "yearly_growth",
        "topic_evolution",
        "keyword_collab",
        "top_directions_with_papers",
        "top_teams",
        "journal_overview",
        "author_profile",
        "keyword_authors",
        "unsupported_citations",
        "hotspot_compare",
        "topic_coverage",
        "top_institutions",
    } or "top_keywords" in (plan.get("sql_ops") or []):
        result = sql_capability.invoke("execute_plan", **kwargs)
    else:
        result = sql_capability.invoke("legacy", **kwargs)
    if not result.get("ok"):
        return {"error": result.get("error"), "source": "sql_capability"}
    return result.get("data") or {}


def sql_agent_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    plan = state.get("query_plan") or {}
    if "sql" not in intents and "sql" not in (plan.get("sources") or []):
        return {}
    try:
        data = run_sql_agent(
            state.get("question") or "",
            state.get("entities"),
            plan,
            journal_id=state.get("journal_id"),
        )
        return {"sql_evidence": data}
    except Exception as e:
        errors = list(state.get("errors") or [])
        errors.append(f"sql_agent: {e}")
        return {"sql_evidence": {"error": str(e)}, "errors": errors}
