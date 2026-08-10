"""Simple path: single-source specialist invocation + evidence sufficiency gate."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.agents.specialists.kg_agent import run_kg_agent
from app.agents.specialists.rag_agent import run_rag_agent
from app.agents.specialists.sql_agent import run_sql_agent
from app.agents.state import JournalState
from app.config import bind_corpus


def _summarize(source: str, data: Dict[str, Any], limit: int = 800) -> str:
    if not data:
        return f"{source}: 空结果"
    if data.get("error"):
        return f"{source}错误: {data.get('error')}"
    # compact json preview
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = str(data)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _evidence_enough(source: str, data: Dict[str, Any]) -> bool:
    if not data or data.get("error"):
        return False
    if source == "sql":
        if data.get("scope") == "unsupported" or data.get("task") == "unsupported_citations":
            return True
        if data.get("scope") in {
            "hotspot_compare",
            "topic_coverage",
            "submission_fit",
            "top_institutions",
            "top_authors",
            "authors_papers",
            "clarification",
            "topic_stats",
            "top_teams",
        }:
            return True
        if data.get("task") in {
            "topic_evolution",
            "journal_overview",
            "top_directions_with_papers",
            "top_teams",
        }:
            return True
        if data.get("yearly") or data.get("yoy") or data.get("authors"):
            return True
        if data.get("institutions"):
            return True
        if data.get("scope") == "author":
            # Found or not — definitive for author questions; do not escalate to RAG
            return True
        if data.get("scope") == "keyword_authors" and data.get("authors") is not None:
            return True
        if data.get("keywords") or data.get("papers") or data.get("periods"):
            return True
        return bool(data.get("task") or data.get("scope"))
    if source == "kg":
        return bool(
            data.get("collaborators")
            or data.get("authors")
            or data.get("institutions")
            or data.get("keyword_papers")
            or data.get("paper")
            or data.get("nodes")
        )
    if source == "rag":
        return bool(data.get("hits"))
    return False


def simple_exec_node(state: JournalState) -> Dict[str, Any]:
    route = dict(state.get("route") or {})
    if route.get("complexity") != "simple":
        return {"stage": "retrieving"}

    journal_id = state.get("journal_id")
    if journal_id:
        bind_corpus(journal_id)

    source = route.get("suggested_source") or "rag"
    question = state.get("question") or ""
    entities = state.get("entities") or {}
    plan = state.get("query_plan") or {}
    bundle: List[Dict[str, Any]] = list(state.get("evidence_bundle") or [])
    errors = list(state.get("errors") or [])
    updates: Dict[str, Any] = {"stage": "retrieving", "intents": [source]}

    try:
        if plan.get("task") == "clarification":
            data = {
                "scope": "clarification",
                "task": "clarification",
                "message": plan.get("focus") or "请补充需要查询的对象。",
                "source": "conversation",
            }
            updates["sql_evidence"] = data
            op = "clarification"
        elif source == "sql":
            data = run_sql_agent(question, entities, plan, journal_id=journal_id)
            updates["sql_evidence"] = data
            op = ",".join(plan.get("sql_ops") or ["legacy"])
        elif source == "kg":
            data = run_kg_agent(
                question,
                entities,
                (entities.get("dois") or []),
                plan,
                journal_id=journal_id,
            )
            updates["kg_evidence"] = data
            op = ",".join(plan.get("kg_ops") or ["auto"])
        else:
            data = run_rag_agent(
                question,
                top_k=state.get("top_k"),
                queries=plan.get("rag_queries") or [question],
                year_start=plan.get("year_start") or entities.get("year_start"),
                year_end=plan.get("year_end") or entities.get("year_end"),
                journal_id=journal_id,
            )
            updates["rag_evidence"] = data
            ents = dict(entities)
            ents["dois"] = [h.get("doi") for h in data.get("hits") or [] if h.get("doi")]
            updates["entities"] = ents
            op = "semantic_search"
    except Exception as e:
        errors.append(f"simple_exec:{source}: {e}")
        data = {"error": str(e)}
        updates[f"{source}_evidence"] = data
        op = "error"

    bundle.append(
        {
            "step": len(bundle) + 1,
            "source": source,
            "operation": op,
            "summary": _summarize(source, data if isinstance(data, dict) else {}),
        }
    )

    # QueryPlan operations, not payload shape, are the evidence contract.
    from app.agents.coverage import assess_operations

    preview_state: JournalState = {**state, **updates}  # type: ignore
    preview_state["query_plan"] = plan
    preview = assess_operations(preview_state)
    updates.update({
        "operation_results": preview.get("operation_results") or [],
        "coverage_report": preview.get("coverage_report") or {},
        "result_set": preview.get("result_set") or {},
    })
    op_results = preview.get("operation_results") or []
    # partial/unsupported results are final evidence states and go to the
    # controlled synthesizer. Only execution errors warrant another source.
    enough = bool(op_results) and not any(r.get("status") == "error" for r in op_results)
    if not enough:
        route["escalated"] = True
        route["complexity"] = "complex"
        route["reason"] = (route.get("reason") or "") + "; 简单路径证据不足→升级ReAct"
        updates["route"] = route
        updates["route_reason"] = f"路由:complex(escalated); {route.get('reason')}"
    else:
        updates["route"] = route

    updates["evidence_bundle"] = bundle
    if errors:
        updates["errors"] = errors
    return updates


def route_after_simple(state: JournalState) -> str:
    """Conditional edge: escalate → react, else → synthesize."""
    route = state.get("route") or {}
    if route.get("escalated") or route.get("complexity") == "complex":
        # if we started simple and escalated, need react; if still simple, synth
        if route.get("escalated"):
            return "react"
        if route.get("complexity") == "complex":
            return "react"
    if route.get("complexity") == "simple" and not route.get("escalated"):
        return "synthesize"
    return "react"
