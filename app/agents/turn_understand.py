from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.agents.understand import extract_author_name, extract_year_window


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _ordinal(question: str) -> Optional[int]:
    match = re.search(r"第\s*(\d{1,2}|[一二三四五六七八九十两])\s*(?:位|名|个)?", question or "")
    if not match:
        return None
    raw = match.group(1)
    return int(raw) if raw.isdigit() else _CN_NUM.get(raw)


def _new_question_signal(question: str) -> bool:
    q = question or ""
    return bool(
        re.search(r"(?:近|过去|最近)\s*(?:\d+|[一二三四五六七八九十两]+)\s*年", q)
        and re.search(r"机构|趋势|热点|关键词|投稿|研究方向|作者排名|发文最多", q)
    )


def _top_n(question: str, default: int = 10) -> int:
    match = re.search(r"(?:Top|前)\s*(\d{1,2})", question or "", re.I)
    if not match:
        match = re.search(r"(?:推荐|列出|找出)\s*(\d{1,2})\s*个", question or "")
    if match:
        return max(1, min(int(match.group(1)), 50))
    cn_match = re.search(
        r"([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u4e24])\s*个?(?:研究)?(?:方向|主题|领域)",
        question or "",
    )
    return _CN_NUM.get(cn_match.group(1), default) if cn_match else default


def _selection_count(question: str) -> Optional[int]:
    match = re.search(
        r"前\s*(\d{1,2}|[一二三四五六七八九十两])\s*"
        r"(?:(?:位|名|个)(?:作者)?|(?:的)?作者)",
        question or "",
    )
    if not match:
        return None
    raw = match.group(1)
    return int(raw) if raw.isdigit() else _CN_NUM.get(raw)


def _clarification_plan(message: str, action: str = "select") -> Dict[str, Any]:
    return {
        "task": "clarification",
        "action": action,
        "sources": ["sql"],
        "sql_ops": ["clarification"],
        "locked": True,
        "focus": message,
    }


