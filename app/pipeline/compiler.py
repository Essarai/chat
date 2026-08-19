"""Compile one semantic intent into one canonical, executable query plan.

The compiler deliberately contains no natural-language pattern matching.  A
semantic router (or the deterministic conversation resolver) supplies slots;
this module is the single authority that turns those slots into operations.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

from app.agents.operation_contracts import CONTRACTS, OperationSpec


KG_OPERATIONS = {"keyword_ego", "author_ego", "paper_neighborhood"}
RAG_OPERATIONS = {"semantic_search"}


def _operation_source(operation: str) -> str:
    if operation in KG_OPERATIONS:
        return "kg"
    if operation in RAG_OPERATIONS:
        return "rag"
    if operation in CONTRACTS:
        return "sql"
    return "unsupported"


def _dedupe(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


class PlanCompiler:
    """Closed policy mapping semantic slots to the capability registry."""

    FIELD_EVOLUTION_OPERATIONS = [
        "yearly_counts",
        "top_keywords",
        "keyword_growth",
        "topic_period_compare",
        "representative_papers_by_topic",
    ]

    def compile(
        self,
        intent: Dict[str, Any],
        previous_turn: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        intent = dict(intent or {})
        if intent.get("action") == "clarify" or intent.get("needs_clarification"):
            operation_types = ["clarification"]
            plan = self._base_plan(intent, operation_types)
            plan["focus"] = str(
                intent.get("clarification")
                or "请补充需要查询的具体对象。"
            )
            plan["operations"] = self._specifications(
                operation_types, plan, intent
            )
            self._attach_compatibility_indexes(plan)
            return plan
        if intent.get("action") == "continue":
            continuation = self._compile_continuation(intent, previous_turn)
            if continuation is not None:
                return continuation

        operation_types = self._requested_operations(intent)
        if not operation_types:
            operation_types = self._policy_operations(intent)
        operation_types = self._ensure_goal_coverage(intent, operation_types)
        operation_types = _dedupe(operation_types or ["semantic_search"])

        plan = self._base_plan(intent, operation_types)
        operation_types = self._complete_dependencies(operation_types, plan)
        plan["task"] = self._task_name(operation_types, intent)
        plan["main_task"] = plan["task"]
        plan["operations"] = self._specifications(operation_types, plan, intent)
        self._attach_compatibility_indexes(plan)
        return plan

    def clarification_plan(
        self,
        intent: Dict[str, Any],
        message: str,
        reason: str,
    ) -> Dict[str, Any]:
        revised = dict(intent or {})
        revised.update(
            {
                "action": "clarify",
                "operation": "search",
                "requested_operations": ["clarification"],
                "clarification": message,
                "source": revised.get("source") or "validator",
            }
        )
        plan = self.compile(revised)
        plan["focus"] = message
        plan["clarification_reason"] = reason
        return plan

    @staticmethod
    def entities(intent: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
        time_range = dict(intent.get("time_range") or {})
        entities: Dict[str, Any] = {
            "year_start": plan.get("year_start", time_range.get("start")),
            "year_end": plan.get("year_end", time_range.get("end")),
            "keywords": list(plan.get("keywords") or []),
        }
        for key in (
            "author_name",
            "author_name_b",
            "institution",
            "author_ids",
            "dois",
            "targets",
            "paper_authors",
        ):
            value = plan.get(key)
            if value not in (None, [], ""):
                entities[key] = value
        if entities.get("author_name") and entities.get("author_name_b"):
            entities["author_names"] = [
                entities["author_name"],
                entities["author_name_b"],
            ]
        return entities

    @staticmethod
    def _requested_operations(intent: Dict[str, Any]) -> List[str]:
        requested = intent.get("requested_operations") or []
        return _dedupe(
            item.get("type") if isinstance(item, dict) else str(item)
            for item in requested
        )

    @staticmethod
    def _complete_dependencies(
        operation_types: List[str],
        plan: Dict[str, Any],
    ) -> List[str]:
        """Insert only capability prerequisites, never new semantic goals."""
        operations = list(operation_types)

        def insert_before(target: str, producer: str) -> None:
            if target not in operations or producer in operations:
                return
            operations.insert(operations.index(target), producer)

        if not plan.get("keywords"):
            for target in (
                "representative_papers_by_topic",
                "representative_authors_by_topic",
                "papers_by_top_keywords",
            ):
                insert_before(target, "keyword_growth")
        if not plan.get("author_ids") and not plan.get("author_name"):
            for target in (
                "papers_for_authors",
                "author_direction_evolution",
                "author_collaborators",
            ):
                insert_before(target, "top_authors")
        if not plan.get("institutions") and not plan.get("institution"):
            for target in (
                "institution_network",
                "representative_papers_by_institution",
            ):
                insert_before(target, "top_institutions")
        if "submission_guidance" in operations:
            insert_before("submission_guidance", "submission_fit")
        if (
            "coauthored_papers" in operations
            and not (plan.get("author_name") and plan.get("author_name_b"))
        ):
            insert_before("coauthored_papers", "author_network")
        return _dedupe(operations)

    def _ensure_goal_coverage(
        self,
        intent: Dict[str, Any],
        operation_types: List[str],
    ) -> List[str]:
        """Add the minimum evidence goals implied by the Intent Schema.

        This operates on semantic enums and slots only; it never re-reads the
        user's natural-language question.
        """
        requested = list(operation_types)
        entity = str(intent.get("entity") or "journal")
        operation = str(intent.get("operation") or "search")
        metric = str(intent.get("metric") or "")
        goal = str(intent.get("goal") or "research_analysis")
        topics = list(intent.get("topics") or intent.get("topic") or [])

        required: List[str] = []
        if goal == "refuse":
            required = ["unsupported_citations"]
        elif goal == "submission_fit":
            required = ["submission_fit", "submission_guidance"]
        elif entity == "journal" and operation in {"trend", "compare"}:
            if metric == "keyword_freq":
                required = list(self.FIELD_EVOLUTION_OPERATIONS)
            elif metric == "publication_count":
                required = ["yearly_counts", "yoy_growth"]
        elif entity == "topic" and operation in {"trend", "compare"}:
            required = (
                ["topic_keyword_counts", "topic_yearly"]
                if topics
                else list(self.FIELD_EVOLUTION_OPERATIONS)
            )
        elif entity == "author" and operation == "rank":
            required = ["top_authors"]
        elif "top_authors" in requested and operation == "rank":
            required = ["top_authors"]
        elif entity == "institution" and operation == "rank":
            required = ["top_institutions"]
        elif "top_institutions" in requested and operation == "rank":
            required = ["top_institutions"]
        elif entity == "author" and operation == "profile":
            required = ["author_profile"]
        elif entity == "topic" and operation == "coverage":
            required = ["topic_coverage"]

        # Required goals lead; optional model-selected operations remain only
        # when they are registered and can add evidence rather than replace it.
        return _dedupe(required + requested)

    def _policy_operations(self, intent: Dict[str, Any]) -> List[str]:
        """Map an already-understood intent to operations.

        This is a small capability policy table, not a second language router.
        """
        if intent.get("action") == "clarify" or intent.get("clarification"):
            return ["clarification"]
        goal = str(intent.get("goal") or "research_analysis")
        if goal == "refuse":
            return ["unsupported_citations"]

        entity = str(intent.get("entity") or "journal")
        operation = str(intent.get("operation") or "search")
        metric = str(intent.get("metric") or "")
        has_topics = bool(intent.get("topics") or intent.get("topic"))

        if goal == "submission_fit" or operation in {"coverage", "recommend"}:
            return ["submission_fit", "topic_yearly", "topic_papers", "submission_guidance"]
        if entity == "author":
            if intent.get("author_name") and intent.get("author_name_b"):
                return ["coauthored_papers"]
            if operation == "rank":
                return ["top_authors"]
            if operation == "profile":
                return ["author_profile", "author_papers", "author_topic_summary"]
            if metric == "collaboration" or operation == "compare":
                return ["author_network", "coauthored_papers"]
            return ["author_profile"] if intent.get("author_name") else ["top_authors"]
        if entity == "institution":
            if operation == "rank":
                return ["top_institutions"]
            if metric == "collaboration" or operation == "compare":
                return ["top_institutions", "institution_network", "representative_papers_by_institution"]
            return ["institution_authors"] if intent.get("institution") else ["top_institutions"]
        if entity == "topic":
            if operation == "rank":
                return ["top_keywords", "keyword_growth"]
            if operation in {"trend", "compare"}:
                return (
                    ["topic_keyword_counts", "topic_yearly", "representative_papers_by_topic"]
                    if has_topics
                    else list(self.FIELD_EVOLUTION_OPERATIONS)
                )
            if operation in {"summarize", "search"} and has_topics:
                return ["topic_papers", "topic_yearly", "authors_by_keyword"]
            return ["semantic_search"]
        if entity == "paper":
            return ["semantic_search"]

        # Journal-level trends and comparisons are corpus-wide.  In
        # particular, no made-up topic is required to compare field changes.
        if operation == "trend" and metric == "publication_count":
            return ["yearly_counts", "yoy_growth"]
        if operation in {"trend", "compare"} and metric in {"keyword_freq", "coverage"}:
            return list(self.FIELD_EVOLUTION_OPERATIONS)
        if operation == "summarize":
            return ["journal_overview"]
        if operation == "rank":
            return ["top_keywords", "keyword_growth"]
        return ["semantic_search"]

    @staticmethod
    def _time_values(intent: Dict[str, Any]) -> tuple[Any, Any]:
        time_range = dict(intent.get("time_range") or {})
        start = time_range.get("start", intent.get("year_start"))
        end = time_range.get("end", intent.get("year_end"))
        last_n = time_range.get("last_n")
        if start is None and last_n is not None:
            from datetime import datetime

            try:
                count = max(1, min(int(last_n), 50))
                end = int(end or datetime.now().year)
                start = end - count + 1
            except (TypeError, ValueError):
                pass
        return start, end

    def _base_plan(
        self,
        intent: Dict[str, Any],
        operation_types: List[str],
    ) -> Dict[str, Any]:
        year_start, year_end = self._time_values(intent)
        topics = _dedupe(
            intent.get("topics")
            or intent.get("topic")
            or intent.get("keywords")
            or []
        )
        task = self._task_name(operation_types, intent)
        plan: Dict[str, Any] = {
            "schema_version": "unified-query-plan/v1",
            "pipeline_version": "unified-pipeline/v1",
            "task": task,
            "main_task": task,
            "action": str(intent.get("action") or "query"),
            "focus": str(intent.get("clarification") or intent.get("goal_description") or ""),
            "plan_origin": str(intent.get("source") or "semantic"),
            "year_start": year_start,
            "year_end": year_end,
            "keywords": topics,
            "top_n": intent.get("top_n"),
            "author_name": intent.get("author_name"),
            "author_name_b": intent.get("author_name_b"),
            "institution": intent.get("institution"),
            "author_ids": list(intent.get("author_ids") or []),
            "dois": list(intent.get("dois") or []),
            "targets": deepcopy(intent.get("targets") or []),
            "paper_authors": deepcopy(intent.get("paper_authors") or []),
            "source_record_count": intent.get("source_record_count"),
            "offset": int(intent.get("offset") or 0),
            "max_items": int(intent.get("max_items") or 100),
            "max_chars": int(intent.get("max_chars") or 24000),
            "presentation": deepcopy(intent.get("presentation") or {}),
            "constraints": {
                "year_start": year_start,
                "year_end": year_end,
                "keywords": topics,
            },
        }
        return plan

    @staticmethod
    def _task_name(operation_types: List[str], intent: Dict[str, Any]) -> str:
        operations = set(operation_types)
        if operations == {"yearly_counts", "yoy_growth"}:
            return "yearly_growth"
        if "submission_fit" in operations:
            return "submission_fit"
        if {
            "top_keywords",
            "keyword_growth",
            "topic_period_compare",
        }.issubset(operations):
            return "field_evolution"
        if len(operation_types) == 1:
            return operation_types[0]
        return str(intent.get("task_hint") or "multi_operation")

    @staticmethod
    def _dependencies(operation: str, ordered: List[str]) -> List[str]:
        dependencies: Dict[str, List[str]] = {
            "papers_for_authors": ["top_authors"],
            "author_direction_evolution": ["top_authors"],
            "author_collaborators": ["top_authors"],
            "topic_yearly": ["top_keywords", "keyword_growth"],
            "topic_papers": ["top_keywords", "keyword_growth"],
            "papers_by_top_keywords": ["top_keywords", "keyword_growth"],
            "representative_papers_by_topic": ["top_keywords", "keyword_growth"],
            "representative_authors_by_topic": ["top_keywords", "keyword_growth"],
            "institution_network": ["top_institutions"],
            "representative_papers_by_institution": ["top_institutions"],
            "submission_guidance": ["submission_fit"],
            "coauthored_papers": ["author_network"],
        }
        result: List[str] = []
        for candidate in dependencies.get(operation, []):
            if candidate in ordered and ordered.index(candidate) < ordered.index(operation):
                result.append(f"op_{ordered.index(candidate) + 1}_{candidate}")
        return result

    def _specifications(
        self,
        operation_types: List[str],
        plan: Dict[str, Any],
        intent: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        params = {
            "year_start": plan.get("year_start"),
            "year_end": plan.get("year_end"),
            "keywords": list(plan.get("keywords") or []),
            "top_n": plan.get("top_n"),
            "author_name": plan.get("author_name"),
            "author_name_b": plan.get("author_name_b"),
            "author_ids": list(plan.get("author_ids") or []),
            "institution": plan.get("institution"),
            "dois": list(plan.get("dois") or []),
            "targets": deepcopy(plan.get("targets") or []),
            "paper_authors": deepcopy(plan.get("paper_authors") or []),
            "source_record_count": plan.get("source_record_count"),
            "offset": plan.get("offset"),
            "max_items": plan.get("max_items"),
            "max_chars": plan.get("max_chars"),
        }
        clean_params = {
            key: value for key, value in params.items() if value not in (None, [], "")
        }
        specs: List[Dict[str, Any]] = []
        for index, operation in enumerate(operation_types, 1):
            contract = dict(
                CONTRACTS.get(operation)
                or {
                    "kind": "unsupported",
                    "reason": f"未知 operation: {operation}",
                }
            )
            specs.append(
                OperationSpec(
                    id=f"op_{index}_{operation}",
                    type=operation,
                    source=_operation_source(operation),
                    target={
                        "entity": intent.get("entity")
                        or intent.get("target_entity")
                        or "journal"
                    },
                    params=deepcopy(clean_params),
                    depends_on=self._dependencies(operation, operation_types),
                    required=True,
                    evidence_contract=contract,
                ).to_dict()
            )
        return specs

    @staticmethod
    def _attach_compatibility_indexes(plan: Dict[str, Any]) -> None:
        operations = list(plan.get("operations") or [])
        plan["sources"] = _dedupe(op.get("source") for op in operations)
        plan["sql_ops"] = [
            op["type"] for op in operations if op.get("source") == "sql"
        ]
        plan["kg_ops"] = [
            op["type"] for op in operations if op.get("source") == "kg"
        ]
        plan["rag_queries"] = list(plan.get("keywords") or [])

    def _compile_continuation(
        self,
        intent: Dict[str, Any],
        previous_turn: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        previous = dict(previous_turn or intent.get("previous_turn") or {})
        previous_plan = dict(
            intent.get("previous_plan")
            or intent.get("previous_query_plan")
            or previous.get("query_plan")
            or {}
        )
        continuation = dict(intent.get("continuation") or previous.get("continuation") or {})
        operation_id = str(continuation.get("operation_id") or "")
        candidates = [
            dict(op)
            for op in previous_plan.get("operations") or []
            if isinstance(op, dict)
        ]
        selected = next(
            (op for op in candidates if str(op.get("id")) == operation_id),
            candidates[-1] if len(candidates) == 1 else None,
        )
        if not selected:
            return None

        operation = str(selected.get("type") or "")
        inherited_intent = {
            **intent,
            "requested_operations": [operation],
            "time_range": {
                "start": previous_plan.get("year_start"),
                "end": previous_plan.get("year_end"),
            },
            "topics": list(previous_plan.get("keywords") or []),
            "author_name": previous_plan.get("author_name"),
            "author_name_b": previous_plan.get("author_name_b"),
            "institution": previous_plan.get("institution"),
            "author_ids": list(previous_plan.get("author_ids") or []),
            "dois": list(previous_plan.get("dois") or []),
            "targets": deepcopy(previous_plan.get("targets") or []),
            "offset": int(continuation.get("next_offset") or previous_plan.get("offset") or 0),
            "source": "conversation",
        }
        plan = self._base_plan(inherited_intent, [operation])
        plan["action"] = "continue"
        plan["continuation"] = continuation
        plan["operations"] = self._specifications([operation], plan, inherited_intent)
        self._attach_compatibility_indexes(plan)
        return plan


__all__ = ["PlanCompiler", "KG_OPERATIONS", "RAG_OPERATIONS"]
