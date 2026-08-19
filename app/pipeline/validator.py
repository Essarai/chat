"""Validation and one bounded repair pass for canonical query plans."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.agents.operation_contracts import CONTRACTS
from app.pipeline.compiler import KG_OPERATIONS, RAG_OPERATIONS, PlanCompiler


@dataclass
class ValidationReport:
    valid: bool
    repaired: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    repair_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "repaired": self.repaired,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "repair_reason": self.repair_reason,
        }


class PlanValidator:
    """Check registry membership, source, dependencies and preconditions."""

    TOPIC_REQUIRED = {
        "authors_by_keyword",
        "institutions_by_keyword",
        "topic_papers",
        "topic_yearly",
        "topic_keyword_counts",
        "topic_coverage",
        "submission_fit",
        "keyword_ego",
    }
    TOPIC_PRODUCIBLE = {
        "representative_papers_by_topic",
        "representative_authors_by_topic",
        "papers_by_top_keywords",
    }
    TOPIC_PRODUCERS = {"top_keywords", "keyword_growth"}
    AUTHOR_REQUIRED = {
        "author_profile",
        "author_topic_summary",
        "author_papers",
        "author_collaborators",
        "author_ego",
    }
    AUTHOR_SET_REQUIRED = {
        "papers_for_authors",
        "author_direction_evolution",
    }
    PAPER_SET_REQUIRED = {
        "present_resultset",
        "paper_set_topic_summary",
        "paper_neighborhood",
    }

    def __init__(self, compiler: Optional[PlanCompiler] = None):
        self.compiler = compiler or PlanCompiler()

    def validate_and_repair(
        self,
        plan: Dict[str, Any],
        intent: Dict[str, Any],
        previous_turn: Optional[Dict[str, Any]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        first = self.validate(plan)
        if first.valid:
            return plan, first.to_dict()

        repaired_intent = self._repair_intent(intent, first.errors)
        if repaired_intent is not None:
            repaired_plan = self.compiler.compile(repaired_intent, previous_turn)
            second = self.validate(repaired_plan)
            second.repaired = True
            second.repair_reason = "; ".join(first.errors)
            if second.valid:
                return repaired_plan, second.to_dict()
            first.errors.extend(
                f"repair_failed:{error}" for error in second.errors
            )

        message = self._clarification_message(first.errors)
        clarification = self.compiler.clarification_plan(
            intent,
            message,
            reason="; ".join(first.errors) or "plan_invalid",
        )
        final = self.validate(clarification)
        final.repaired = True
        final.repair_reason = "; ".join(first.errors) or "plan_invalid"
        final.warnings.append("invalid plan converted to clarification")
        return clarification, final.to_dict()

    def validate(self, plan: Dict[str, Any]) -> ValidationReport:
        operations = [
            dict(op)
            for op in plan.get("operations") or []
            if isinstance(op, dict)
        ]
        errors: List[str] = []
        warnings: List[str] = []
        if not operations:
            errors.append("plan has no operations")
            return ValidationReport(False, errors=errors)

        ids: List[str] = []
        types = [str(op.get("type") or "") for op in operations]
        topics = [str(value) for value in plan.get("keywords") or [] if str(value)]
        author_name = str(plan.get("author_name") or "").strip()
        author_name_b = str(plan.get("author_name_b") or "").strip()
        author_ids = [str(value) for value in plan.get("author_ids") or [] if str(value)]
        dois = [str(value) for value in plan.get("dois") or [] if str(value)]
        targets = list(plan.get("targets") or [])
        institution = str(plan.get("institution") or "").strip()

        for index, op in enumerate(operations):
            operation = str(op.get("type") or "").strip()
            op_id = str(op.get("id") or "").strip()
            source = str(op.get("source") or "").strip()
            if not operation:
                errors.append(f"operation[{index}] missing type")
                continue
            if operation not in CONTRACTS:
                errors.append(f"unknown operation:{operation}")
            if not op_id or op_id in ids:
                errors.append(f"invalid operation id:{op_id or operation}")
            ids.append(op_id)
            expected = (
                "kg" if operation in KG_OPERATIONS else
                "rag" if operation in RAG_OPERATIONS else
                "sql"
            )
            if operation in CONTRACTS and source != expected:
                errors.append(
                    f"source mismatch:{operation}:expected {expected}, got {source}"
                )
            if dict(op.get("evidence_contract") or {}).get("kind") != dict(
                CONTRACTS.get(operation) or {}
            ).get("kind"):
                errors.append(f"evidence contract mismatch:{operation}")
            for dependency in op.get("depends_on") or []:
                if dependency not in ids:
                    errors.append(
                        f"dependency must precede operation:{op_id}->{dependency}"
                    )

            if operation in self.TOPIC_REQUIRED and not topics:
                errors.append(f"missing topics:{operation}")
            if operation in self.TOPIC_PRODUCIBLE and not (
                topics or bool(self.TOPIC_PRODUCERS.intersection(types[:index]))
            ):
                errors.append(f"missing topics or producer:{operation}")
            if operation in self.AUTHOR_REQUIRED and not author_name:
                if operation in {"author_papers", "author_collaborators"} and author_ids:
                    pass
                else:
                    errors.append(f"missing author:{operation}")
            if operation in self.AUTHOR_SET_REQUIRED and not (
                author_name or author_ids or "top_authors" in types[:index]
            ):
                errors.append(f"missing author set:{operation}")
            if operation in self.PAPER_SET_REQUIRED:
                enough = bool(dois or targets)
                if operation == "paper_neighborhood":
                    enough = bool(dois or targets)
                if not enough:
                    errors.append(f"missing paper set:{operation}")
            if operation == "institution_authors" and not institution:
                errors.append("missing institution:institution_authors")
            if operation == "coauthored_papers" and not (
                (author_name and author_name_b) or "author_network" in types[:index]
            ):
                errors.append("missing author pair:coauthored_papers")
            if operation == "submission_guidance" and "submission_fit" not in types[:index]:
                errors.append("missing dependency:submission_guidance->submission_fit")

        year_start, year_end = plan.get("year_start"), plan.get("year_end")
        if year_start is not None and year_end is not None:
            try:
                if int(year_start) > int(year_end):
                    errors.append("year_start is after year_end")
            except (TypeError, ValueError):
                errors.append("invalid year range")
        if plan.get("sources") != list(dict.fromkeys(op.get("source") for op in operations)):
            warnings.append("source index does not match operations")
        return ValidationReport(not errors, errors=errors, warnings=warnings)

    def _repair_intent(
        self,
        intent: Dict[str, Any],
        errors: List[str],
    ) -> Optional[Dict[str, Any]]:
        repaired = deepcopy(intent)
        # A topicless "field/area evolution" is a corpus-level comparison, not
        # a reason to invent a topic.  This is the only semantic repair here.
        is_field_evolution = (
            str(intent.get("entity") or "") in {"journal", "topic"}
            and str(intent.get("operation") or "") in {"trend", "compare"}
            and str(intent.get("metric") or "") == "keyword_freq"
            and str(intent.get("goal") or "research_analysis") == "research_analysis"
        )
        if (
            is_field_evolution
            and errors
            and all(error.startswith("missing topics:") for error in errors)
        ):
            repaired.update(
                {
                    "entity": "journal",
                    "operation": "compare",
                    "metric": "keyword_freq",
                    "topics": [],
                    "topic": [],
                    "requested_operations": list(
                        self.compiler.FIELD_EVOLUTION_OPERATIONS
                    ),
                    "source": "validator_repair",
                }
            )
            return repaired
        return None

    @staticmethod
    def _clarification_message(errors: List[str]) -> str:
        if any("author pair" in error for error in errors):
            return "请提供需要比较合作论文的两位作者姓名。"
        if any("author" in error for error in errors):
            return "请提供要查询的作者姓名，或先获取一组作者结果。"
        if any("institution" in error for error in errors):
            return "请提供要查询的机构名称。"
        if any("paper set" in error for error in errors):
            return "请先提供或查询需要分析的论文集合。"
        if any("topics" in error for error in errors):
            return "请提供要分析的具体研究专题。"
        return "当前问题无法生成可安全执行的查询计划，请补充具体查询对象和目标。"


__all__ = ["PlanValidator", "ValidationReport"]
