"""Per-operation DAG execution for an already validated query plan.

This module never invokes the legacy plan normalizer.  Every operation is
executed exactly once from its canonical spec and stores namespaced evidence.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Set

from app.agents.coverage import _assess, _result_items
from app.agents.operation_contracts import CoverageReport, OperationResult
from app.capabilities import kg_capability, rag_capability
from app.capabilities.sql_capability import _db, _execute_single_plan
from app.config import bind_corpus


class DeterministicExecutor:
    """Topologically execute the canonical Operation DAG."""

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        bind_corpus(state.get("journal_id"))
        plan = dict(state.get("query_plan") or {})
        specs = [
            dict(op)
            for op in plan.get("operations") or []
            if isinstance(op, dict)
        ]
        ordered = self._topological(specs)
        evidence_by_op: Dict[str, Dict[str, Any]] = {}
        status_by_op: Dict[str, str] = {}
        trace: List[Dict[str, Any]] = []
        errors = list(state.get("errors") or [])

        for spec in ordered:
            op_id = str(spec.get("id") or spec.get("type") or "")
            dependencies = [str(value) for value in spec.get("depends_on") or []]
            failed_dependencies = [
                dependency
                for dependency in dependencies
                if status_by_op.get(dependency) not in {"complete", "partial"}
            ]
            if failed_dependencies:
                data = {
                    "error": "上游 operation 未完成: " + ", ".join(failed_dependencies),
                    "dependency_failed": failed_dependencies,
                    "scope": spec.get("type"),
                }
            else:
                try:
                    data = self._execute_one(
                        spec,
                        plan,
                        state,
                        evidence_by_op,
                    )
                except Exception as exc:
                    data = {
                        "error": str(exc),
                        "scope": spec.get("type"),
                        "source": spec.get("source"),
                    }
            evidence_by_op[op_id] = dict(data or {})
            operation_status, missing = _assess(spec, evidence_by_op[op_id])
            status_by_op[op_id] = operation_status
            if operation_status == "error":
                errors.append(
                    f"{op_id}: "
                    + str(evidence_by_op[op_id].get("error") or "; ".join(missing))
                )
            trace.append(
                {
                    "op_id": op_id,
                    "operation": spec.get("type"),
                    "source": spec.get("source"),
                    "depends_on": dependencies,
                    "status": operation_status,
                }
            )

        self._attach_source_evidence(state, ordered, evidence_by_op)
        state["operation_evidence"] = evidence_by_op
        state["execution_trace"] = trace
        state["errors"] = errors
        state["evidence_bundle"] = [
            {
                "step": index,
                "source": row["source"],
                "operation": row["operation"],
                "summary": row["status"],
            }
            for index, row in enumerate(trace, 1)
        ]
        state.update(self._assess_all(plan, ordered, evidence_by_op))
        state["stage"] = "evidence_validated"
        return state

    @staticmethod
    def _topological(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_id = {str(spec.get("id")): spec for spec in specs}
        pending = list(specs)
        emitted: Set[str] = set()
        ordered: List[Dict[str, Any]] = []
        while pending:
            ready = [
                spec
                for spec in pending
                if all(
                    str(dep) in emitted
                    for dep in spec.get("depends_on") or []
                )
            ]
            if not ready:
                unresolved = [str(spec.get("id")) for spec in pending]
                raise ValueError(
                    "operation dependency cycle or missing dependency: "
                    + ", ".join(unresolved)
                )
            for spec in ready:
                op_id = str(spec.get("id"))
                if op_id not in by_id:
                    raise ValueError(f"unknown operation id: {op_id}")
                ordered.append(spec)
                emitted.add(op_id)
                pending.remove(spec)
        return ordered

    def _execute_one(
        self,
        spec: Dict[str, Any],
        plan: Dict[str, Any],
        state: Dict[str, Any],
        evidence_by_op: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        operation = str(spec.get("type") or "")
        source = str(spec.get("source") or "")
        params = self._resolved_params(spec, plan, state, evidence_by_op)

        if operation == "clarification":
            return {
                "scope": "clarification",
                "task": "clarification",
                "message": plan.get("focus") or "请补充需要查询的对象。",
                "source": "conversation",
            }
        if operation == "unsupported_citations":
            return {
                "scope": "unsupported",
                "task": operation,
                "reason": "当前语料没有被引或引用次数字段，无法可靠完成该排名。",
                "source": "capability_registry",
            }
        if operation == "submission_guidance":
            fit = self._first_scope(evidence_by_op, "submission_fit")
            return {
                "scope": operation,
                "task": operation,
                "fit_label": fit.get("fit_label"),
                "total_hits": fit.get("total_hits"),
                "keywords_queried": fit.get("keywords_queried") or params.get("keywords") or [],
                "papers": fit.get("papers") or [],
                "is_inference": True,
                "source": "derived",
            }

        if source == "sql":
            data = self._execute_sql(operation, params, plan, state)
            data = dict(data or {})
            if operation == "topic_period_compare":
                data["comparison_goal"] = (
                    "field_evolution"
                    if not list(plan.get("keywords") or [])
                    else "period_compare"
                )
            return data

        if source == "kg":
            result = kg_capability.invoke(
                operation,
                question=str(state.get("question") or ""),
                journal_id=state.get("journal_id"),
                keyword=(params.get("keywords") or [None])[0],
                name=params.get("author_name"),
                doi=(params.get("dois") or [None])[0],
            )
            if not result.get("ok"):
                return {"error": result.get("error"), "source": "kg"}
            return dict(result.get("data") or {})

        if source == "rag":
            result = rag_capability.invoke(
                "semantic_search",
                question=str(state.get("question") or ""),
                queries=list(params.get("keywords") or []) or None,
                top_k=state.get("top_k"),
                year_start=params.get("year_start"),
                year_end=params.get("year_end"),
                journal_id=state.get("journal_id"),
            )
            if not result.get("ok"):
                return {
                    "error": result.get("error"),
                    "source": "rag",
                    "hits": [],
                    "citations": [],
                }
            return dict(result.get("data") or {})
        return {"error": f"unsupported source: {source}", "source": source}

    @staticmethod
    def _execute_sql(
        operation: str,
        params: Dict[str, Any],
        plan: Dict[str, Any],
        state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Direct structured capability adapter with no question parsing."""
        db = _db(state.get("journal_id"))
        y0, y1 = params.get("year_start"), params.get("year_end")
        keywords = list(params.get("keywords") or [])
        limit = int(params.get("limit") or 20)

        if operation == "yearly_counts":
            return {
                "scope": operation,
                "task": operation,
                "yearly": db.yearly_counts(y0, y1),
                "start_year": y0,
                "end_year": y1,
                "source": "sqlite",
            }
        if operation == "top_keywords":
            return {
                "scope": operation,
                "task": operation,
                "keywords": db.top_keywords(limit, y0, y1),
                "start_year": y0,
                "end_year": y1,
                "source": "sqlite",
            }
        if operation == "keyword_growth":
            return {**db.keyword_growth(limit, y0, y1), "task": operation}
        if operation == "topic_period_compare":
            return {**db.topic_period_compare(keywords, y0, y1, limit), "task": operation}
        if operation == "topic_keyword_counts" or operation == "topic_yearly":
            return {
                **db.topic_keyword_stats(keywords, y0, y1),
                "scope": "topic_stats",
                "task": operation,
            }
        if operation == "top_authors":
            return {
                "scope": operation,
                "task": operation,
                "authors": db.top_authors(limit, y0, y1),
                "top_n": limit,
                "start_year": y0,
                "end_year": y1,
                "source": "sqlite",
            }
        if operation == "top_institutions":
            return {
                "scope": operation,
                "task": operation,
                "institutions": db.top_institutions(limit, y0, y1),
                "top_n": limit,
                "start_year": y0,
                "end_year": y1,
                "source": "sqlite",
            }
        if operation == "authors_by_keyword":
            if len(keywords) > 1:
                return db.representative_authors_by_topics(keywords, limit, y0, y1)
            return db.authors_by_keyword(
                keywords[0] if keywords else "",
                author_limit=limit,
                papers_per_author=int(params.get("papers_per_author") or 3),
                start_year=y0,
                end_year=y1,
            )
        if operation == "institutions_by_keyword":
            if len(keywords) > 1:
                return db.institutions_by_topics(keywords, limit, y0, y1)
            return db.institutions_by_keyword(
                keywords[0] if keywords else "",
                limit,
                y0,
                y1,
            )
        if operation in {"topic_papers", "representative_papers_by_topic"}:
            papers_per_topic = int(
                params.get("papers_per_topic")
                or (5 if operation == "representative_papers_by_topic" else 10000)
            )
            return {
                **db.papers_by_topics(keywords, papers_per_topic, y0, y1),
                "scope": operation,
                "task": operation,
            }
        if operation == "representative_authors_by_topic":
            return {
                **db.representative_authors_by_topics(keywords, limit, y0, y1),
                "task": operation,
            }
        if operation == "papers_by_top_keywords":
            directions = []
            for keyword in keywords[: int(params.get("top_n_directions") or 3)]:
                directions.append(
                    {
                        "keyword": keyword,
                        "papers": db.papers_by_keyword(
                            keyword,
                            int(params.get("papers_per_topic") or 5),
                            y0,
                            y1,
                        ),
                    }
                )
            return {
                "scope": operation,
                "task": operation,
                "directions": directions,
                "source": "sqlite",
            }
        if operation in {"author_profile", "author_topic_summary"}:
            return {
                **db.author_profile(str(params.get("author_name") or "")),
                "task": operation,
            }
        if operation in {"author_papers", "papers_for_authors"}:
            grouped = db.papers_for_authors(list(params.get("author_ids") or []), y0, y1)
            papers = [
                {
                    **paper,
                    "author_id": author.get("author_id"),
                    "author_name": author.get("name_zh"),
                }
                for author in grouped
                for paper in author.get("papers") or []
            ]
            return {
                "scope": operation,
                "task": operation,
                "authors": grouped,
                "papers": papers,
                "total_count": len(papers),
                "shown_count": len(papers),
                "has_more": False,
                "source": "sqlite",
            }
        if operation == "author_collaborators":
            return {
                **db.author_collaborators_for_ids(
                    list(params.get("author_ids") or []), limit, y0, y1
                ),
                "task": operation,
            }
        if operation == "author_direction_evolution":
            author_ids = list(params.get("author_ids") or [])
            if len(author_ids) > 1 and not params.get("author_name"):
                authors = [
                    db.author_direction_evolution(None, author_id, y0, y1)
                    for author_id in author_ids
                ]
                return {
                    "scope": operation,
                    "task": operation,
                    "authors": authors,
                    "source": "sqlite",
                }
            return {
                **db.author_direction_evolution(
                    params.get("author_name"),
                    author_ids[0] if author_ids else None,
                    y0,
                    y1,
                ),
                "task": operation,
            }
        if operation == "author_direction_diversity":
            return {**db.author_direction_diversity(limit, y0, y1), "task": operation}
        if operation == "institution_stability":
            return {**db.institution_stability(limit, y0, y1), "task": operation}
        if operation == "institution_authors":
            return {
                **db.institution_authors_with_papers(
                    str(params.get("institution") or ""),
                    top_authors=limit,
                    start_year=y0,
                    end_year=y1,
                ),
                "task": operation,
            }
        if operation == "author_network":
            return {
                **db.author_network(keywords[0] if keywords else None, limit, y0, y1),
                "task": operation,
            }
        if operation == "institution_network":
            return {
                **db.institution_network(keywords[0] if keywords else None, limit, y0, y1),
                "task": operation,
            }
        if operation == "representative_papers_by_institution":
            return {
                **db.representative_papers_by_institutions(
                    list(params.get("institutions") or []),
                    int(params.get("papers_per_institution") or 5),
                    y0,
                    y1,
                ),
                "task": operation,
            }
        if operation == "coauthored_papers":
            return {
                **db.coauthored_papers(
                    str(params.get("author_name") or ""),
                    str(params.get("author_name_b") or ""),
                ),
                "task": operation,
            }
        if operation == "paper_set_topic_summary":
            return {
                **db.paper_set_topic_summary(list(params.get("dois") or []), limit),
                "task": operation,
                "source_record_count": int(params.get("source_record_count") or 0),
                "paper_authors": list(params.get("paper_authors") or []),
            }
        if operation == "present_resultset":
            targets = list(params.get("targets") or [])
            grouped: Dict[str, Dict[str, Any]] = {}
            for paper in targets:
                author_id = str(paper.get("author_id") or "unknown")
                grouped.setdefault(
                    author_id,
                    {
                        "author_id": author_id,
                        "name_zh": paper.get("author_name") or "上一轮论文",
                        "papers": [],
                    },
                )["papers"].append(
                    {
                        "doi": paper.get("doi"),
                        "title_zh": paper.get("title") or paper.get("title_zh"),
                        "year": paper.get("year"),
                    }
                )
            return {
                "scope": operation,
                "task": operation,
                "authors": list(grouped.values()),
                "papers": targets,
                "total_count": len(targets),
                "shown_count": len(targets),
                "has_more": False,
                "source": "conversation",
            }
        if operation == "journal_overview":
            return {**db.journal_overview(y0, y1), "task": operation}

        # Less-common registered SQL operations still receive a canonical
        # single-op plan.  Question is intentionally empty, so this fallback
        # cannot infer or augment semantic slots from natural language.
        subplan = {
            **deepcopy(plan),
            **params,
            "task": operation,
            "main_task": operation,
            "operations": [],
            "sql_ops": [operation],
            "kg_ops": [],
            "rag_queries": [],
        }
        return _execute_single_plan(subplan, "", {}, db)

    @staticmethod
    def _first_scope(
        evidence_by_op: Dict[str, Dict[str, Any]],
        scope: str,
    ) -> Dict[str, Any]:
        return next(
            (
                data
                for data in evidence_by_op.values()
                if data.get("scope") == scope or data.get("task") == scope
            ),
            {},
        )

    def _resolved_params(
        self,
        spec: Dict[str, Any],
        plan: Dict[str, Any],
        state: Dict[str, Any],
        evidence_by_op: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        params = {
            "year_start": plan.get("year_start"),
            "year_end": plan.get("year_end"),
            "keywords": list(plan.get("keywords") or []),
            "top_n": plan.get("top_n"),
            "author_name": plan.get("author_name"),
            "author_name_b": plan.get("author_name_b"),
            "author_ids": list(plan.get("author_ids") or []),
            "institution": plan.get("institution"),
            "institutions": list(plan.get("institutions") or []),
            "dois": list(plan.get("dois") or []),
            "targets": deepcopy(plan.get("targets") or []),
            "paper_authors": deepcopy(plan.get("paper_authors") or []),
            "source_record_count": plan.get("source_record_count"),
            "offset": plan.get("offset"),
            "max_items": plan.get("max_items"),
            "max_chars": plan.get("max_chars"),
        }
        params.update(deepcopy(spec.get("params") or {}))

        dependencies = [str(value) for value in spec.get("depends_on") or []]
        upstream = [evidence_by_op.get(dep) or {} for dep in dependencies]
        if not params.get("author_ids"):
            params["author_ids"] = self._author_ids(upstream)
        if not params.get("keywords"):
            params["keywords"] = self._topics(upstream)
        if not params.get("institutions"):
            params["institutions"] = self._institutions(upstream)
        if not params.get("dois"):
            params["dois"] = self._dois(upstream)
        if not params.get("dois"):
            params["dois"] = list((state.get("entities") or {}).get("dois") or [])

        limit = params.get("top_n")
        params["limit"] = int(limit or 20)
        if params.get("keywords"):
            params["keyword"] = params["keywords"][0]
        if params.get("author_ids"):
            params["author_id"] = params["author_ids"][0]
        if params.get("author_name"):
            params["name"] = params["author_name"]
        return params

    @staticmethod
    def _author_ids(rows: List[Dict[str, Any]]) -> List[str]:
        result: List[str] = []
        for data in rows:
            for row in data.get("authors") or []:
                author = row.get("author") if isinstance(row, dict) else None
                value = (
                    row.get("author_id")
                    if isinstance(row, dict)
                    else None
                ) or (author or {}).get("author_id")
                if value and str(value) not in result:
                    result.append(str(value))
        return result

    @staticmethod
    def _topics(rows: List[Dict[str, Any]]) -> List[str]:
        result: List[str] = []
        # Growth evidence is a better producer for field-change examples.
        ordered = sorted(
            rows,
            key=lambda row: 0 if row.get("keyword_growth") else 1,
        )
        for data in ordered:
            candidates = (
                data.get("keyword_growth")
                or data.get("keywords")
                or data.get("topics")
                or []
            )
            for row in candidates:
                value = row.get("keyword") if isinstance(row, dict) else row
                if value and str(value) not in result:
                    result.append(str(value))
        return result[:5]

    @staticmethod
    def _institutions(rows: List[Dict[str, Any]]) -> List[str]:
        result: List[str] = []
        for data in rows:
            for row in data.get("institutions") or []:
                value = row.get("institution") if isinstance(row, dict) else row
                if value and str(value) not in result:
                    result.append(str(value))
        return result[:20]

    @staticmethod
    def _dois(rows: List[Dict[str, Any]]) -> List[str]:
        result: List[str] = []
        stack: List[Any] = list(rows)
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                doi = value.get("doi")
                if doi and str(doi) not in result:
                    result.append(str(doi))
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        return result

    @staticmethod
    def _attach_source_evidence(
        state: Dict[str, Any],
        specs: List[Dict[str, Any]],
        evidence_by_op: Dict[str, Dict[str, Any]],
    ) -> None:
        for source in ("sql", "kg", "rag"):
            source_specs = [spec for spec in specs if spec.get("source") == source]
            source_data = {
                str(spec.get("id")): evidence_by_op.get(str(spec.get("id"))) or {}
                for spec in source_specs
            }
            if not source_specs:
                state[f"{source}_evidence"] = {}
                continue
            if len(source_data) == 1:
                aggregate = dict(next(iter(source_data.values())))
            else:
                aggregate = {
                    "scope": (state.get("query_plan") or {}).get("main_task"),
                    "task": (state.get("query_plan") or {}).get("main_task"),
                    "source": source,
                }
                for data in source_data.values():
                    for key, value in data.items():
                        if key not in aggregate or aggregate.get(key) in (None, [], {}):
                            aggregate[key] = value
            if source == "sql":
                aggregate["operation_data"] = source_data
            if source == "rag" and not aggregate.get("citations"):
                aggregate["citations"] = [
                    citation
                    for data in source_data.values()
                    for citation in data.get("citations") or []
                ]
            state[f"{source}_evidence"] = aggregate

    @staticmethod
    def _assess_all(
        plan: Dict[str, Any],
        specs: List[Dict[str, Any]],
        evidence_by_op: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        by_operation: Dict[str, Any] = {}
        primary_items: List[Dict[str, Any]] = []
        required_statuses: List[str] = []
        continuation: Optional[Dict[str, Any]] = None
        for spec in specs:
            op_id = str(spec.get("id") or spec.get("type"))
            operation = str(spec.get("type") or "")
            data = dict(evidence_by_op.get(op_id) or {})
            status, missing = _assess(spec, data)
            items = _result_items(operation, data)
            result_set = {
                "type": operation,
                "items": items,
                "shown_count": int(data.get("shown_count") or len(items)),
                "total_count": int(data.get("total_count") or len(items)),
                "has_more": bool(data.get("has_more")),
                "next_offset": data.get("next_offset"),
            }
            if result_set["has_more"] and continuation is None:
                continuation = {**result_set, "operation_id": op_id}
            result = OperationResult(
                op_id=op_id,
                operation=operation,
                status=status,
                data=data,
                result_set=result_set,
                missing_requirements=missing,
                error=str(data.get("error")) if data.get("error") else None,
            ).to_dict()
            results.append(result)
            by_operation[op_id] = result_set
            if spec.get("required", True):
                required_statuses.append(status)
            if not primary_items and items:
                primary_items = items

        counts = {
            status: sum(1 for current in required_statuses if current == status)
            for status in ("complete", "partial", "unsupported", "error")
        }
        total = len(required_statuses)
        report = CoverageReport(
            required_count=total,
            complete_count=counts["complete"],
            partial_count=counts["partial"],
            unsupported_count=counts["unsupported"],
            error_count=counts["error"],
            coverage=round(counts["complete"] / total, 3) if total else 1.0,
            needs_llm=any(status != "complete" for status in required_statuses),
            operations=[
                {
                    "op_id": row["op_id"],
                    "operation": row["operation"],
                    "status": row["status"],
                    "missing_requirements": row["missing_requirements"],
                }
                for row in results
            ],
        ).to_dict()
        result_set: Dict[str, Any] = {
            "type": plan.get("main_task") or plan.get("task") or "answer",
            "items": primary_items,
            "by_operation": by_operation,
            "total_count": len(primary_items),
            "constraints": dict(plan.get("constraints") or {}),
        }
        if continuation:
            result_set["continuation"] = continuation
        return {
            "operation_results": results,
            "coverage_report": report,
            "result_set": result_set,
        }


__all__ = ["DeterministicExecutor"]
