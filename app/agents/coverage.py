"""Operation-level evidence assessment and stable ResultSet construction."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from app.agents.operation_contracts import CoverageReport, OperationResult
from app.agents.state import JournalState


def _paper_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    papers: List[Dict[str, Any]] = []
    seen = set()

    def add(paper: Dict[str, Any], **extra: Any) -> None:
        doi = paper.get("doi")
        identity = (doi, extra.get("author_id")) if extra.get("author_id") else doi
        if not doi or identity in seen:
            return
        seen.add(identity)
        papers.append({
            "type": "paper", "id": doi, "doi": doi,
            "title": paper.get("title_zh") or paper.get("title"),
            "year": paper.get("year"), **extra,
        })

    for key in (
        "papers", "recent_papers", "sample_papers", "direct_papers",
        "adjacent_papers", "generic_only_papers",
    ):
        for paper in data.get(key) or []:
            add(paper, evidence_role=key)
    for author in data.get("authors") or []:
        if not isinstance(author, dict):
            continue
        for paper in author.get("papers") or []:
            add(paper, author_id=author.get("author_id"), author_name=author.get("name_zh"))
        for collaborator in author.get("collaborators") or []:
            for paper in collaborator.get("papers") or []:
                add(
                    paper,
                    author_id=(author.get("author") or {}).get("author_id"),
                    collaborator_id=collaborator.get("author_id"),
                )
    for collaborator in data.get("collaborators") or []:
        if not isinstance(collaborator, dict):
            continue
        for paper in collaborator.get("papers") or []:
            add(paper, collaborator_id=collaborator.get("author_id"))
    for direction in data.get("directions") or []:
        for paper in direction.get("papers") or []:
            add(paper, keyword=direction.get("keyword"))
    for topic in data.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        for paper in topic.get("papers") or []:
            add(paper, keyword=topic.get("topic") or topic.get("keyword"))
    for institution in data.get("institutions") or []:
        if not isinstance(institution, dict):
            continue
        for paper in institution.get("papers") or []:
            add(paper, institution=institution.get("institution"))
    for edge in data.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        for paper in edge.get("papers") or []:
            add(paper, source_id=edge.get("source_id"), target_id=edge.get("target_id"))
    for period in data.get("periods") or []:
        for paper in period.get("papers") or []:
            add(paper, period=period.get("label"))
    return papers


def _result_items(op_type: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if op_type == "paper_set_topic_summary":
        keyword_items = [
            {
                "type": "keyword",
                "id": f"keyword:{row.get('keyword')}",
                "name": row.get("keyword"),
                "paper_count": row.get("paper_count"),
                "position": index,
            }
            for index, row in enumerate(data.get("keywords") or [], 1)
            if isinstance(row, dict) and row.get("keyword")
        ]
        return keyword_items + _paper_items(data)
    if op_type in {"top_authors", "author_direction_diversity", "authors_by_keyword", "representative_authors_by_topic"}:
        return [
            {"type": "author", "id": row.get("author_id"),
             "name": row.get("name_zh") or row.get("name_en"),
             "paper_count": row.get("paper_count"), "position": index,
             "direction_count": row.get("direction_count")}
            for index, row in enumerate(data.get("authors") or [], 1)
            if isinstance(row, dict) and row.get("author_id")
        ]
    if op_type in {"top_institutions", "institution_stability", "institutions_by_keyword"}:
        return [
            {"type": "institution", "id": row.get("institution_id") or f"normalized:{row.get('institution')}",
             "name": row.get("institution"), "paper_count": row.get("paper_count") or row.get("total_papers"),
             "position": index, "stable_high_output": row.get("stable_high_output")}
            for index, row in enumerate(data.get("institutions") or [], 1)
            if isinstance(row, dict) and row.get("institution")
        ]
    if op_type in {
        "top_keywords", "keyword_growth", "topic_period_compare",
        "author_topic_summary", "research_methods",
    }:
        rows = data.get("keywords") or data.get("keyword_growth") or data.get("topics") or data.get("methods") or []
        return [
            {"type": "keyword", "id": f"keyword:{row.get('keyword')}", "name": row.get("keyword"),
             "paper_count": row.get("paper_count") or row.get("late_count"), "position": index}
            for index, row in enumerate(rows, 1) if isinstance(row, dict) and row.get("keyword")
        ]
    if op_type in {"author_network", "institution_network"}:
        return [
            {
                "type": "relation",
                "id": f"{row.get('source_id')}->{row.get('target_id')}",
                "source_id": row.get("source_id"),
                "target_id": row.get("target_id"),
                "paper_count": row.get("paper_count"),
                "position": index,
            }
            for index, row in enumerate(data.get("edges") or [], 1)
            if isinstance(row, dict) and row.get("source_id") and row.get("target_id")
        ]
    papers = _paper_items(data)
    if papers:
        return papers
    author = data.get("author") or {}
    if isinstance(author, dict) and author.get("author_id"):
        return [{"type": "author", "id": author.get("author_id"), "name": author.get("name_zh") or author.get("name_en")}]
    return []


def _assess(op: Dict[str, Any], data: Dict[str, Any]) -> Tuple[str, List[str]]:
    op_type = str(op.get("type") or "")
    contract = dict(op.get("evidence_contract") or {})
    missing: List[str] = []
    if data.get("error"):
        return "error", [str(data.get("error"))]
    if contract.get("kind") == "unsupported" or data.get("scope") == "unsupported":
        return "unsupported", [str(data.get("reason") or contract.get("reason") or "数据源不支持该指标")]
    if contract.get("kind") == "clarification":
        return "complete", []
    if contract.get("kind") == "notice":
        return ("complete", []) if data.get("message") else ("partial", ["缺少数据范围说明"])
    if contract.get("kind") == "opportunity":
        return (
            ("complete", [])
            if data.get("message") and data.get("directions")
            else ("partial", ["缺少可追溯的投稿机会依据"])
        )
    if contract.get("kind") == "evolution":
        evolutions = data.get("authors") or [data]
        complete_authors = 0
        for evolution in evolutions:
            if isinstance(evolution, dict) and evolution.get("evolution"):
                evolution = evolution["evolution"]
            if not isinstance(evolution, dict):
                continue
            nonempty = [
                p for p in evolution.get("periods") or []
                if int(p.get("paper_count") or 0) > 0 and p.get("keywords")
            ]
            if (
                int(evolution.get("total_papers") or 0) >= int(contract.get("min_papers") or 3)
                and len(nonempty) >= int(contract.get("min_periods") or 2)
            ):
                complete_authors += 1
        if not complete_authors:
            missing.extend(["论文数量不足 3 篇", "不足两个含主题证据的阶段"])
        elif complete_authors < len(evolutions):
            missing.append("部分作者不足两个含主题证据的阶段")
    elif contract.get("kind") == "stability":
        if len(data.get("years") or []) < int(contract.get("min_periods") or 5):
            missing.append("完整年份不足 5 年")
        if not data.get("institutions"):
            missing.append("无机构逐年结果")
    elif contract.get("kind") == "trend":
        rows = data.get(contract.get("items") or "yearly") or []
        if op_type == "topic_yearly":
            rows = [r for series in (data.get("yearly_by_keyword") or {}).values() for r in series]
        if op_type == "keyword_growth":
            rows = data.get("periods") or []
        if len(rows) < int(contract.get("min_periods") or 2):
            missing.append("趋势有效阶段不足 2 个")
    elif contract.get("kind") == "comparison":
        if len(data.get("periods") or []) < int(contract.get("min_periods") or 2):
            missing.append("对比阶段不足 2 个")
        topics = data.get("topics") or []
        if topics and all(
            int(row.get("early_count") or 0) == 0 and int(row.get("late_count") or 0) == 0
            for row in topics
        ):
            missing.append("指定主题在关键词字段中无直接命中")
    elif contract.get("kind") == "papers":
        papers = _paper_items(data)
        if not papers:
            missing.append("无可追溯论文")
        elif any(not paper.get("title") or paper.get("year") is None for paper in papers):
            missing.append("论文缺少题名或年份")
    elif contract.get("kind") == "author":
        if not data.get("author"):
            missing.append("未找到作者")
    elif contract.get("kind") == "ranking":
        rows = data.get(contract.get("items") or "") or []
        if not rows:
            missing.append("排名结果为空")
        required_fields = contract.get("required_fields") or []
        for field in required_fields:
            if rows and any(row.get(field) is None for row in rows if isinstance(row, dict)):
                missing.append(f"排名缺少字段 {field}")
    elif contract.get("kind") == "network":
        rows = data.get(contract.get("items") or "edges") or []
        if not rows and not data.get("query_completed"):
            missing.append("合作关系为空")
        for field in contract.get("required_fields") or []:
            if rows and any(row.get(field) is None for row in rows if isinstance(row, dict)):
                missing.append(f"合作关系缺少字段 {field}")
        if rows and any(not _paper_items({"edges": [row]}) for row in rows if isinstance(row, dict)):
            missing.append("合作关系缺少共同 DOI 证据")
    elif contract.get("kind") == "graph":
        if not any(data.get(key) for key in ("nodes", "edges", "collaborators", "papers", "keyword_papers")):
            missing.append("图谱结果为空")
    elif contract.get("kind") == "topic":
        if not data.get(contract.get("items") or "topic_keywords"):
            missing.append("主题统计为空")
    elif contract.get("kind") == "coverage":
        if data.get("topic_keywords") is None and data.get("keywords_queried") is None:
            missing.append("缺少专题覆盖统计")
    elif contract.get("kind") == "derived":
        if data.get("fit_label") is None and data.get("total_hits") is None:
            missing.append("缺少可用于建议的投稿匹配证据")
    elif contract.get("kind") == "overview":
        if not any(data.get(key) for key in ("yearly", "keywords", "authors", "institutions")):
            missing.append("期刊概览证据为空")
    elif not data:
        missing.append("无证据")
    return ("complete" if not missing else "partial"), missing


def assess_operations(state: JournalState) -> Dict[str, Any]:
    plan = dict(state.get("query_plan") or {})
    sql = dict(state.get("sql_evidence") or {})
    sql_by_op = dict(sql.get("operation_data") or {})
    kg = dict(state.get("kg_evidence") or {})
    rag = dict(state.get("rag_evidence") or {})
    results: List[Dict[str, Any]] = []
    by_operation: Dict[str, Any] = {}
    primary_items: List[Dict[str, Any]] = []
    for op in plan.get("operations") or []:
        if not isinstance(op, dict):
            continue
        op_id = str(op.get("id") or op.get("type"))
        source = op.get("source") or "sql"
        data = sql_by_op.get(op_id) if source == "sql" else (kg if source == "kg" else rag)
        data = dict(data or {})
        status, missing = _assess(op, data)
        items = _result_items(str(op.get("type") or ""), data)
        result_set = {
            "type": op.get("type"),
            "items": items,
            "shown_count": int(data.get("shown_count") or len(items)),
            "total_count": int(data.get("total_count") or len(items)),
            "has_more": bool(data.get("has_more")),
            "next_offset": data.get("next_offset"),
        }
        result = OperationResult(
            op_id=op_id, operation=str(op.get("type") or ""), status=status,
            data=data, result_set=result_set, missing_requirements=missing,
            error=str(data.get("error")) if data.get("error") else None,
        ).to_dict()
        results.append(result)
        by_operation[op_id] = result_set
        if not primary_items and items:
            primary_items = items
    required = [r for r, op in zip(results, plan.get("operations") or []) if not isinstance(op, dict) or op.get("required", True)]
    counts = {status: sum(1 for r in required if r["status"] == status) for status in ("complete", "partial", "unsupported", "error")}
    complete = counts["complete"]
    total = len(required)
    report = CoverageReport(
        required_count=total, complete_count=complete, partial_count=counts["partial"],
        unsupported_count=counts["unsupported"], error_count=counts["error"],
        coverage=round(complete / total, 3) if total else 1.0,
        needs_llm=any(r["status"] != "complete" for r in required),
        operations=[{"op_id": r["op_id"], "operation": r["operation"], "status": r["status"], "missing_requirements": r["missing_requirements"]} for r in results],
    ).to_dict()
    result_set = {
        "type": plan.get("main_task") or plan.get("task") or "answer",
        "items": primary_items,
        "by_operation": by_operation,
        "total_count": len(primary_items),
        "constraints": dict(plan.get("constraints") or {}),
    }
    return {"operation_results": results, "coverage_report": report, "result_set": result_set, "stage": "covered"}


def coverage_node(state: JournalState) -> Dict[str, Any]:
    return assess_operations(state)


def assess_answer_coverage(answer: str, operation_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    text = answer or ""
    summary_tokens = {
        "author_profile": ("作者身份", "发文概况", "author_id"),
        "author_collaborators": ("主要合作者", "合作伙伴"),
        "author_direction_evolution": ("方向演化", "研究方向演化"),
        "institution_stability": ("长期高产机构", "稳定性"),
        "journal_overview": ("期刊概览", "发展历程"),
        "submission_guidance": ("投稿建议",),
        "research_methods": ("常见研究方法", "方法相关关键词"),
        "submission_opportunities": ("潜在投稿机会",),
    }
    rows: List[Dict[str, Any]] = []
    for result in operation_results or []:
        status = result.get("status")
        op_type = str(result.get("operation") or "")
        items = (result.get("result_set") or {}).get("items") or []
        anchors: List[str] = []
        for item in items[:5]:
            for key in ("name", "title", "doi"):
                value = str(item.get(key) or "").strip()
                if len(value) >= 2:
                    anchors.append(value)
        data = result.get("data") or {}
        if op_type == "author_direction_evolution":
            author = data.get("author") or {}
            if author.get("name_zh"):
                anchors.insert(0, str(author.get("name_zh")))
        if not anchors:
            for row in (data.get("yearly") or data.get("yoy") or [])[:2]:
                if row.get("year") is not None:
                    anchors.append(str(row["year"]))
        if status == "complete":
            covered = (
                any(anchor in text for anchor in anchors)
                or any(token in text for token in summary_tokens.get(op_type, ()))
            ) if anchors or summary_tokens.get(op_type) else bool(text.strip())
        else:
            covered = any(token in text for token in ("证据不足", "数据不足", "不支持", "无法", "可能", "推测", "候选"))
        rows.append({"op_id": result.get("op_id"), "operation": op_type, "covered": covered, "anchors": anchors[:5]})
    return {
        "covered_count": sum(1 for row in rows if row["covered"]),
        "total_count": len(rows),
        "coverage": round(sum(1 for row in rows if row["covered"]) / len(rows), 3) if rows else 1.0,
        "missing_operation_ids": [row["op_id"] for row in rows if not row["covered"]],
        "operations": rows,
    }


def assess_quality(answer: str, state: JournalState) -> Dict[str, Any]:
    """Deterministic runtime proxy for ARP, RGC, EA and hard gates."""
    from app.agents.evidence_guard import (
        collect_allowed_labels,
        collect_allowed_numbers,
        collect_allowed_papers,
    )

    results = list(state.get("operation_results") or [])
    answer_coverage = assess_answer_coverage(answer, results)
    covered = {
        row.get("op_id"): bool(row.get("covered"))
        for row in answer_coverage.get("operations") or []
    }

    required_specs = [
        op for op in (state.get("query_plan") or {}).get("operations") or []
        if isinstance(op, dict) and op.get("required", True)
    ]
    by_id = {str(row.get("op_id")): row for row in results}
    rgc_score = 0.0
    for spec in required_specs:
        row = by_id.get(str(spec.get("id"))) or {}
        score = 1.0 if row.get("status") == "complete" else (0.5 if row.get("status") == "partial" else 0.0)
        if not covered.get(row.get("op_id"), False):
            score = 0.0
        rgc_score += score
    rgc = round(rgc_score / len(required_specs), 3) if required_specs else 1.0

    anchors: List[str] = []
    for row in results:
        for item in (row.get("result_set") or {}).get("items") or []:
            for key in ("name", "title", "doi"):
                value = str(item.get(key) or "").strip()
                if len(value) >= 2:
                    anchors.append(value)
    operation_tokens = {
        "yearly_counts": ("年度发文", "发文量"),
        "yoy_growth": ("同比", "增长"),
        "top_keywords": ("热门关键词",),
        "keyword_growth": ("关键词增长", "新兴方向"),
        "topic_period_compare": ("主题阶段", "热点变化", "领域变化", "内容缺口", "预测限制"),
        "period_hotspot_compare": ("热点窗口对比", "热点变化"),
        "keywords_by_periods": ("子主题的阶段变化",),
        "topic_keyword_counts": ("专题发展", "主题词命中", "主题命中统计"),
        "topic_yearly": ("年度变化", "主题趋势", "逐年命中"),
        "top_authors": ("作者",),
        "authors_by_keyword": ("代表作者", "相关作者"),
        "representative_authors_by_topic": ("代表作者",),
        "top_institutions": ("机构",),
        "institutions_by_keyword": ("相关机构",),
        "institution_stability": ("长期高产机构",),
        "author_profile": ("发文", "作者"),
        "author_papers": ("作者论文", "全部发文"),
        "papers_for_authors": ("论文", "发文"),
        "topic_papers": ("主题概览", "主题论文", "检索口径", "论文"),
        "representative_papers_by_topic": ("代表论文",),
        "representative_papers_by_institution": ("代表论文",),
        "papers_by_top_keywords": ("研究方向", "代表论文"),
        "author_topic_summary": ("研究主题",),
        "author_direction_evolution": ("方向演化",),
        "author_collaborators": ("合作者",),
        "author_network": ("作者合作网络",),
        "institution_network": ("机构合作网络",),
        "coauthored_papers": ("代表论文", "合作论文"),
        "submission_fit": ("投稿", "匹配"),
        "submission_guidance": ("投稿建议",),
        "research_methods": ("常见研究方法", "方法相关关键词"),
        "submission_opportunities": ("潜在投稿机会",),
        "data_scope_notice": ("数据范围说明",),
        "clarification": ("请提供", "请补充"),
    }
    relevant_tokens = tuple(
        token
        for row in results
        for token in operation_tokens.get(str(row.get("operation") or ""), ())
    )
    units = [unit.strip() for unit in re.split(r"(?m)(?=^##\s+)", answer or "") if unit.strip()]
    relevant_units = 0.0
    scope_tokens = ("统计范围", "数据限制", "证据不足", "不支持", "无法", "未完年", "投稿建议")
    for unit in units:
        if (
            any(anchor in unit for anchor in anchors)
            or any(token in unit for token in scope_tokens)
            or any(token in unit for token in relevant_tokens)
        ):
            relevant_units += 1.0
        elif any(str(row.get("operation") or "") in unit for row in results):
            relevant_units += 0.5
    arp = round(relevant_units / len(units), 3) if units else (1.0 if not answer else 0.0)

    allowed_papers = collect_allowed_papers(state)
    allowed_numbers = collect_allowed_numbers(state)
    allowed_labels = collect_allowed_labels(state)
    claims: List[Tuple[str, str, int, bool]] = []
    doi_pattern = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
    for match in doi_pattern.finditer(answer or ""):
        doi = match.group(0).rstrip(").,;]}>\"'").lower()
        claims.append(("doi", doi, 2, doi in allowed_papers))
        paper = allowed_papers.get(doi) or {}
        line_start = (answer or "").rfind("\n", 0, match.start()) + 1
        line_end = (answer or "").find("\n", match.end())
        line = (answer or "")[line_start : line_end if line_end >= 0 else None]
        expected_title = str(paper.get("title") or "").strip()
        if paper and expected_title:
            claims.append(("paper_title", expected_title, 2, expected_title in line))
        expected_year = paper.get("year")
        displayed_year = re.search(r"[（(](\d{4})[年）)]", line)
        if paper and expected_year is not None and displayed_year:
            claims.append(
                (
                    "paper_year",
                    displayed_year.group(1),
                    2,
                    int(displayed_year.group(1)) == int(expected_year),
                )
            )
    number_pattern = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(篇|%|个百分点|年)")
    for match in number_pattern.finditer(answer or ""):
        value = match.group(1)
        normalized = str(int(float(value))) if float(value).is_integer() else value
        numeric_match = value in allowed_numbers or normalized in allowed_numbers
        if not numeric_match:
            try:
                numeric_match = any(abs(float(candidate) - float(value)) < 1e-9 for candidate in allowed_numbers)
            except (TypeError, ValueError):
                numeric_match = False
        claims.append(("number", value + match.group(2), 2, numeric_match))
    for match in re.finditer(r"(?m)^\s*\d+[.、)]\s*\*\*(.+?)\*\*", answer or ""):
        label = match.group(1).strip()
        parts = [part.strip() for part in re.split(r"↔|→|—", label) if part.strip()]
        claims.append(
            (
                "entity",
                label,
                2,
                label in allowed_labels or (len(parts) > 1 and all(part in allowed_labels for part in parts)),
            )
        )
    total_weight = sum(weight for _, _, weight, _ in claims)
    correct_weight = sum(weight for _, _, weight, ok in claims if ok)
    ea = round(correct_weight / total_weight, 3) if total_weight else 1.0

    violations: List[str] = []
    if len(results) != len((state.get("query_plan") or {}).get("operations") or []):
        violations.append("operation_without_status")
    for row in results:
        if row.get("status") == "error":
            violations.append(f"operation_error:{row.get('op_id')}")
        if row.get("status") == "unsupported" and any(
            "未知 operation" in str(reason) for reason in row.get("missing_requirements") or []
        ):
            violations.append(f"unknown_operation:{row.get('operation')}")
    for kind, value, _, ok in claims:
        if not ok:
            violations.append(f"unsupported_{kind}:{value}")
    for op_id in answer_coverage.get("missing_operation_ids") or []:
        violations.append(f"answer_missing_operation:{op_id}")

    return {
        "answer_relevance_precision": arp,
        "required_goal_coverage": rgc,
        "evidence_accuracy": ea,
        "hard_gate_passed": not violations,
        "hard_gate_violations": violations,
        "claim_count": len(claims),
        "answer_units": len(units),
        "answer_coverage": answer_coverage,
    }
