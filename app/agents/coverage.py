"""Operation-level evidence assessment and stable ResultSet construction."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.agents.operation_contracts import CoverageReport, OperationResult
from app.agents.state import JournalState


def _paper_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    papers: List[Dict[str, Any]] = []
    seen = set()

    def add(paper: Dict[str, Any], **extra: Any) -> None:
        doi = paper.get("doi")
        if not doi or doi in seen:
            return
        seen.add(doi)
        papers.append({
            "type": "paper", "id": doi, "doi": doi,
            "title": paper.get("title_zh") or paper.get("title"),
            "year": paper.get("year"), **extra,
        })

    for paper in data.get("papers") or data.get("recent_papers") or []:
        add(paper)
    for author in data.get("authors") or []:
        if not isinstance(author, dict):
            continue
        for paper in author.get("papers") or []:
            add(paper, author_id=author.get("author_id"), author_name=author.get("name_zh"))
    for direction in data.get("directions") or []:
        for paper in direction.get("papers") or []:
            add(paper, keyword=direction.get("keyword"))
    for period in data.get("periods") or []:
        for paper in period.get("papers") or []:
            add(paper, period=period.get("label"))
    return papers


def _result_items(op_type: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if op_type in {"top_authors", "author_direction_diversity", "authors_by_keyword"}:
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
    if op_type in {"top_keywords", "keyword_growth", "topic_period_compare", "author_topic_summary"}:
        rows = data.get("keywords") or data.get("keyword_growth") or data.get("topics") or []
        return [
            {"type": "keyword", "id": f"keyword:{row.get('keyword')}", "name": row.get("keyword"),
             "paper_count": row.get("paper_count") or row.get("late_count"), "position": index}
            for index, row in enumerate(rows, 1) if isinstance(row, dict) and row.get("keyword")
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
        return "unsupported", [str(data.get("reason") or "数据源不支持该指标")]
    if op_type == "submission_guidance":
        return "partial", ["该目标需要基于其他 operation 证据综合推断"]
    if contract.get("kind") == "clarification":
        return "complete", []
    if contract.get("kind") == "evolution":
        if int(data.get("total_papers") or 0) < int(contract.get("min_papers") or 3):
            missing.append("论文数量不足 3 篇")
        nonempty = [p for p in data.get("periods") or [] if int(p.get("paper_count") or 0) > 0 and p.get("keywords")]
        if len(nonempty) < int(contract.get("min_periods") or 2):
            missing.append("不足两个含主题证据的阶段")
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
        if not _paper_items(data):
            missing.append("无可追溯论文")
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
    elif contract.get("kind") == "topic":
        if not data.get(contract.get("items") or "topic_keywords"):
            missing.append("主题统计为空")
    elif contract.get("kind") == "coverage":
        if data.get("topic_keywords") is None and data.get("keywords_queried") is None:
            missing.append("缺少专题覆盖统计")
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
        result_set = {"type": op.get("type"), "items": items, "total_count": len(items)}
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
            covered = any(anchor in text for anchor in anchors) if anchors else bool(text.strip())
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
