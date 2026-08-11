"""Deterministic hard gates for the ten product-level QA contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Set

from app.agents.coverage import assess_answer_coverage, assess_quality
from app.agents.operation_contracts import operation_types


def _results_by_type(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(row.get("operation")): row
        for row in state.get("operation_results") or []
        if isinstance(row, dict)
    }


def _papers(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add(paper: Dict[str, Any]) -> None:
        doi = str(paper.get("doi") or "")
        if doi and doi not in seen:
            seen.add(doi)
            rows.append(paper)

    for paper in data.get("papers") or []:
        add(paper)
    for key in ("authors", "topics", "directions", "institutions"):
        for group in data.get(key) or []:
            if isinstance(group, dict):
                for paper in group.get("papers") or []:
                    add(paper)
    for edge in data.get("edges") or []:
        if isinstance(edge, dict):
            for paper in edge.get("papers") or []:
                add(paper)
    return rows


def _valid_papers(rows: Iterable[Dict[str, Any]], require_authors: bool = False) -> bool:
    papers = list(rows)
    return bool(papers) and all(
        paper.get("doi")
        and (paper.get("title_zh") or paper.get("title"))
        and paper.get("year") is not None
        and (not require_authors or paper.get("authors"))
        for paper in papers
    )


def _edge_dois(data: Dict[str, Any]) -> bool:
    return all(
        edge.get("paper_count")
        and _valid_papers(edge.get("papers") or [])
        for edge in data.get("edges") or []
    )


def _periods_nonempty(data: Dict[str, Any]) -> bool:
    periods = data.get("periods") or []
    return len(periods) >= 2 and all(int(period.get("paper_count") or 0) > 0 for period in periods[:2])


def evaluate_core_contract(
    spec: Dict[str, Any], state: Dict[str, Any], answer: str
) -> Dict[str, Any]:
    """Return named gate outcomes and deterministic acceptance failures."""
    plan = dict(state.get("query_plan") or {})
    intent = dict(state.get("turn_intent") or {})
    results = list(state.get("operation_results") or [])
    by_type = _results_by_type(state)
    actual_ops = operation_types(plan)
    failures: List[str] = []

    for key, expected in (spec.get("expected_intent") or {}).items():
        if intent.get(key) != expected:
            failures.append(f"intent.{key}={intent.get(key)!r}, expected {expected!r}")
    for operation in spec.get("required_operations") or []:
        if operation not in actual_ops:
            failures.append(f"missing operation:{operation}")
        if operation not in by_type:
            failures.append(f"missing OperationResult:{operation}")
    forbidden = set(spec.get("forbidden_literal_topics") or [])
    leaked = forbidden & set(plan.get("keywords") or [])
    if leaked:
        failures.append("forbidden literal topics:" + ",".join(sorted(leaked)))

    now = datetime.now().year
    constraints = spec.get("constraints") or {}
    if constraints.get("relative_years"):
        years = int(constraints["relative_years"])
        if (plan.get("year_start"), plan.get("year_end")) != (now - years + 1, now):
            failures.append("relative time range mismatch")
    if constraints.get("default_complete_years"):
        years = int(constraints["default_complete_years"])
        if (plan.get("year_start"), plan.get("year_end")) != (now - years, now - 1):
            failures.append("complete-year default mismatch")
    if constraints.get("topic") and constraints["topic"] not in (
        list(plan.get("keywords") or []) + [plan.get("primary_topic")]
    ):
        failures.append(f"missing topic constraint:{constraints['topic']}")
    if constraints.get("author_name") and plan.get("author_name") != constraints["author_name"]:
        failures.append(f"author constraint mismatch:{plan.get('author_name')}")

    quality = assess_quality(answer, state)  # type: ignore[arg-type]
    answer_coverage = assess_answer_coverage(answer, results)
    minimum = float(spec.get("minimum_operation_coverage", 1.0))
    if quality["answer_relevance_precision"] < 0.9:
        failures.append(f"ARP={quality['answer_relevance_precision']}")
    if quality["required_goal_coverage"] < minimum:
        failures.append(f"RGC={quality['required_goal_coverage']}")
    if quality["evidence_accuracy"] < 1.0:
        failures.append(f"EA={quality['evidence_accuracy']}")
    if not quality["hard_gate_passed"]:
        failures.extend(str(value) for value in quality["hard_gate_violations"])
    if answer_coverage.get("coverage") != 1.0:
        failures.append("answer does not cover every operation")

    data = {name: row.get("data") or {} for name, row in by_type.items()}
    topic_papers = _papers(data.get("topic_papers") or {})
    author_papers = data.get("author_papers") or data.get("papers_for_authors") or {}
    topic_compare = data.get("topic_period_compare") or {}
    growth = data.get("keyword_growth") or {}
    author_network = data.get("author_network") or {}
    institution_network = data.get("institution_network") or {}

    def status_for(operation: str) -> str:
        return str((by_type.get(operation) or {}).get("status") or "")

    def gate(name: str) -> bool:
        if name == "all_operations_have_status":
            return len(results) == len(plan.get("operations") or []) and all(row.get("status") for row in results)
        if name == "no_invented_fact":
            return bool(quality["hard_gate_passed"] and quality["evidence_accuracy"] == 1.0)
        if name == "correct_time_range":
            return "relative time range mismatch" not in failures
        if name == "incomplete_current_year_labeled":
            return plan.get("year_end") != now or "未完年" in answer
        if name in {"at_least_two_non_empty_periods", "two_non_empty_periods"}:
            return _periods_nonempty(topic_compare)
        if name in {"representative_papers_traceable", "papers_traceable"}:
            op = data.get("representative_papers_by_topic") or data.get("topic_papers") or {}
            return _valid_papers(_papers(op))
        if name in {"fit_supported_by_data", "fit_supported_by_publication_data"}:
            fit = data.get("submission_fit") or {}
            return fit.get("fit_label") is not None and fit.get("topic_keywords") is not None
        if name == "authors_linked_to_topic_papers":
            return all(_valid_papers(row.get("papers") or []) for row in (data.get("authors_by_keyword") or {}).get("authors") or [])
        if name == "papers_have_title_year_doi":
            return any(_valid_papers(_papers(value)) for value in data.values())
        if name == "facts_and_suggestions_separated":
            return "投稿建议" in answer and ("历史" in answer or "关键词命中" in answer)
        if name == "counts_are_distinct_dois":
            top = (data.get("top_authors") or {}).get("authors") or []
            return int(author_papers.get("total_count") or 0) == sum(
                int(row.get("paper_count") or 0) for row in top
            )
        if name in {"teams_proven_by_coauthorship", "coauthors_have_shared_dois"}:
            collaborators = data.get("author_collaborators") or {}
            return all(_valid_papers(row.get("papers") or []) for row in collaborators.get("collaborators") or [])
        if name == "evolution_has_two_non_empty_periods_or_partial":
            return status_for("author_direction_evolution") in {"complete", "partial"}
        if name == "papers_belong_to_author_id":
            allowed = {row.get("author_id") for row in (data.get("top_authors") or {}).get("authors") or []}
            return bool(allowed) and all(row.get("author_id") in allowed for row in author_papers.get("authors") or [])
        if name == "no_same_name_merge":
            rows = (data.get("top_authors") or {}).get("authors") or []
            return len({row.get("author_id") for row in rows}) == len(rows)
        if name == "at_least_five_complete_years":
            return len((data.get("institution_stability") or {}).get("years") or []) >= 5
        if name == "stability_threshold_enforced":
            rows = (data.get("institution_stability") or {}).get("institutions") or []
            return all(bool(row.get("stable_high_output")) == (float(row.get("active_year_rate") or 0) >= 0.8 and float(row.get("top10_year_rate") or 0) >= 0.5) for row in rows)
        if name == "edges_have_shared_dois":
            return _edge_dois(author_network) and _edge_dois(institution_network)
        if name == "aliases_not_forced":
            return all(str(row.get("institution_id") or "").startswith("normalized:") for row in (data.get("institution_stability") or {}).get("institutions") or [])
        if name == "emerging_threshold_enforced":
            return all(not row.get("emerging") or (int(row.get("early_count") or 0) <= 1 and int(row.get("late_count") or 0) >= 3 and int(row.get("late_active_years") or 0) >= 2) for row in growth.get("keyword_growth") or [])
        if name == "ranked_by_share_delta":
            values = [float(row.get("share_delta_pp") or 0) for row in growth.get("keyword_growth") or []]
            return values == sorted(values, reverse=True)
        if name == "inference_language_used":
            return any(token in answer for token in ("候选", "可能", "值得关注"))
        if name == "no_external_trend_claim_without_evidence":
            return all(token not in answer for token in ("市场规模将", "政策必将", "全学科必然"))
        if name == "all_papers_match_topic_and_time":
            y0, y1 = plan.get("year_start"), plan.get("year_end")
            return bool(topic_papers) and all((y0 is None or int(p.get("year")) >= int(y0)) and (y1 is None or int(p.get("year")) <= int(y1)) for p in topic_papers)
        if name == "distinct_dois":
            dois = [paper.get("doi") for paper in topic_papers]
            return len(dois) == len(set(dois))
        if name == "summary_grounded_in_matched_papers":
            return bool((data.get("topic_papers") or {}).get("related_keywords") is not None)
        if name == "truncation_cursor_when_needed":
            topic = data.get("topic_papers") or {}
            return int(topic.get("total_count") or 0) <= int(topic.get("shown_count") or 0) or bool(topic.get("has_more") and topic.get("next_offset"))
        if name == "no_global_ranking_substitution":
            return "top_authors" not in actual_ops and "top_institutions" not in actual_ops
        if name == "stable_author_id_used":
            author = (data.get("author_profile") or {}).get("author") or {}
            return bool(author.get("author_id"))
        if name == "count_matches_distinct_dois":
            profile = data.get("author_profile") or {}
            return int(profile.get("total_papers") or 0) == len({paper.get("doi") for paper in _papers(data.get("author_papers") or {})})
        if name == "topics_have_paper_evidence":
            return bool(_papers(data.get("author_topic_summary") or {}))
        if name == "clarify_if_homonyms":
            profile = data.get("author_profile") or {}
            return not profile.get("ambiguous") or bool(intent.get("needs_clarification"))
        if name == "both_comparison_sides_preserved":
            return len(topic_compare.get("periods") or []) >= 2
        if name == "periods_contiguous_and_non_overlapping":
            periods = topic_compare.get("periods") or []
            return len(periods) >= 2 and int(periods[0]["end_year"]) + 1 == int(periods[1]["start_year"])
        if name == "share_based_comparison":
            return all(row.get("early_share_pct") is not None and row.get("late_share_pct") is not None for row in topic_compare.get("topics") or [])
        if name == "differences_traceable":
            return bool(topic_compare.get("topics")) and _valid_papers(_papers(data.get("representative_papers_by_topic") or {}))
        if name in {"rankings_limited_to_topic_result_set", "same_constraints_across_operations"}:
            return all((value.get("topic") == plan.get("keywords", [None])[0] or value.get("keyword") == plan.get("keywords", [None])[0] or plan.get("keywords", [None])[0] in (value.get("topics") or [])) for key, value in data.items() if key in {"authors_by_keyword", "institutions_by_keyword", "author_network", "institution_network"})
        if name == "network_nodes_limited_to_topic_result_set":
            authors = {row.get("author_id") for row in (data.get("authors_by_keyword") or {}).get("authors") or []}
            institutions = {row.get("institution") for row in (data.get("institutions_by_keyword") or {}).get("institutions") or []}
            return all(edge.get("source_id") in authors and edge.get("target_id") in authors for edge in author_network.get("edges") or []) and all(edge.get("source_name") in institutions and edge.get("target_name") in institutions for edge in institution_network.get("edges") or [])
        if name == "core_not_called_influence":
            return "学术影响力最高" not in answer and "最有影响力" not in answer
        if name == "direct_and_adjacent_matches_separated":
            fit = data.get("submission_fit") or {}
            return bool(fit.get("primary_topic")) and fit.get("direct_hits") is not None and fit.get("adjacent_hits") is not None and "相邻" in answer
        if name == "no_acceptance_promise":
            return "不代表" in answer and "录用承诺" in answer
        if name == "historical_data_limit_disclosed":
            return "历史" in answer and ("当前编辑政策" in answer or "不代表" in answer)
        return False

    named_gates: Dict[str, bool] = {}
    for name in spec.get("hard_gates") or []:
        named_gates[name] = gate(name)
        if not named_gates[name]:
            failures.append(f"hard gate failed:{name}")

    for check in spec.get("answer_section_checks") or []:
        any_tokens = list(check.get("any") or [])
        all_tokens = list(check.get("all") or [])
        passed = (not any_tokens or any(token in answer for token in any_tokens)) and all(
            token in answer for token in all_tokens
        )
        if not passed:
            failures.append(f"answer section missing:{check.get('name')}")

    return {
        "passed": not failures,
        "failures": list(dict.fromkeys(failures)),
        "named_gates": named_gates,
        "quality": quality,
        "answer_coverage": answer_coverage,
    }
