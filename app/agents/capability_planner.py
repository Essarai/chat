"""Map Intent Schema → query_plan consumed by existing executors."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.agents.intent_schema import (
    CONFIDENCE_THRESHOLD,
    expand_submission_keywords,
    extract_quoted_topic_phrase,
)


def _years(intent: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    tr = intent.get("time_range") or {}
    return tr.get("start"), tr.get("end")


def _asks_authors(question: str) -> bool:
    return bool(re.search(r"作者|学者|代表(性)?成果|代表论文", question or ""))


def _real_topics(topics: List[str]) -> List[str]:
    meta = {
        "热点", "研究热点", "主题", "研究主题", "领域", "研究领域",
        "方向", "研究方向", "接受文章", "收录领域", "投稿领域",
        "热门关键词", "热门词", "热词", "关键词",
    }
    out: List[str] = []
    for t in topics or []:
        s = str(t).strip()
        if s and s not in meta and s not in out:
            out.append(s)
    return out


def plan_from_intent(intent: Dict[str, Any], question: str = "") -> Optional[Dict[str, Any]]:
    """Return a query_plan dict, or None if schema cannot be mapped confidently."""
    if not intent:
        return None
    conf = float(intent.get("confidence") or 0.0)
    if conf < CONFIDENCE_THRESHOLD and intent.get("goal") != "refuse":
        return None

    entity = intent.get("entity") or "journal"
    op = intent.get("operation") or "search"
    goal = intent.get("goal") or "research_analysis"
    topics = _real_topics(list(intent.get("topic") or []))
    y0, y1 = _years(intent)
    top_n = intent.get("top_n")
    author = intent.get("author_name")
    author_b = intent.get("author_name_b")
    institution = intent.get("institution")
    sources = list(intent.get("sources") or ["sql"])
    notes = str(intent.get("notes") or "")
    q = question or ""

    requested = [
        str(item.get("type") if isinstance(item, dict) else item).strip()
        for item in (intent.get("requested_operations") or [])
    ]
    requested = [item for item in requested if item]
    if requested:
        from app.agents.operation_contracts import CONTRACTS

        known = [item for item in requested if item in CONTRACTS]
        topic_required = {
            "topic_yearly", "topic_keyword_counts", "topic_papers",
            "authors_by_keyword", "institutions_by_keyword", "topic_coverage",
        }
        incompatible = {item for item in known if item in topic_required and not topics}
        known = [item for item in known if item not in incompatible]
        # A topic trend with no named topic means journal-wide composition
        # change. Reconcile the proposal to executable capabilities instead of
        # running a topic operation with an empty topic list.
        if (
            incompatible
            and op == "trend"
            and entity in {"topic", "journal", "paper"}
            and intent.get("metric") == "keyword_freq"
        ):
            for item in (
                "top_keywords", "keyword_growth", "topic_period_compare",
                "representative_papers_by_topic",
            ):
                if item not in known:
                    known.append(item)
        if known:
            known_set = set(known)
            if {"top_keywords", "keyword_growth", "topic_period_compare"}.issubset(known_set):
                inferred_task = "hotspot_compare"
            elif "author_profile" in known_set:
                inferred_task = "author_profile"
            elif "top_authors" in known_set:
                inferred_task = "top_authors"
            elif "submission_fit" in known_set:
                inferred_task = "submission_fit"
            else:
                inferred_task = known[0]
            task = str(intent.get("legacy_task") or inferred_task)
            return {
                "task": task,
                "main_task": task,
                "sources": list(dict.fromkeys(
                    "kg" if item in {"keyword_ego", "author_ego", "paper_neighborhood"}
                    else "rag" if item == "semantic_search"
                    else "sql"
                    for item in known
                )),
                "sql_ops": [
                    item for item in known
                    if item not in {"keyword_ego", "author_ego", "paper_neighborhood", "semantic_search"}
                ],
                "kg_ops": [item for item in known if item in {"keyword_ego", "author_ego", "paper_neighborhood"}],
                "rag_queries": [q] if "semantic_search" in known else [],
                "year_start": y0,
                "year_end": y1,
                "top_n": int(top_n or 10),
                "author_name": author,
                "author_name_b": author_b,
                "institution": institution,
                "keywords": topics,
                "focus": notes or "按语义理解得到的操作计划查询并回答。",
                "complexity": "complex" if len(sources) > 1 else "simple",
                "plan_source": "schema",
            }

    if goal == "refuse" or intent.get("legacy_task") == "unsupported_citations":
        return {
            "task": "unsupported_citations",
            "sources": ["sql"],
            "sql_ops": ["unsupported_citations"],
            "kg_ops": [],
            "rag_queries": [],
            "focus": "说明本库无被引数据，无法排名高被引论文。",
            "year_start": y0,
            "year_end": y1,
            "keywords": [],
            "complexity": "simple",
            "plan_source": "schema",
        }

    if goal == "submission_fit" or (
        op == "coverage" and goal == "submission_fit"
    ):
        kws = expand_submission_keywords(q, topics)
        if not kws:
            kws = topics[:6]
        if not kws:
            kws = ["人工智能"]
        return {
            "task": "submission_fit",
            "sources": ["sql"],
            "sql_ops": ["submission_fit"],
            "kg_ops": [],
            "rag_queries": [],
            "focus": (
                "严格依据关键词命中与代表论文评估投稿适合度；"
                "禁止用证据外旧文或相邻技术（如仅 GPS/GIS）论证“适合投稿”。"
            ),
            "year_start": y0,
            "year_end": y1,
            "keywords": kws,
            "topic_phrase": extract_quoted_topic_phrase(q),
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Journal overview / comprehensive
    if notes == "journal_overview" or (
        entity == "journal"
        and op == "summarize"
        and re.search(r"综合|优势|发展历程|核心作者团队", q)
    ):
        return {
            "task": "journal_overview",
            "sources": ["sql"],
            "sql_ops": ["journal_overview"],
            "year_start": y0,
            "year_end": y1,
            "keywords": [],
            "focus": "概括发展历程、核心作者/机构线索与方向趋势；投稿建议须基于证据。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Institution collaboration → complex multi-source
    if notes == "institution_collab" or (
        entity == "institution"
        and op == "compare"
        and re.search(r"合作", q)
    ):
        return {
            "task": "generic",
            "sources": ["sql", "kg"],
            "sql_ops": ["top_institutions"],
            "kg_ops": [],
            "rag_queries": [],
            "year_start": y0,
            "year_end": y1,
            "top_n": int(top_n or 15),
            "keywords": [],
            "focus": "结合高产机构名单与图谱关系，分析主要机构合作线索；禁止编造合作边。",
            "complexity": "complex",
            "plan_source": "schema",
        }

    # Authors covering multiple directions
    if notes == "multi_direction_authors" or (
        entity == "author"
        and op == "rank"
        and intent.get("metric") == "keyword_freq"
        and not topics
    ):
        return {
            "task": "top_teams",
            "sources": ["sql"],
            "sql_ops": ["top_authors", "author_keywords_sample"],
            "year_start": y0,
            "year_end": y1,
            "top_n": int(top_n or 10),
            "keywords": [],
            "focus": "列出高产作者及其关键词样本，识别同时覆盖多方向的作者。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Core author network
    if notes == "author_network" or (
        entity == "author"
        and op == "summarize"
        and re.search(r"网络", q)
    ):
        return {
            "task": "top_teams",
            "sources": ["sql", "kg"],
            "sql_ops": ["top_authors", "author_keywords_sample"],
            "kg_ops": [],
            "year_start": y0,
            "year_end": y1,
            "focus": "基于高产作者与合作线索归纳核心作者网络；勿编造边。",
            "complexity": "complex",
            "plan_source": "schema",
        }

    if author and author_b and op in {"search", "profile", "summarize"}:
        return {
            "task": "coauthored_papers",
            "sources": ["sql"],
            "sql_ops": ["coauthored_papers"],
            "author_name": author,
            "author_name_b": author_b,
            "focus": f"只列出 {author} 与 {author_b} 的合著论文。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Author profile: named person paper list / collaborators / trajectory
    if author and not author_b and (
        (entity == "author" and op == "profile")
        or (
            entity in {"author", "paper"}
            and op in {"search", "profile", "summarize"}
            and re.search(r"发表|论文|发文|合作|轨迹|主题|伙伴|团队|有没有|是否有|在平台", q)
        )
    ):
        return {
            "task": "author_profile",
            "sources": ["sql"],
            "sql_ops": ["author_profile"],
            "kg_ops": [],
            "author_name": author,
            "focus": f"回答作者 {author} 的相关问题。",
            "year_start": None,
            "year_end": None,
            "keywords": [],
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Topic collab / core team under a keyword
    if (
        entity == "author"
        and op == "rank"
        and topics
        and re.search(r"合作|团队|紧密", q)
    ):
        return {
            "task": "keyword_collab",
            "sources": ["sql", "kg"],
            "sql_ops": ["authors_by_keyword", "institutions_by_keyword"],
            "keywords": topics[:3],
            "year_start": y0,
            "year_end": y1,
            "focus": f"围绕主题「{topics[0]}」找出高产作者/机构合作线索。",
            "complexity": "complex",
            "plan_source": "schema",
        }

    if entity == "author" and op == "rank" and topics:
        n = int(top_n or 10)
        return {
            "task": "keyword_authors",
            "sources": ["sql"],
            "sql_ops": ["authors_by_keyword"],
            "keywords": topics[:3],
            "year_start": y0,
            "year_end": y1,
            "top_n": n,
            "papers_per_author": 2,
            "focus": f"在主题「{topics[0]}」相关论文中按发文量列出前 {n} 位作者。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    if entity == "author" and op == "rank":
        n = int(top_n or 10)
        return {
            "task": "top_authors",
            "sources": ["sql"],
            "sql_ops": ["top_authors"],
            "year_start": y0,
            "year_end": y1,
            "top_n": n,
            "keywords": [],
            "focus": f"按发文量列出前 {n} 位作者。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    if entity == "author" and op == "search" and topics:
        return {
            "task": "keyword_authors",
            "sources": ["sql"],
            "sql_ops": ["authors_by_keyword"],
            "keywords": topics[:3],
            "year_start": y0,
            "year_end": y1,
            "top_n": int(top_n or 30),
            "focus": "列出关键词相关作者。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    if entity == "institution" and op == "rank" and not _asks_authors(q):
        n = int(top_n or 10)
        return {
            "task": "top_institutions",
            "sources": ["sql"],
            "sql_ops": ["top_institutions"],
            "year_start": y0,
            "year_end": y1,
            "top_n": n,
            "focus": f"列出发文量前 {n} 的机构。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    if entity == "institution" and institution and (
        op in {"summarize", "profile", "search"} or _asks_authors(q)
    ):
        n = int(top_n or 8)
        return {
            "task": "institution_authors",
            "sources": ["sql"],
            "sql_ops": ["institution_authors"],
            "institution": institution,
            "top_n_authors": n,
            "papers_per_author": 3,
            "year_start": y0,
            "year_end": y1,
            "keywords": [],
            "focus": (
                f"列出署名单位含「{institution}」的高产作者及代表论文；"
                "勿用校史类文献冒充成果。"
            ),
            "complexity": "simple",
            "plan_source": "schema",
        }

    if entity == "institution" and op == "rank":
        n = int(top_n or 10)
        return {
            "task": "top_institutions",
            "sources": ["sql"],
            "sql_ops": ["top_institutions"],
            "year_start": y0,
            "year_end": y1,
            "top_n": n,
            "focus": f"列出发文量前 {n} 的机构。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    if op == "compare" and entity in {"topic", "journal"}:
        return {
            "task": "hotspot_compare",
            "sources": ["sql"],
            "sql_ops": ["hotspot_compare"],
            "year_start": None,
            "year_end": None,
            "keywords": topics,
            "focus": "对比前后时间窗热门关键词变化。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Hotspots are a ranked set of themes, not a literal topic named「热点」.
    if (
        entity in {"topic", "journal", "paper"}
        and re.search(r"(?:有哪些|什么|主要|当前|近期|近年).{0,8}(?:热点|热门领域|热门方向)|(?:热点|热门领域|热门方向).{0,8}(?:有哪些|是什么)", q)
        and not re.search(r"变化|演变|变迁|对比|前.*后", q)
    ):
        return {
            "task": "top_directions_with_papers",
            "sources": ["sql"],
            "sql_ops": ["top_keywords", "papers_by_top_keywords"],
            "year_start": y0,
            "year_end": y1,
            "top_n_directions": int(top_n or 10),
            "keywords": [],
            "focus": "按指定时间范围的关键词发文量列出研究热点，并给出代表论文。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Emerging / inventory directions
    if entity in {"topic", "journal"} and (
        goal == "inventory"
        or (op in {"rank", "summarize"} and re.search(r"研究方向|新兴|未来|重点", q))
    ):
        n = int(top_n or 8)
        # Prefer recent window for 新兴/近年来
        if y0 is None and y1 is None and re.search(r"近年|新兴|未来", q):
            from datetime import datetime

            end = datetime.now().year
            y0, y1 = end - 4, end
        return {
            "task": "top_directions_with_papers",
            "sources": ["sql"],
            "sql_ops": ["top_keywords", "papers_by_top_keywords"],
            "year_start": y0,
            "year_end": y1,
            "top_n_directions": n,
            "keywords": [],
            "focus": "基于关键词发文量归纳主要/新兴研究方向并给代表论文。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Journal-wide theme evolution means keyword/hotspot composition changes,
    # never publication-volume growth. No named topic is required here.
    if (
        op == "trend"
        and entity in {"journal", "topic", "paper"}
        and re.search(r"(?:主题|研究方向|领域|收录方向|接受文章|热点).*(?:变化|演变|变迁|发展)|(?:变化|演变|变迁).*(?:主题|研究方向|领域|热点)", q)
        and not re.search(r"每年发文|逐年发文|发文数量|发文趋势|增长最快|同比", q)
    ):
        return {
            "task": "hotspot_compare",
            "sources": ["sql"],
            "sql_ops": ["hotspot_compare"],
            "year_start": None,
            "year_end": y1,
            "keywords": [],
            "focus": "对比前后时间窗的高频关键词，说明期刊研究主题的增强、新增、回落与淡出。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    # Topic-scoped evolution / trend
    if op == "trend" and topics and entity in {"topic", "journal", "paper"}:
        if not re.search(r"每年发文|发文数量|发表论文数量|增长最快", q):
            return {
                "task": "topic_evolution",
                "sources": ["sql"],
                "sql_ops": ["topic_keyword_counts", "topic_yearly"],
                "keywords": topics[:6],
                "year_start": y0,
                "year_end": y1,
                "focus": (
                    f"围绕主题「{'、'.join(topics[:3])}」给出命中统计与逐年变化；"
                    "禁止用全刊发文趋势冒充专题趋势。"
                ),
                "complexity": "simple",
                "plan_source": "schema",
            }

    if op == "trend" and entity in {"journal", "topic", "paper"}:
        ops = ["yearly_counts", "yoy_growth"]
        if "关键词" in q or "热词" in q:
            ops.append("top_keywords")
        return {
            "task": "yearly_growth",
            "sources": ["sql"],
            "sql_ops": ops,
            "year_start": y0,
            "year_end": y1,
            "keywords": [],
            "focus": "列出逐年发文与增速"
            + ("及热门关键词。" if "top_keywords" in ops else "。"),
            "complexity": "simple",
            "plan_source": "schema",
        }

    if op == "coverage" and topics and goal != "submission_fit":
        return {
            "task": "topic_coverage",
            "sources": ["sql"],
            "sql_ops": ["topic_coverage"],
            "keywords": topics[:8],
            "year_start": y0,
            "year_end": y1,
            "focus": "统计专题关键词命中与代表论文。",
            "complexity": "simple",
            "plan_source": "schema",
        }

    if len(sources) > 1 or (
        op in {"summarize", "recommend"}
        and entity in {"topic", "journal"}
        and "kg" in sources
    ):
        return {
            "task": "generic",
            "sources": sources,
            "sql_ops": ["yearly_counts", "top_keywords"] if "sql" in sources else [],
            "kg_ops": ["keyword_ego"] if "kg" in sources and topics else [],
            "rag_queries": (
                topics[:4]
                if "rag" in sources and topics
                else ([question] if "rag" in sources else [])
            ),
            "keywords": topics,
            "year_start": y0,
            "year_end": y1,
            "focus": intent.get("notes") or "多源取证后综合回答。",
            "complexity": "complex",
            "plan_source": "schema",
        }

    return None
