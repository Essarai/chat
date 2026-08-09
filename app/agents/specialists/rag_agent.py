from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents.state import JournalState
from app.capabilities import rag_capability


def _year_window_from_state(
    state: JournalState,
    plan: Dict[str, Any] | None = None,
) -> tuple[Optional[int], Optional[int]]:
    plan = plan or state.get("query_plan") or {}
    ents = state.get("entities") or {}
    sql = state.get("sql_evidence") or {}
    y0 = plan.get("year_start") or ents.get("year_start") or sql.get("start_year")
    y1 = plan.get("year_end") or ents.get("year_end") or sql.get("end_year")
    try:
        y0 = int(y0) if y0 is not None else None
    except (TypeError, ValueError):
        y0 = None
    try:
        y1 = int(y1) if y1 is not None else None
    except (TypeError, ValueError):
        y1 = None
    return y0, y1


def run_rag_agent(
    question: str,
    top_k: Optional[int] = None,
    queries: Optional[List[str]] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    journal_id: Optional[str] = None,
) -> Dict[str, Any]:
    result = rag_capability.invoke(
        "semantic_search",
        question=question,
        queries=queries,
        top_k=top_k,
        year_start=year_start,
        year_end=year_end,
        journal_id=journal_id,
    )
    if not result.get("ok"):
        return {
            "error": result.get("error"),
            "hits": [],
            "citations": [],
            "source": "rag_capability",
        }
    return result.get("data") or {"hits": [], "citations": []}


def rag_agent_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    plan = state.get("query_plan") or {}
    if "rag" not in intents and "rag" not in (plan.get("sources") or []):
        return {}
    try:
        queries = list(plan.get("rag_queries") or [])
        if not queries:
            for k in plan.get("keywords") or (state.get("entities") or {}).get("keywords") or []:
                if k:
                    queries.append(str(k))
        sql = state.get("sql_evidence") or {}
        for d in sql.get("directions") or []:
            kw = d.get("keyword")
            if kw and str(kw) not in queries:
                queries.append(str(kw))

        y0, y1 = _year_window_from_state(state, plan)
        data = run_rag_agent(
            state.get("question") or "",
            top_k=state.get("top_k"),
            queries=queries or None,
            year_start=y0,
            year_end=y1,
            journal_id=state.get("journal_id"),
        )
        entities = dict(state.get("entities") or {})
        entities["dois"] = [h.get("doi") for h in data.get("hits") or [] if h.get("doi")]
        return {"rag_evidence": data, "entities": entities}
    except Exception as e:
        errors = list(state.get("errors") or [])
        errors.append(f"rag_agent: {e}")
        return {
            "rag_evidence": {"error": str(e), "hits": [], "citations": []},
            "errors": errors,
        }