def _structured_new_question(question: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Zero-model path for high-confidence product task families."""
    q = question or ""
    if re.search(r"某位作者|指定作者", q) and not re.search(
        r"[一-龥]{2,4}(?:老师|教授|研究员)", q
    ):
        message = "请提供需要分析的具体作者姓名，例如“徐建明”。"
        from app.agents.operation_contracts import normalize_query_plan

        plan = normalize_query_plan(_clarification_plan(message, action="query"), q)
        plan["locked"] = True
        return {
            "kind": "ambiguous", "action": "query", "target_turn_id": None,
            "target_entity": "author", "selector": None, "explicit_constraints": {},
            "confidence": 1.0, "needs_clarification": True,
            "clarification": message, "source": "deterministic-core",
        }, plan

    author = extract_author_name(q)
    from app.agents.understand import extract_author_pair
    pair = extract_author_pair(q)
    author_b = pair[1] if pair else None
    if pair:
        author = pair[0]
    ranking = bool(
        re.search(r"作者", q)
        and re.search(r"发文最多|发文量|高产|排名|前\s*\d+", q)
        and not (re.search(r"论文|文章", q) and re.search(r"列出|展开|具体", q))
    )
    publication_trend = bool(
        re.search(r"发文|发表论文|论文数量", q)
        and re.search(r"每年|逐年|趋势|数量变化|增长最快", q)
    )
    direction_rank = bool(
        re.search(r"研究方向|研究领域|热点|热门关键词", q)
        and re.search(r"最多|排名|前\s*\d+|哪些|演变|变化", q)
    )
    institution_task = bool(
        re.search(r"机构|单位|大学|学院|研究院|研究所", q)
        and re.search(r"高产|发文|论文|作者|合作|关系|排名|最多", q)
    )
    author_detail = bool(
        (author or author_b)
        and re.search(r"发表过|论文|发文|研究方向|研究主题|合作伙伴|合作者|研究团队", q)
    )
    topic_analysis = bool(
        re.search(r"水稻|人工智能|AI技术|基因编辑|农业机器人|智能农业|传统农业", q, re.I)
        and re.search(r"趋势|发展|变化|演变|相关论文|相关研究|合作|团队|投稿|推荐", q)
    )
    publication_trend = publication_trend and not topic_analysis
    network_or_diversity = bool(
        re.search(r"合作最紧密|核心研究团队|核心作者网络|机构之间.*合作|同时覆盖多个研究方向", q)
    )
    journal_summary = bool(
        re.search(r"发展历程|优势领域", q)
        and re.search(r"核心作者|团队|热点|未来趋势|投稿建议", q)
    )
    submission = bool(
        re.search(r"适合.{0,20}投稿|是否有相关论文|推荐.{0,12}相关研究", q)
        and re.search(r"研究方向|论文主题|方向投稿|相关论文|相关研究", q)
    )
    unsupported_citations = bool(re.search(r"被引用|引用次数|高被引|被引次数|citation", q, re.I))
    international_collaboration = bool(
        re.search(r"国际合作|跨国合作|哪些国家", q)
        and re.search(r"合作|国家|机构", q)
    )
    topic_paper_classification = bool(
        re.search(r"标题|关键词", q)
        and re.search(r"包含", q)
        and re.search(r"按照.*(?:方向|主题).*分类|分类", q)
    )
    submission_research = bool(
        re.search(r"投稿前调研|潜在投稿机会", q)
        and re.search(r"热门方向|研究方法|投稿机会", q)
    )
    period_hotspot_compare = bool(
        re.search(r"近\s*(?:\d+|[一二三四五六七八九十两]+)\s*年", q)
        and re.search(r"前\s*(?:\d+|[一二三四五六七八九十两]+)\s*年", q)
        and re.search(r"热点|研究方向|主题", q)
        and re.search(r"比较|对比|变化|差异", q)
    )
    field_evolution = bool(
        re.search(r"(?:接受|收录)\s*(?:文章|论文)", q)
        and re.search(r"领域|方向|主题", q)
        and re.search(r"变化|演变|变迁|趋势", q)
    )
    core = bool(
        re.search(
            r"策划.{0,12}专题|"
            r"(?:领域|接受文章).*(?:变化|增长|减弱)|"
            r"(?:核心作者|核心团队|研究团队).*(?:方向|变化|演化)|"
            r"机构.*长期.*高产|长期.*高产.*机构|"
            r"下一阶段|内容缺口|未来可能.*热点|"
            r"关于.{1,20}研究有哪些.*主要关注|"
            r"前\s*(?:\d+|[一二三四五六七八九十两]+)\s*年.*后\s*(?:\d+|[一二三四五六七八九十两]+)\s*年|"
            r"核心作者和机构.*合作|核心作者.*机构.*如何合作|"
            r"研究方向是?.{1,20}适合投稿|"
            r"[一-龥A-Za-z·]{2,4}发表过哪些论文.*(?:方向|合作)"
        , q)
    )
    if not any(
        (
            ranking,
            publication_trend,
            direction_rank,
            institution_task,
            author_detail,
            topic_analysis,
            network_or_diversity,
            journal_summary,
            submission,
            unsupported_citations,
            international_collaboration,
            topic_paper_classification,
            submission_research,
            period_hotspot_compare,
            field_evolution,
            core,
        )
    ):
        return None, None

    y0, y1 = extract_year_window(q, default_last_n=None)
    if y0 is None and re.search(r"近年|最近几年", q):
        y1 = datetime.now().year
        y0 = y1 - 4
    if publication_trend or unsupported_citations or international_collaboration or field_evolution or (
        re.search(r"研究团队", q) and re.search(r"影响力|核心|方向.*变化", q)
    ):
        author, author_b = None, None
    institution = None
    match = re.search(r"([一-龥]{2,20}(?:大学|学院|研究院|研究所|科学院))", q)
    if match:
        institution = match.group(1)
    from app.agents.intent_schema import _seed_topics_from_question
    topics = _seed_topics_from_question(q)
    if re.search(r"农业人工智能|农业.{0,3}(?:AI|ai)", q):
        # 「人工智能」在农业期刊中仍可命中食品/营养论文；用农业场景中的
        # 主流技术词构成可追溯的相邻检索口径，避免将无关论文归入「农业 AI」。
        topics = ["深度学习", "机器学习", "机器视觉", "智能农业", "农业机器人"]

    rag_queries: List[str] = []
    extra_plan: Dict[str, Any] = {}
    if unsupported_citations:
        task, initial_ops = "unsupported_citations", ["unsupported_citations"]
    elif period_hotspot_compare:
        task, initial_ops = "period_hotspot_compare", ["period_hotspot_compare"]
    elif field_evolution:
        task, initial_ops = "field_evolution", [
            "yearly_counts", "top_keywords", "keyword_growth", "topic_period_compare",
            "representative_papers_by_topic",
        ]
    elif submission_research:
        task, initial_ops = "submission_research", [
            "top_keywords", "papers_by_top_keywords", "research_methods",
            "submission_opportunities",
        ]
        extra_plan["top_n_directions"] = 3
    elif international_collaboration:
        task, initial_ops = "international_collaboration", [
            "data_scope_notice", "top_institutions", "institution_network"
        ]
    elif re.search(r"中国作者", q) and re.search(r"机构", q):
        task, initial_ops = "top_institutions", ["data_scope_notice", "top_institutions"]
    elif re.search(r"研究团队", q) and re.search(r"影响力|核心", q) and re.search(r"方向|变化", q):
        task, initial_ops = "top_teams", [
            "data_scope_notice", "top_authors", "papers_for_authors",
            "author_direction_evolution", "author_collaborators"
        ]
        extra_plan["max_items"] = 40
    elif topic_paper_classification:
        task, initial_ops = "topic_paper_classification", ["topic_papers", "papers_by_top_keywords"]
        extra_plan["expand_topic_directions"] = True
        extra_plan["top_n_directions"] = 5
        extra_plan["max_items"] = 30
    elif ranking and re.search(r"机构|单位", q):
        task, initial_ops = "top_institutions", ["top_institutions"]
    elif ranking and not core:
        task, initial_ops = "top_authors", ["top_authors"]
    elif publication_trend:
        task, initial_ops = "yearly_growth", ["yearly_counts", "yoy_growth"]
    elif re.search(r"作者", q) and re.search(r"同时覆盖|多个研究方向|跨方向|多方向", q):
        task, initial_ops = "author_direction_diversity", ["author_direction_diversity"]
    elif direction_rank and not re.search(r"变化|演变", q):
        task, initial_ops = "hot_topics", ["top_keywords", "papers_by_top_keywords"]
    elif re.search(r"发文.*(?:最多|排名)|高产|机构.*排名", q) and re.search(r"机构|单位", q):
        task, initial_ops = "top_institutions", ["top_institutions"]
    elif institution and re.search(r"作者|论文|成果", q):
        task, initial_ops = "institution_authors", ["institution_authors"]
    elif author_b:
        task, initial_ops = "coauthored_papers", ["coauthored_papers"]
    elif author:
        task, initial_ops = "author_profile", ["author_profile"]
    elif submission:
        task, initial_ops = "submission_fit", ["submission_fit"]
    elif journal_summary:
        task, initial_ops = "journal_overview", ["journal_overview"]
    elif topics and re.search(r"趋势|发展|变化|演变", q):
        task, initial_ops = "topic_evolution", [
            "topic_keyword_counts", "topic_yearly", "keywords_by_periods"
        ]
    else:
        task, initial_ops = "generic", []
    explicit_rule_match = any(
        (
            ranking,
            publication_trend,
            direction_rank,
            institution_task,
            author_detail,
            topic_analysis,
            network_or_diversity,
            journal_summary,
            submission,
            unsupported_citations,
            international_collaboration,
            topic_paper_classification,
            submission_research,
            period_hotspot_compare,
            field_evolution,
        )
    )
    rule_confidence = 0.98 if explicit_rule_match and task != "generic" else 0.55
    if re.search(r"农业人工智能|农业.{0,3}(?:AI|ai)", q):
        initial_ops = ["data_scope_notice"] + [
            operation for operation in initial_ops if operation != "data_scope_notice"
        ]
    base = {
        "task": task,
        "main_task": task,
        "action": "query",
        "targets": [],
        "sources": ["sql"],
        "sql_ops": initial_ops,
        "rag_queries": rag_queries,
        "year_start": y0,
        "year_end": y1,
        "top_n": _top_n(q),
        "author_name": author,
        "author_name_b": author_b,
        "institution": institution,
        "keywords": topics,
        "complexity": "simple",
        "plan_source": "deterministic",
        "rule_confidence": rule_confidence,
        "locked": True,
        "focus": "按期刊问答核心任务规格逐项查询并验收证据。",
        **extra_plan,
    }
    from app.agents.operation_contracts import normalize_query_plan

    plan = normalize_query_plan(base, q)
    plan["locked"] = True
    if journal_summary or publication_trend or field_evolution:
        target_entity = "journal"
    elif institution:
        target_entity = "institution"
    elif author or re.search(r"作者|团队", q):
        target_entity = "author"
    elif topics or re.search(r"主题|热点|方向|领域|专题", q):
        target_entity = "topic"
    elif institution_task:
        target_entity = "institution"
    else:
        target_entity = "journal"
    action = "compare" if re.search(
        r"比较|对比|从.{2,12}到.{2,12}(?:变化|演变)|前\s*(?:\d+|[一二三四五六七八九十两]+)\s*年.*后", q
    ) else "query"
    intent = {
        "kind": "new_question",
        "action": action,
        "target_turn_id": None,
        "target_entity": target_entity,
        "selector": {"top_n": _top_n(q)} if ranking else None,
        "explicit_constraints": {"year_start": plan.get("year_start"), "year_end": plan.get("year_end")},
        "confidence": rule_confidence,
        "needs_clarification": False,
        "source": "deterministic-core",
    }
    return intent, plan


def understand_contextual_turn(
    question: str,
    previous_turn: Optional[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return (TurnIntent, locked QueryPlan) for deterministic follow-ups."""
    q = (question or "").strip()
    if not q:
        return None, None
    structured = _structured_new_question(q)
    if not previous_turn:
        needs_context = bool(
            re.fullmatch(r"(?:继续|继续列出|继续展开|还有|剩余|下一批)[。！!？?\s]*", q)
            or _ordinal(q)
            or re.search(r"(?:这些|上述|他们|她们|其|该作者).{0,10}(?:论文|发文|文章|排序|展开|列出)", q)
        )
        if needs_context and structured[0] is None:
            message = "当前会话中没有可引用的上一轮结果，请先说明要查询的作者、论文或机构。"
            return {
                "kind": "ambiguous", "action": "select", "target_turn_id": None,
                "target_entity": None, "selector": None, "explicit_constraints": {},
                "confidence": 1.0, "needs_clarification": True,
                "clarification": message, "source": "context",
            }, _clarification_plan(message)
        return structured
    contextual_signal = bool(
        _ordinal(q)
        or _selection_count(q)
        or re.search(r"最后一位|最后一个|其中.*发文最多|这些作者.*发文最多", q)
        or re.fullmatch(r"(?:继续|继续列出|继续展开|还有|剩余|下一批)[。！!？?\s]*", q)
        or re.search(r"(?:这些|上述|他们|她们|其|他|她|该作者).{0,12}(?:论文|发文|文章|排序|展开|列出|具体)", q)
    )
    if structured[0] is not None and not contextual_signal:
        return structured
    if _new_question_signal(q):
        structured = _structured_new_question(q)
        if structured[0] is not None:
            return structured
        return {
            "kind": "new_question",
            "action": "query",
            "target_turn_id": None,
            "target_entity": None,
            "selector": None,
            "explicit_constraints": {},
            "confidence": 0.99,
            "needs_clarification": False,
            "source": "standalone-signal",
        }, None

    result_set = previous_turn.get("result_set") or {}
    items = list(result_set.get("items") or [])
    constraints = dict(result_set.get("constraints") or {})
    prior_plan = dict(previous_turn.get("query_plan") or {})
    continuation = dict(previous_turn.get("continuation") or {})

    papers = [item for item in items if item.get("type") == "paper" and item.get("doi")]
    if (
        papers
        and re.search(r"(?:这些|上述|其|该批).{0,8}(?:论文|发文|文章)", q)
        and re.search(r"主题|方向|关键词|相似|共性|共同点|重合", q)
    ):
        paper_by_doi: Dict[str, Dict[str, Any]] = {}
        paper_authors: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for item in papers:
            doi = str(item["doi"])
            paper_by_doi.setdefault(doi, item)
            author_id = str(item.get("author_id") or "").strip()
            author_name = str(item.get("author_name") or "").strip()
            if author_id or author_name:
                key = author_id or f"name:{author_name}"
                paper_authors.setdefault(doi, {})[key] = {
                    "author_id": author_id or None,
                    "name": author_name or author_id,
                }
        unique_papers = list(paper_by_doi.values())
        intent = {
            "kind": "followup",
            "action": "summarize",
            "target_turn_id": previous_turn.get("turn_id"),
            "target_entity": "paper",
            "selector": {"scope": "all"},
            "explicit_constraints": {},
            "inherited_constraints": constraints,
            "confidence": 1.0,
            "needs_clarification": False,
            "source": "context",
        }
        return intent, {
            "task": "paper_set_topic_summary",
            "main_task": "paper_set_topic_summary",
            "action": "summarize",
            "targets": unique_papers,
            "dois": [paper["doi"] for paper in unique_papers],
            "paper_authors": [
                {"doi": doi, "authors": list(paper_authors.get(doi, {}).values())}
                for doi in paper_by_doi
            ],
            "source_record_count": len(papers),
            "constraints": constraints,
            "sources": ["sql"],
            "operations": ["paper_set_topic_summary"],
            "sql_ops": ["paper_set_topic_summary"],
            "year_start": constraints.get("year_start"),
            "year_end": constraints.get("year_end"),
            "keywords": [],
            "top_n": 20,
            "complexity": "simple",
            "plan_source": "conversation",
            "locked": True,
            "focus": "仅归纳上一轮论文集合的关键词主题及其重合度。",
        }
    if papers and re.search(r"这些|上述|其", q) and re.search(r"按.*年|年份.*排序|时间.*排序", q):
        ordered = sorted(
            papers,
            key=lambda item: (int(item.get("year") or 0), str(item.get("title") or "")),
            reverse=not bool(re.search(r"升序|从早到晚", q)),
        )
        intent = {
            "kind": "followup",
            "action": "sort",
            "target_turn_id": previous_turn.get("turn_id"),
            "target_entity": "paper",
            "selector": {"scope": "all"},
            "explicit_constraints": {"sort": "year_asc" if re.search(r"升序|从早到晚", q) else "year_desc"},
            "inherited_constraints": constraints,
            "confidence": 1.0,
            "needs_clarification": False,
            "source": "context",
        }
        return intent, {
            "task": "resultset_papers",
            "action": "sort",
            "targets": ordered,
            "constraints": constraints,
            "sources": ["sql"],
            "operations": ["present_resultset"],
            "sql_ops": ["present_resultset"],
            "year_start": constraints.get("year_start"),
            "year_end": constraints.get("year_end"),
            "max_items": 100,
            "max_chars": 24000,
            "presentation": {"group_by": "author", "sort": intent["explicit_constraints"]["sort"]},
            "complexity": "simple",
            "plan_source": "conversation",
            "locked": True,
            "focus": "仅对上一轮论文结果集重新排序。",
        }

    if re.fullmatch(r"(?:继续|继续列出|继续展开|还有|剩余|下一批)[。！!？?\s]*", q):
        if continuation.get("has_more"):
            intent = {
                "kind": "followup",
                "action": "continue",
                "target_turn_id": previous_turn.get("turn_id"),
                "target_entity": "paper",
                "selector": None,
                "explicit_constraints": {},
                "confidence": 1.0,
                "needs_clarification": False,
                "source": "context",
            }
            plan = dict(prior_plan)
            plan["offset"] = int(continuation.get("next_offset") or 0)
            target_op_id = continuation.get("operation_id")
            if target_op_id:
                plan["operations"] = [
                    op for op in (prior_plan.get("operations") or [])
                    if isinstance(op, dict) and op.get("id") == target_op_id
                ]
                plan["sql_ops"] = [
                    str(op.get("type")) for op in plan["operations"]
                    if op.get("source") == "sql"
                ]
                plan["kg_ops"] = [
                    str(op.get("type")) for op in plan["operations"]
                    if op.get("source") == "kg"
                ]
                plan["sources"] = list(dict.fromkeys(
                    str(op.get("source")) for op in plan["operations"] if op.get("source")
                ))
            plan["locked"] = True
            plan["action"] = "continue"
            return intent, plan
        clarification = "上一轮没有可继续的截断结果，请说明要查询或展开的内容。"
        return {
            "kind": "ambiguous",
            "action": "continue",
            "target_turn_id": previous_turn.get("turn_id"),
            "target_entity": None,
            "selector": None,
            "explicit_constraints": {},
            "confidence": 1.0,
            "needs_clarification": True,
            "clarification": clarification,
            "source": "context",
        }, {
            "task": "clarification",
            "action": "continue",
            "sources": ["sql"],
            "operations": ["clarification"],
            "sql_ops": ["clarification"],
            "locked": True,
            "focus": clarification,
        }

    authors = [item for item in items if item.get("type") == "author" and item.get("id")]
    ordinal = _ordinal(q)
    selection_count = _selection_count(q)
    paper_noun = bool(re.search(r"论文|发文|文章|文献", q))
    asks_papers = bool(
        (paper_noun and (
            re.search(r"展开|具体|详细|列(?:出|一下|一列)?|查看|给出|罗列|展示|有哪些|什么", q)
            or ordinal
            or selection_count
        ))
        or (ordinal and "作者" in q)
    )
    if authors and asks_papers:
        selected = authors
        singular_pronoun = bool(re.search(r"(?:他|她|该作者)的", q))
        if ordinal is not None:
            selected = authors[ordinal - 1 : ordinal]
        elif selection_count is not None:
            selected = authors[:selection_count]
        elif re.search(r"最后一位|最后一个", q):
            selected = authors[-1:]
        elif re.search(r"其中.*发文最多|这些作者.*发文最多", q):
            selected = sorted(
                authors,
                key=lambda row: (-int(row.get("paper_count") or 0), int(row.get("position") or 10**6)),
            )[:1]
        elif singular_pronoun and len(authors) > 1:
            intent = {
                "kind": "ambiguous",
                "action": "select",
                "target_turn_id": previous_turn.get("turn_id"),
                "target_entity": "author",
                "selector": None,
                "explicit_constraints": {},
                "confidence": 1.0,
                "needs_clarification": True,
                "clarification": "上一轮有多位作者，请说明要展开哪一位（例如“第 3 位作者”）。",
                "source": "context",
            }
            return intent, {
                "task": "clarification",
                "action": "select",
                "sources": ["sql"],
                "sql_ops": [],
                "locked": True,
                "focus": intent["clarification"],
            }
        if not selected:
            message = "选择器超出上一轮结果范围，请换一个有效序号。"
            return {
                "kind": "ambiguous", "action": "select",
                "target_turn_id": previous_turn.get("turn_id"),
                "target_entity": "author", "selector": {"ordinal": ordinal},
                "explicit_constraints": {}, "confidence": 1.0,
                "needs_clarification": True, "clarification": message,
                "source": "context",
            }, _clarification_plan(message)
        intent = {
            "kind": "followup",
            "action": "expand",
            "target_turn_id": previous_turn.get("turn_id"),
            "target_entity": "author",
            "selector": (
                {"ordinal": ordinal} if ordinal else (
                    {"first": selection_count} if selection_count else (
                        {"last": 1} if re.search(r"最后一位|最后一个", q) else (
                            {"sort": "paper_count", "first": 1}
                            if re.search(r"其中.*发文最多|这些作者.*发文最多", q)
                            else {"scope": "all"}
                        )
                    )
                )
            ),
            "explicit_constraints": {},
            "inherited_constraints": constraints,
            "confidence": 1.0,
            "needs_clarification": False,
            "source": "context",
        }
        explicit_y0, explicit_y1 = extract_year_window(q, default_last_n=None)
        final_y0 = explicit_y0 if explicit_y0 is not None else constraints.get("year_start")
        final_y1 = explicit_y1 if explicit_y1 is not None else constraints.get("year_end")
        if explicit_y0 is not None or explicit_y1 is not None:
            intent["explicit_constraints"] = {"year_start": final_y0, "year_end": final_y1}
        return intent, {
            "task": "authors_papers",
            "action": "expand",
            "targets": selected,
            "constraints": constraints,
            "sources": ["sql"],
            "operations": ["papers_for_authors"],
            "sql_ops": ["papers_for_authors"],
            "author_ids": [a["id"] for a in selected],
            "year_start": final_y0,
            "year_end": final_y1,
            "keywords": list(constraints.get("keywords") or []),
            "offset": 0,
            "max_items": 100,
            "max_chars": 24000,
            "presentation": {"group_by": "author", "include_all": True},
            "continuation": None,
            "complexity": "simple",
            "plan_source": "conversation",
            "locked": True,
            "focus": "列出上一轮所选作者在同一时间范围内的全部论文。",
        }

    return None, None
