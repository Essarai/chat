"""Canonical multi-operation query-plan and evidence contracts.

Legacy planners may still emit ``task``/``sql_ops``.  This module is the only
compatibility boundary: downstream execution consumes ``operations``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class OperationSpec:
    id: str
    type: str
    source: str = "sql"
    target: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    required: bool = True
    evidence_contract: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OperationResult:
    op_id: str
    operation: str
    status: str
    data: Dict[str, Any] = field(default_factory=dict)
    result_set: Dict[str, Any] = field(default_factory=dict)
    missing_requirements: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CoverageReport:
    required_count: int
    complete_count: int
    partial_count: int
    unsupported_count: int
    error_count: int
    coverage: float
    needs_llm: bool
    operations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


META_TOPICS = {
    "热点", "研究热点", "主题", "研究主题", "领域", "研究领域", "方向",
    "研究方向", "热门关键词", "热门词", "热词", "关键词", "新兴研究方向",
    "未来研究方向", "接受文章", "投稿领域", "收录领域",
}


CONTRACTS: Dict[str, Dict[str, Any]] = {
    "top_authors": {"kind": "ranking", "items": "authors", "required_fields": ["author_id", "paper_count"]},
    "top_institutions": {"kind": "ranking", "items": "institutions", "required_fields": ["institution", "paper_count"]},
    "top_keywords": {"kind": "ranking", "items": "keywords", "required_fields": ["keyword", "paper_count"]},
    "papers_for_authors": {"kind": "papers", "items": "authors", "required_fields": ["doi", "title_zh", "year"]},
    "author_profile": {"kind": "author", "items": "papers", "required_fields": ["doi", "title_zh", "year"]},
    "author_topic_summary": {"kind": "ranking", "items": "keywords", "required_fields": ["keyword", "paper_count"]},
    "topic_yearly": {"kind": "trend", "min_periods": 2},
    "topic_keyword_counts": {"kind": "topic", "items": "topic_keywords"},
    "yearly_counts": {"kind": "trend", "items": "yearly", "min_periods": 2},
    "yoy_growth": {"kind": "trend", "items": "yoy", "min_periods": 2},
    "keyword_growth": {"kind": "trend", "items": "keyword_growth", "min_periods": 2},
    "topic_period_compare": {"kind": "comparison", "items": "periods", "min_periods": 2},
    "author_direction_evolution": {"kind": "evolution", "items": "periods", "min_periods": 2, "min_papers": 3},
    "author_direction_diversity": {"kind": "ranking", "items": "authors", "required_fields": ["author_id", "direction_count"]},
    "institution_stability": {"kind": "stability", "items": "institutions", "min_periods": 5},
    "papers_by_top_keywords": {"kind": "papers", "items": "directions"},
    "topic_coverage": {"kind": "coverage", "allow_empty": True},
    "submission_fit": {"kind": "coverage", "allow_empty": True},
    "unsupported_citations": {"kind": "unsupported"},
    "clarification": {"kind": "clarification"},
}


def _clean_topics(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for value in values or []:
        topic = str(value or "").strip().strip("“”\"'‘’")
        if topic and topic not in META_TOPICS and topic not in out:
            out.append(topic)
    return out


def _operation_types_from_question(question: str, plan: Dict[str, Any]) -> List[str]:
    """Augment the primary plan with explicitly requested answer goals.

    This is a deterministic planner fallback.  LLM-produced
    ``requested_operations`` takes precedence when present.
    """
    q = question or ""
    task = str(plan.get("task") or "generic")
    ops = list(plan.get("sql_ops") or [])
    for op in plan.get("kg_ops") or []:
        if op not in ops:
            ops.append(op)
    if plan.get("rag_queries") and "semantic_search" not in ops:
        ops.append("semantic_search")

    def add(name: str) -> None:
        if name not in ops:
            ops.append(name)

    if re.search(r"热门关键词|热门词|热词", q):
        ops = [op for op in ops if op not in {"topic_keyword_counts", "topic_yearly", "yearly_counts", "yoy_growth"}]
        add("top_keywords")
        add("keyword_growth")
    if re.search(r"新兴|未来可能|未来趋势|重点发展|重点关注", q):
        add("keyword_growth")
        add("papers_by_top_keywords")
    if re.search(r"长期.*高产|长期保持高产|稳定高产", q) and re.search(r"机构|单位", q):
        ops = [op for op in ops if op != "top_institutions"]
        add("institution_stability")
    if re.search(r"同时覆盖|多个研究方向|跨方向|多方向", q) and re.search(r"作者", q):
        ops = [op for op in ops if op not in {"top_authors", "author_keywords_sample"}]
        add("author_direction_diversity")
    if re.search(r"合作最紧密|核心研究团队", q):
        ops = [op for op in ops if op not in {"top_authors", "author_keywords_sample"}]
        add("authors_by_keyword")
        add("institutions_by_keyword")
    if re.search(r"核心作者网络", q):
        ops = [op for op in ops if op != "semantic_search"]
        add("top_authors")
        add("author_keywords_sample")
    if re.search(r"研究方向|研究主题", q) and re.search(r"变化|演变|轨迹", q) and plan.get("author_name"):
        ops = [op for op in ops if op != "author_profile"]
        add("author_direction_evolution")
    if re.search(r"研究团队", q) and re.search(r"方向.*变化|变化.*方向", q):
        ops = [op for op in ops if op != "author_profile"]
        add("author_direction_evolution")
    if re.search(r"从.{2,12}到.{2,12}(?:的)?(?:变化|演变)|主题差异|热点演变|研究热点演变", q):
        ops = [op for op in ops if op != "hotspot_compare"]
        add("topic_period_compare")
        add("keyword_growth")
    if task == "topic_evolution" and re.search(r"趋势|发展|变化|演变", q):
        add("topic_yearly")
    if plan.get("author_name") and re.search(r"论文|发表过|发文", q) and re.search(r"主题|研究方向", q):
        add("author_topic_summary")
    if task == "top_authors" and re.search(r"(?:和|及|并|以及|与).{0,6}(?:其|他们|作者)?.{0,4}(?:发文情况|具体发文|论文)|发文最多的作者和其发文", q):
        add("papers_for_authors")
    if task == "journal_overview":
        if re.search(r"热点|优势领域", q):
            add("top_keywords")
        if re.search(r"未来|趋势", q):
            add("keyword_growth")
        if re.search(r"投稿建议|投稿", q):
            add("submission_guidance")
    return ops or ([task] if task != "generic" else [])


def normalize_query_plan(
    plan: Dict[str, Any],
    question: str = "",
    intent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out = dict(plan or {})
    intent = intent or {}
    out.setdefault("task", "generic")
    out.setdefault("main_task", out.get("task") or "generic")
    out.setdefault("action", "query")
    out.setdefault("targets", [])
    topics = _clean_topics(out.get("keywords") or intent.get("topic") or [])
    if not topics:
        for term in ("人工智能", "水稻", "基因编辑", "番茄", "镉"):
            if term in (question or ""):
                topics.append(term)
    out["keywords"] = topics
    constraints = dict(out.get("constraints") or {})
    constraints.setdefault("year_start", out.get("year_start"))
    constraints.setdefault("year_end", out.get("year_end"))
    constraints["keywords"] = topics
    out["constraints"] = constraints
    requested = intent.get("requested_operations") or []
    requested_types = [
        str(x.get("type") if isinstance(x, dict) else x)
        for x in requested
        if str(x.get("type") if isinstance(x, dict) else x).strip()
    ]
    op_types = requested_types or _operation_types_from_question(question, out)
    existing = out.get("operations") or []
    if existing and isinstance(existing[0], dict):
        specs = [dict(x) for x in existing]
    else:
        specs = []
        for index, op_type in enumerate(op_types, 1):
            source = "kg" if op_type in (out.get("kg_ops") or []) else (
                "rag" if op_type == "semantic_search" else "sql"
            )
            params = {
                "year_start": out.get("year_start"),
                "year_end": out.get("year_end"),
                "keywords": topics,
                "top_n": out.get("top_n"),
                "author_name": out.get("author_name"),
                "author_ids": out.get("author_ids"),
                "institution": out.get("institution"),
            }
            depends: List[str] = []
            if op_type == "papers_for_authors" and "top_authors" in op_types:
                depends = [f"op_{op_types.index('top_authors') + 1}_top_authors"]
            specs.append(
                OperationSpec(
                    id=f"op_{index}_{op_type}",
                    type=op_type,
                    source=source,
                    target={"entity": intent.get("entity")},
                    params={k: v for k, v in params.items() if v is not None and v != []},
                    depends_on=depends,
                    required=True,
                    evidence_contract=dict(CONTRACTS.get(op_type) or {}),
                ).to_dict()
            )
    out["operations"] = specs
    out["sources"] = list(dict.fromkeys([str(s.get("source") or "sql") for s in specs])) or list(out.get("sources") or [])
    out["sql_ops"] = [s["type"] for s in specs if s.get("source") == "sql" and s.get("type") != "submission_guidance"]
    out["kg_ops"] = [s["type"] for s in specs if s.get("source") == "kg"]
    out.setdefault("presentation", {})
    out.setdefault("continuation", None)
    return out


def operation_types(plan: Dict[str, Any], source: Optional[str] = None) -> List[str]:
    return [
        str(op.get("type"))
        for op in (plan.get("operations") or [])
        if isinstance(op, dict) and (source is None or op.get("source") == source)
    ]
