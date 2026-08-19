"""Unified production pipeline: context -> semantics -> plan -> evidence."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from app.agents.coverage import assess_answer_coverage, assess_quality
from app.agents.generate import finalize_answer, stream_generate
from app.agents.merge import merge_node
from app.config import Settings, bind_corpus, get_settings
from app.pipeline.compiler import PlanCompiler
from app.pipeline.context import ContextResolver
from app.pipeline.executor import DeterministicExecutor
from app.pipeline.semantic import SemanticRouter
from app.pipeline.validator import PlanValidator
from app.services.minimax_chat import MiniMaxChat


class UnifiedProductionPipeline:
    """Production request pipeline without Controller/LangGraph/ReAct state."""

    VERSION = "unified-pipeline/v1"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        chat: Optional[MiniMaxChat] = None,
        context_resolver: Optional[ContextResolver] = None,
        semantic_router: Optional[SemanticRouter] = None,
        compiler: Optional[PlanCompiler] = None,
        validator: Optional[PlanValidator] = None,
        executor: Optional[DeterministicExecutor] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.chat = chat or MiniMaxChat(self.settings)
        self.context_resolver = context_resolver or ContextResolver()
        self.semantic_router = semantic_router or SemanticRouter(self.chat)
        self.compiler = compiler or PlanCompiler()
        self.validator = validator or PlanValidator(self.compiler)
        self.executor = executor or DeterministicExecutor()

    def prepare(
        self,
        question: str,
        top_k: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        last_dois: Optional[List[str]] = None,
        previous_turn: Optional[Dict[str, Any]] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        bind_corpus(self.settings.journal_id)
        started = time.monotonic()
        raw_question = str(question or "").strip()

        contextual = self.context_resolver.resolve(
            raw_question,
            previous_turn,
            last_dois=last_dois,
        )
        if contextual is not None:
            intent = self._normalize_context_intent(contextual)
            intent_source = "context"
        else:
            intent = self.semantic_router.route(
                raw_question,
                history=history or [],
                previous_turn=previous_turn,
            )
            intent_source = str(intent.get("source") or "semantic")

        understood_at = time.monotonic()
        plan = self.compiler.compile(intent, previous_turn)
        plan, validation = self.validator.validate_and_repair(
            plan,
            intent,
            previous_turn,
        )
        if plan.get("task") == "clarification":
            intent = {
                **intent,
                "action": "clarify",
                "clarification": plan.get("focus"),
                "requested_operations": ["clarification"],
            }
        entities = self.compiler.entities(intent, plan)
        planned_at = time.monotonic()
        sources = [
            source for source in plan.get("sources") or []
            if source in {"sql", "kg", "rag"}
        ]

        state: Dict[str, Any] = {
            "pipeline": {
                "name": "unified-production",
                "version": self.VERSION,
                "state_source": "conversation_store",
            },
            "question": raw_question,
            "question_raw": raw_question,
            "history": list(history or []),
            "previous_turn": dict(previous_turn or {}),
            "top_k": top_k or self.settings.rag_top_k,
            "journal_id": self.settings.journal_id,
            "thread_id": thread_id,
            "stage": "planned",
            "intent": intent,
            "turn_intent": {
                "kind": "followup" if intent_source == "context" else "new_question",
                "action": intent.get("action") or "query",
                "source": intent_source,
                "confidence": intent.get("confidence"),
                "target_entity": intent.get("entity") or intent.get("target_entity"),
            },
            "followup_intent": contextual or {},
            "entities": entities,
            "query_plan": plan,
            "analysis_plan": {},
            "validation_report": validation,
            "intents": sources or ["sql"],
            "route": {
                "pipeline": self.VERSION,
                "mode": "deterministic_plan_execution",
                "sources": sources,
            },
            "route_reason": (
                f"{intent_source} semantic intent -> validated canonical plan -> "
                + "+".join(sources or ["sql"])
            ),
            "goal": intent.get("goal") or "research_analysis",
            "errors": [],
        }
        if intent.get("router_error"):
            state["errors"].append(f"semantic_router: {intent['router_error']}")

        self.executor.execute(state)
        retrieved_at = time.monotonic()
        state.update(merge_node(state))
        state["stage"] = "ready_to_generate"
        state["stage_timings_ms"] = {
            "understand": int((understood_at - started) * 1000),
            "plan_validate": int((planned_at - understood_at) * 1000),
            "execute_validate": int((retrieved_at - planned_at) * 1000),
            "prepare_total": int((time.monotonic() - started) * 1000),
        }
        return state

    @staticmethod
    def generate(state: Dict[str, Any]) -> str:
        """Buffer, gate and only then return user-visible answer text."""
        raw = "".join(stream_generate(state))
        answer = finalize_answer(raw, state.get("intents") or [], state)
        state["answer_coverage"] = assess_answer_coverage(
            answer,
            state.get("operation_results") or [],
        )
        state["quality_report"] = assess_quality(answer, state)  # type: ignore[arg-type]
        state["stage"] = "done"
        return answer

    def run(self, question: str, **kwargs: Any) -> tuple[Dict[str, Any], str]:
        state = self.prepare(question, **kwargs)
        return state, self.generate(state)

    @staticmethod
    def _normalize_context_intent(contextual: Dict[str, Any]) -> Dict[str, Any]:
        intent = dict(contextual)
        inherited = dict(intent.get("inherited_constraints") or {})
        year_start = intent.get("year_start", inherited.get("year_start"))
        year_end = intent.get("year_end", inherited.get("year_end"))
        keywords = list(
            intent.get("topics")
            or intent.get("keywords")
            or inherited.get("keywords")
            or []
        )
        if intent.get("needs_clarification"):
            intent.update(
                {
                    "action": "clarify",
                    "entity": intent.get("target_entity") or "journal",
                    "operation": "search",
                    "goal": "research_analysis",
                    "requested_operations": ["clarification"],
                }
            )
        else:
            intent.setdefault("entity", intent.get("target_entity") or "journal")
            intent.setdefault("operation", "search")
            intent.setdefault("goal", "research_analysis")
        intent.update(
            {
                "topics": keywords,
                "time_range": {
                    "start": year_start,
                    "end": year_end,
                    "last_n": None,
                },
                "confidence": float(intent.get("confidence") or 1.0),
                "source": "context",
            }
        )
        return intent


__all__ = ["UnifiedProductionPipeline"]
