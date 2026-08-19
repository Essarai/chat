"""Canonical multi-operation query-plan and evidence contracts.

Legacy planners may still emit ``task``/``sql_ops``.  This module is the only
compatibility boundary: downstream execution consumes ``operations``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
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
    "authors_by_keyword": {"kind": "ranking", "items": "authors", "required_fields": ["author_id", "paper_count"]},
    "institutions_by_keyword": {"kind": "ranking", "items": "institutions", "required_fields": ["institution", "paper_count"]},
    "papers_for_authors": {"kind": "papers", "items": "authors", "required_fields": ["doi", "title_zh", "year"]},
    "present_resultset": {"kind": "papers", "items": "authors"},
    "author_profile": {"kind": "author", "items": "papers", "required_fields": ["doi", "title_zh", "year"]},
    "author_topic_summary": {"kind": "ranking", "items": "keywords", "required_fields": ["keyword", "paper_count"]},
    "paper_set_topic_summary": {"kind": "ranking", "items": "keywords", "required_fields": ["keyword", "paper_count"]},
    "topic_yearly": {"kind": "trend", "min_periods": 2},
    "topic_keyword_counts": {"kind": "topic", "items": "topic_keywords"},
    "yearly_counts": {"kind": "trend", "items": "yearly", "min_periods": 2},
    "yoy_growth": {"kind": "trend", "items": "yoy", "min_periods": 2},
    "keyword_growth": {"kind": "trend", "items": "keyword_growth", "min_periods": 2},
    "topic_period_compare": {"kind": "comparison", "items": "periods", "min_periods": 2},
    "period_hotspot_compare": {"kind": "comparison", "items": "periods", "min_periods": 2},
    "author_direction_evolution": {"kind": "evolution", "items": "periods", "min_periods": 2, "min_papers": 3},
    "author_direction_diversity": {"kind": "ranking", "items": "authors", "required_fields": ["author_id", "direction_count"]},
    "institution_stability": {"kind": "stability", "items": "institutions", "min_periods": 5},
    "papers_by_top_keywords": {"kind": "papers", "items": "directions"},
    "keywords_by_periods": {"kind": "comparison", "items": "periods", "min_periods": 2},
    "research_methods": {"kind": "ranking", "items": "methods", "required_fields": ["keyword", "paper_count"]},
    "submission_opportunities": {"kind": "opportunity"},
    "author_keywords_sample": {"kind": "ranking", "items": "author_keywords", "required_fields": ["author_id", "paper_count"]},
    "institution_authors": {"kind": "ranking", "items": "authors", "required_fields": ["author_id", "paper_count"]},
    "topic_papers": {"kind": "papers", "items": "papers"},
    "representative_papers_by_topic": {"kind": "papers", "items": "topics"},
    "representative_authors_by_topic": {"kind": "ranking", "items": "authors", "required_fields": ["author_id", "paper_count"]},
    "author_papers": {"kind": "papers", "items": "papers"},
    "author_collaborators": {"kind": "network", "items": "collaborators", "required_fields": ["author_id", "co_papers"]},
    "author_network": {"kind": "network", "items": "edges", "required_fields": ["source_id", "target_id", "paper_count"]},
    "institution_network": {"kind": "network", "items": "edges", "required_fields": ["source_id", "target_id", "paper_count"]},
    "representative_papers_by_institution": {"kind": "papers", "items": "papers"},
    "coauthored_papers": {"kind": "papers", "items": "papers"},
    "topic_coverage": {"kind": "coverage", "allow_empty": True},
    "submission_fit": {"kind": "coverage", "allow_empty": True},
    "submission_guidance": {"kind": "derived"},
    "data_scope_notice": {"kind": "notice"},
    "journal_overview": {"kind": "overview"},
    "semantic_search": {"kind": "papers", "items": "hits"},
    "keyword_ego": {"kind": "graph"},
    "author_ego": {"kind": "graph"},
    "paper_neighborhood": {"kind": "graph"},
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
    if task == "clarification":
        return ["clarification"]
    for op in plan.get("kg_ops") or []:
        if op not in ops:
            ops.append(op)
    if plan.get("rag_queries") and "semantic_search" not in ops:
        ops.append("semantic_search")

    def add(name: str) -> None:
        if name not in ops:
            ops.append(name)

    def replace_legacy(name: str, replacements: List[str]) -> None:
        nonlocal ops
        if name not in ops:
            return
        position = ops.index(name)
        ops = [op for op in ops if op != name]
        for replacement in reversed(replacements):
            if replacement not in ops:
                ops.insert(position, replacement)

    # Compatibility aliases are normalized here; executors never interpret
    # the old umbrella task as if it were one complete piece of evidence.
    replace_legacy("hotspot_compare", ["top_keywords", "keyword_growth", "topic_period_compare"])
    if task == "period_hotspot_compare":
        return ["period_hotspot_compare"]
    if task == "field_evolution":
        return list(dict.fromkeys(ops or [
            "yearly_counts", "top_keywords", "keyword_growth", "topic_period_compare",
            "representative_papers_by_topic",
        ]))

    if not plan.get("keywords") and re.search(r"发文|发表论文|论文数量", q) and re.search(r"每年|逐年|趋势|数量变化|增长最快", q):
        add("yearly_counts")
        add("yoy_growth")
    if re.search(r"发文量最多的(?:研究)?方向|研究方向.*(?:最多|排名)", q):
        add("top_keywords")
        add("papers_by_top_keywords")
    if task == "top_authors" and re.search(r"主要研究方向|研究方向", q):
        add("author_keywords_sample")
    if re.search(r"机构|单位", q) and re.search(r"发文.*(?:最多|排名)|高产", q):
        add("top_institutions")
    if plan.get("institution") and re.search(r"作者|论文|成果", q):
        add("institution_authors")
    if plan.get("author_name") and plan.get("author_name_b"):
        add("coauthored_papers")
    if plan.get("keywords") and re.search(r"发文趋势|发展趋势|应用研究.*变化", q):
        add("topic_keyword_counts")
        add("topic_yearly")
    if re.search(r"机构之间.{0,8}(?:合作|关系)|机构.*合作关系", q):
        add("top_institutions")
        add("institution_network")
        add("representative_papers_by_institution")
    if re.search(r"核心作者网络", q):
        add("top_authors")
        add("author_network")
        add("coauthored_papers")
    if plan.get("keywords") and re.search(r"合作最紧密|核心研究团队", q):
        add("authors_by_keyword")
        add("institutions_by_keyword")
        add("author_network")
        add("institution_network")
        add("coauthored_papers")

    if re.search(r"热门关键词|热门词|热词", q):
        ops = [op for op in ops if op not in {"topic_keyword_counts", "topic_yearly", "yearly_counts", "yoy_growth"}]
        add("top_keywords")
        add("keyword_growth")
    if re.search(r"新兴|未来可能|未来趋势|重点发展|重点关注", q):
        add("keyword_growth")
        add("topic_period_compare")
        add("papers_by_top_keywords")
        add("representative_authors_by_topic")
    if re.search(r"长期.*高产|长期保持高产|稳定高产", q) and re.search(r"机构|单位", q):
        ops = [op for op in ops if op != "top_institutions"]
        add("institution_stability")
    if re.search(r"同时覆盖|多个研究方向|跨方向|多方向", q) and re.search(r"作者", q):
        ops = [
            op for op in ops
            if op not in {"top_authors", "author_keywords_sample", "top_keywords", "papers_by_top_keywords"}
        ]
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
    if not plan.get("keywords") and re.search(r"(?:领域|热点|研究方向).*(?:变化|演变|增长|减弱)|(?:变化|演变).*(?:领域|热点|研究方向)", q):
        add("yearly_counts")
        add("top_keywords")
        add("keyword_growth")
        add("topic_period_compare")
    if task == "submission_fit" or re.search(r"适合.{0,20}投稿|是否适合投|策划.{0,12}专题|推荐.{0,12}相关研究", q):
        add("submission_fit")
        add("topic_yearly")
        if re.search(r"论文|文献|参考", q):
            add("topic_papers")
        if re.search(r"作者|团队", q):
            add("authors_by_keyword")
        if re.search(r"机构|单位", q):
            add("institutions_by_keyword")
        add("submission_guidance")
    if re.search(r"核心作者|核心团队|研究团队", q) and re.search(r"方向|变化|演化", q):
        if re.search(r"影响力", q):
            add("data_scope_notice")
        add("top_authors")
        add("papers_for_authors")
        add("author_direction_evolution")
        add("author_collaborators")
    if re.search(r"长期.*高产|长期保持高产|稳定高产", q) and re.search(r"机构|单位", q):
        add("top_institutions")
        add("institution_stability")
        if re.search(r"合作", q):
            add("institution_network")
        add("representative_papers_by_institution")
    if plan.get("author_name"):
        if re.search(r"论文|发表|发文", q):
            add("author_papers")
        if re.search(r"主题|方向", q):
            add("author_topic_summary")
        if re.search(r"合作|伙伴|合作者", q):
            add("author_collaborators")
    if plan.get("keywords") and re.search(r"论文|文献|有哪些研究", q):
        add("topic_papers")
    if plan.get("keywords") and re.search(r"代表作者|核心作者|哪些作者", q):
        add("authors_by_keyword")
    if plan.get("keywords") and re.search(r"代表论文", q):
        add("representative_papers_by_topic")
    if plan.get("keywords") and re.search(r"核心作者|核心机构|合作网络|如何合作", q):
        add("authors_by_keyword")
        add("institutions_by_keyword")
        add("author_network")
        add("institution_network")
        add("coauthored_papers")
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
        if re.search(r"核心作者|团队", q):
            add("top_authors")
            add("author_network")

    topics = list(plan.get("keywords") or [])
    if len(topics) >= 2 and re.search(r"比较|对比|从.{2,12}到|差异|变化|演变", q):
        ops = [
            op for op in ops
            if op not in {"top_keywords", "keyword_growth", "yearly_counts", "yoy_growth", "topic_keyword_counts"}
        ]
        add("topic_period_compare")
        add("topic_yearly")
        add("representative_papers_by_topic")
    elif re.search(r"前\s*(?:\d+|[一二三四五六七八九十两]+)\s*年.*后\s*(?:\d+|[一二三四五六七八九十两]+)\s*年", q):
        add("representative_papers_by_topic")

    if topics and re.search(r"(?:关于|围绕).{0,20}(?:研究有哪些|有哪些研究|主要关注)|(?:研究有哪些|有哪些研究).{0,20}(?:关注|问题)", q):
        ops = [op for op in ops if op != "semantic_search"]
        add("topic_papers")
        add("topic_yearly")
        add("authors_by_keyword")

    if re.search(r"策划.{0,12}专题", q):
        add("authors_by_keyword")
        add("institutions_by_keyword")
        add("topic_papers")
    if re.search(r"国际合作|哪些国家", q):
        add("data_scope_notice")
        add("top_institutions")
        add("institution_network")
    if re.search(r"中国作者", q) and re.search(r"机构", q):
        add("data_scope_notice")
        add("top_institutions")
    if re.search(r"标题|关键词", q) and re.search(r"包含", q) and re.search(r"分类|研究方向", q):
        add("topic_papers")
        add("papers_by_top_keywords")

    def keep_ordered(required: List[str]) -> None:
        nonlocal ops
        unknown = [op for op in ops if op not in CONTRACTS]
        ops = list(dict.fromkeys(required + unknown))

    if plan.get("author_name") and plan.get("author_name_b"):
        keep_ordered(["coauthored_papers"])
    elif plan.get("author_name"):
        required = ["author_profile"]
        if re.search(r"论文|发表|发文", q):
            required.append("author_papers")
        if re.search(r"主题|方向", q):
            required.append("author_topic_summary")
        if re.search(r"方向|主题", q) and re.search(r"变化|演变|轨迹", q):
            required.append("author_direction_evolution")
        if re.search(r"合作|伙伴|合作者", q):
            required.append("author_collaborators")
        keep_ordered(required)
    elif re.search(r"国际合作|哪些国家", q):
        keep_ordered(["data_scope_notice", "top_institutions", "institution_network"])
    elif re.search(r"中国作者", q) and re.search(r"机构", q):
        keep_ordered(["data_scope_notice", "top_institutions"])
    elif re.search(r"标题|关键词", q) and re.search(r"包含", q) and re.search(r"分类|研究方向", q):
        keep_ordered(["topic_papers", "papers_by_top_keywords"])
    elif plan.get("institution") and re.search(r"作者|论文|成果", q):
        keep_ordered(["institution_authors"])
    elif re.search(r"策划.{0,12}专题", q):
        keep_ordered([
            "submission_fit", "topic_yearly", "papers_by_top_keywords",
            "authors_by_keyword", "institutions_by_keyword", "submission_guidance",
        ])
    elif task == "submission_fit" or re.search(r"适合.{0,20}投稿|是否有相关论文|推荐.{0,12}相关研究", q):
        required = ["submission_fit", "topic_yearly"]
        if re.search(r"论文|研究|参考", q):
            required.append("topic_papers")
        required.append("submission_guidance")
        keep_ordered(required)
    elif re.search(r"长期.*高产|长期保持高产|稳定高产", q) and re.search(r"机构|单位", q):
        required = ["institution_stability", "top_institutions"]
        if re.search(r"合作", q):
            required.append("institution_network")
        required.append("representative_papers_by_institution")
        keep_ordered(required)
    elif re.search(r"机构之间.{0,8}(?:合作|关系)|机构.*合作关系", q):
        keep_ordered(["top_institutions", "institution_network", "representative_papers_by_institution"])
    elif topics and re.search(r"合作最紧密|核心研究团队", q):
        keep_ordered([
            "authors_by_keyword", "institutions_by_keyword", "author_network",
            "institution_network", "coauthored_papers",
        ])
    elif re.search(r"核心作者网络", q):
        keep_ordered(["top_authors", "author_network", "coauthored_papers"])
    elif task == "journal_overview" or re.search(r"发展历程", q) and re.search(r"核心作者|团队|未来", q):
        required = ["journal_overview"]
        if re.search(r"核心作者|团队", q):
            required.extend(["top_authors", "author_network"])
        if re.search(r"未来|趋势", q):
            required.extend(["keyword_growth", "topic_period_compare"])
        if re.search(r"热点|优势领域", q):
            required.append("top_keywords")
        if re.search(r"投稿", q):
            required.append("submission_guidance")
        keep_ordered(required)
    elif re.search(r"核心作者|核心团队|研究团队", q) and re.search(r"方向|变化|演化", q):
        required = ["top_authors", "papers_for_authors", "author_direction_evolution", "author_collaborators"]
        if re.search(r"影响力", q):
            required.insert(0, "data_scope_notice")
        keep_ordered(required)
    elif re.search(r"下一阶段|内容缺口|未来可能|未来趋势|新兴(?:研究)?方向", q):
        keep_ordered(["keyword_growth", "topic_period_compare", "papers_by_top_keywords", "representative_authors_by_topic"])
    elif re.search(r"前\s*(?:\d+|[一二三四五六七八九十两]+)\s*年.*后\s*(?:\d+|[一二三四五六七八九十两]+)\s*年", q):
        keep_ordered(["topic_period_compare", "topic_yearly", "representative_papers_by_topic"])
    elif not topics and re.search(r"(?:领域|热点|研究方向).*(?:变化|演变|增长|减弱)|(?:变化|演变).*(?:领域|热点|研究方向)", q):
        keep_ordered(["yearly_counts", "top_keywords", "keyword_growth", "topic_period_compare", "representative_papers_by_topic"])
    elif topics and re.search(r"(?:关于|围绕).{0,20}(?:研究有哪些|有哪些研究|主要关注)|(?:研究有哪些|有哪些研究).{0,20}(?:关注|问题)", q):
        keep_ordered(["topic_papers", "topic_yearly", "authors_by_keyword"])
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
    if not out.get("author_name") and intent.get("author_name"):
        out["author_name"] = intent.get("author_name")
    if not out.get("author_name_b") and intent.get("author_name_b"):
        out["author_name_b"] = intent.get("author_name_b")
    if not out.get("institution") and intent.get("institution"):
        out["institution"] = intent.get("institution")
    from app.agents.understand import extract_year_window

    explicit_y0, explicit_y1 = extract_year_window(question)
    period_match = re.search(
        r"前\s*(\d+|[一二三四五六七八九十两]+)\s*年.*后\s*(\d+|[一二三四五六七八九十两]+)\s*年",
        question or "",
    )
    if period_match:
        cn = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        left = int(period_match.group(1)) if period_match.group(1).isdigit() else cn.get(period_match.group(1), 10)
        right = int(period_match.group(2)) if period_match.group(2).isdigit() else cn.get(period_match.group(2), 10)
        explicit_y1 = datetime.now().year - 1
        explicit_y0 = explicit_y1 - left - right + 1
    intent_range = intent.get("time_range") or {}
    if explicit_y0 is not None or explicit_y1 is not None:
        out["year_start"], out["year_end"] = explicit_y0, explicit_y1
    else:
        if out.get("year_start") is None and intent_range.get("start") is not None:
            out["year_start"] = intent_range.get("start")
        if out.get("year_end") is None and intent_range.get("end") is not None:
            out["year_end"] = intent_range.get("end")
        if (
            out.get("task") == "submission_fit"
            or re.search(r"策划.{0,12}专题|推荐.{0,12}相关研究", question or "")
            or (out.get("author_name") and not out.get("author_name_b"))
        ):
            out["year_start"], out["year_end"] = None, None
        if re.search(r"长期.*高产|长期保持高产|稳定高产", question or "") and re.search(r"机构|单位", question or ""):
            out["year_end"] = datetime.now().year - 1
            out["year_start"] = out["year_end"] - 9
    topics = _clean_topics(out.get("keywords") or intent.get("topic") or [])
    # A locked conversation plan is already the task contract.  In particular,
    # an explicit empty keyword list must not be repopulated from pronoun-like
    # follow-ups such as “这些发文的主题有什么相似性”.
    if not topics and not (out.get("locked") and "keywords" in out):
        from app.agents.intent_schema import _seed_topics_from_question

        topics = _clean_topics(_seed_topics_from_question(question))
    if not topics:
        match = re.search(r"策划[“\"「]?([\u4e00-\u9fffA-Za-z0-9]{2,12})[”\"」]?专题", question or "")
        if match:
            topics = _clean_topics([match.group(1)])
    if re.search(r"策划.{0,12}专题|适合.{0,20}投稿|是否适合投|推荐.{0,12}相关研究", question or ""):
        from app.agents.intent_schema import expand_submission_keywords

        expanded = _clean_topics(expand_submission_keywords(question, topics))
        if expanded:
            topics = expanded
        existing_primary = str(out.get("primary_topic") or "").strip()
        if existing_primary and existing_primary in topics:
            topics = [existing_primary] + [topic for topic in topics if topic != existing_primary]
    out["keywords"] = topics
    if re.search(r"策划.{0,12}专题|适合.{0,20}投稿|是否适合投|推荐.{0,12}相关研究", question or "") and topics:
        out["primary_topic"] = str(out.get("primary_topic") or topics[0])
        out["adjacent_topics"] = [topic for topic in topics if topic != out["primary_topic"]]
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
    # LLM proposals are hints, not authority. Deterministic augmentation keeps
    # explicit sub-goals that a model omitted, while unknown names remain in
    # the plan as unsupported instead of being silently treated as complete.
    op_types = list(requested_types)
    for op_type in _operation_types_from_question(question, out):
        if op_type not in op_types:
            op_types.append(op_type)
    existing = out.get("operations") or []
    if existing and isinstance(existing[0], dict):
        specs = []
        for index, raw in enumerate(existing, 1):
            spec = dict(raw)
            op_type = str(spec.get("type") or "").strip()
            if not op_type:
                continue
            spec.setdefault("id", f"op_{index}_{op_type}")
            spec.setdefault("required", True)
            spec["evidence_contract"] = dict(
                CONTRACTS.get(op_type)
                or {"kind": "unsupported", "reason": f"未知 operation: {op_type}"}
            )
            if op_type not in CONTRACTS:
                spec["source"] = "unsupported"
            specs.append(spec)
    else:
        specs = []
        for index, op_type in enumerate(op_types, 1):
            supported = op_type in CONTRACTS
            source = "kg" if op_type in (out.get("kg_ops") or []) else (
                "rag" if op_type == "semantic_search" else ("sql" if supported else "unsupported")
            )
            params = {
                "year_start": out.get("year_start"),
                "year_end": out.get("year_end"),
                "keywords": topics,
                "top_n": out.get("top_n"),
                "author_name": out.get("author_name"),
                "author_ids": out.get("author_ids"),
                "institution": out.get("institution"),
                "dois": out.get("dois"),
                "paper_authors": out.get("paper_authors"),
                "source_record_count": out.get("source_record_count"),
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
                    evidence_contract=dict(
                        CONTRACTS.get(op_type)
                        or {"kind": "unsupported", "reason": f"未知 operation: {op_type}"}
                    ),
                ).to_dict()
            )
    out["operations"] = specs
    out["sources"] = list(dict.fromkeys([str(s.get("source") or "sql") for s in specs])) or list(out.get("sources") or [])
    out["sql_ops"] = [s["type"] for s in specs if s.get("source") == "sql" and s.get("type") != "submission_guidance"]
    out["kg_ops"] = [s["type"] for s in specs if s.get("source") == "kg"]
    out.setdefault("presentation", {})
    out.setdefault("continuation", None)
    out.setdefault("offset", 0)
    out.setdefault("max_items", 100)
    out.setdefault("max_chars", 24000)
    return out


def operation_types(plan: Dict[str, Any], source: Optional[str] = None) -> List[str]:
    return [
        str(op.get("type"))
        for op in (plan.get("operations") or [])
        if isinstance(op, dict) and (source is None or op.get("source") == source)
    ]
