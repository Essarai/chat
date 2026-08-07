"""Complexity Router: simple (single-source) vs complex (multi-hop / multi-source)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.agents.state import JournalState
from app.agents.understand import (
    _restore_author_pair,
    extract_year_window,
    llm_confirm_entities,
    regex_extract,
)

GENE_EDIT_KWS = ["基因编辑", "CRISPR", "基因组编辑", "基因敲除"]


def _years(question: str, entities: Dict[str, Any], default_n: Optional[int] = None):
    q = question or ""
    m_n = re.search(r"(?:过去|近|最近)\s*(\d{1,2})\s*年|(\d{1,2})\s*年来", q)
    if m_n:
        n = int(m_n.group(1) or m_n.group(2))
        end = datetime.now().year
        return end - n + 1, end
    y0, y1 = entities.get("year_start"), entities.get("year_end")
    if y0 is None and y1 is None:
        y0, y1 = extract_year_window(question, default_last_n=default_n)
    return y0, y1


def _top_n_from_question(question: str, default: int = 10) -> int:
    m = re.search(r"前\s*(\d{1,2})", question or "")
    if m:
        return max(1, min(int(m.group(1)), 50))
    return default


def _is_author_trajectory(question: str, entities: Dict[str, Any]) -> bool:
    q = question or ""
    if not entities.get("author_name") or entities.get("author_name_b"):
        return False
    return bool(
        re.search(
            r"研究轨迹|首次发表|主题变化|合作作者变化|合作者变化|研究主题变化",
            q,
        )
    )


def extract_node(state: JournalState) -> Dict[str, Any]:
    question = state.get("question") or ""
    prior = (state.get("entities") or {}).get("dois") or []
    draft = regex_extract(question, prior)
    try:
        entities = llm_confirm_entities(question, draft)
    except Exception as e:
        entities = dict(draft)
        entities["confirmed_intents"] = []
        entities["extract_meta"] = {
            "llm_confirmed": False,
            "fixes": f"llm确认失败: {e}",
        }
    entities = _restore_author_pair(question, entities, draft)
    return {"entities": entities, "stage": "extracted"}


def _complex_features(question: str, entities: Dict[str, Any]) -> List[str]:
    """Hard multi-source / multi-hop features only (no soft multi_clause)."""
    q = question or ""
    hits: List[str] = []
    # Personal author trajectory must stay simple — do not treat as journal topic/collab.
    if _is_author_trajectory(q, entities):
        return hits
    if re.search(
        r"(学术发展历程|发展历程|研究方向演变|核心作者团队|代表性机构|"
        r"主要研究方向演变|值得关注的研究方向)",
        q,
    ):
        hits.append("journal_overview_multi")
    if re.search(r"合作.*(作者|机构)|作者和机构|最紧密", q) and not re.search(
        r"合作作者变化|合作者变化|研究轨迹", q
    ):
        hits.append("collab_multi_source")
    if re.search(
        r"发表最多的.*(方向|主题)|三个研究方向|三大方向|(研究方向).{0,12}(重要)?成果",
        q,
        re.I,
    ):
        hits.append("directions_then_papers")
    if re.search(r"研究团队|核心团队|影响力最高", q) and re.search(
        r"方向|变化|演变", q
    ):
        hits.append("teams_and_evolution")
    if re.search(r"人工智能|机器学习|深度学习", q) and re.search(
        r"主题|演变|发展|变化", q
    ):
        hits.append("topic_evolution_multi")
    # domain topic evolution, e.g. 「水稻领域研究主题的发展变化」
    # Skip when asking about one author's theme change.
    if re.search(r"(研究)?主题.*(发展|演变|变化)|(发展|演变).*主题", q) and not (
        entities.get("author_name") and re.search(r"作者|轨迹|首次发表", q)
    ):
        hits.append("topic_evolution_multi")
    return hits


def _simple_match(
    question: str, entities: Dict[str, Any]
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """Return (source, reason, query_plan) if clearly simple."""
    q = question or ""
    y0, y1 = _years(q, entities, default_n=20)

    # Q9: no citation field in DB — refuse early
    if re.search(r"被引用|引用次数|高被引|被引次数|citation", q, re.I):
        return (
            "sql",
            "拒答：库无被引字段",
            {
                "task": "unsupported_citations",
                "sources": ["sql"],
                "sql_ops": ["unsupported_citations"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": "说明本库无被引数据，无法排名高被引论文。",
                "year_start": y0,
                "year_end": y1,
                "keywords": [],
            },
        )

    # Q15: institution top-N
    if re.search(
        r"前\s*\d+.*机构|机构.*前\s*\d+|排名前.*机构|机构.*排名|中国作者.*机构|机构有哪些",
        q,
    ) and re.search(r"机构|单位", q):
        top_n = _top_n_from_question(q, 10)
        return (
            "sql",
            "单源统计：机构发文排名",
            {
                "task": "top_institutions",
                "sources": ["sql"],
                "sql_ops": ["top_institutions"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": f"列出发文量前 {top_n} 的机构。",
                "year_start": y0,
                "year_end": y1,
                "top_n": top_n,
                "keywords": [],
            },
        )

    # Q14: hotspot decade compare — do not bind year_start to 「近N年」alone
    if re.search(
        r"热点.*变化|研究热点|近\s*\d+\s*年.*前\s*\d+\s*年|前\s*\d+\s*年.*近\s*\d+\s*年|"
        r"前后.*\d+\s*年|近10年.*前10年|前10年.*近10年",
        q,
    ):
        return (
            "sql",
            "单源统计：两窗热点对比",
            {
                "task": "hotspot_compare",
                "sources": ["sql"],
                "sql_ops": ["hotspot_compare"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": "对比前一时间窗与近一时间窗的热门关键词变化。",
                "year_start": None,
                "year_end": None,
                "keywords": [],
            },
        )

    # Q12: gene-edit / suitability — keyword coverage before RAG
    if re.search(r"适合投稿|是否适合投稿", q) or (
        re.search(r"基因编辑|CRISPR|基因组编辑", q, re.I)
        and re.search(r"投稿|相关论文|参考|研究方向为", q)
    ):
        kws = list(entities.get("keywords") or [])
        for term in GENE_EDIT_KWS:
            if term not in kws:
                kws.append(term)
        # also pull topic from 「研究方向为X」
        m = re.search(r"研究方向为([^，。；\s]{2,20})", q)
        if m:
            topic = m.group(1).strip()
            if topic and topic not in kws:
                kws.insert(0, topic)
        return (
            "sql",
            "单源：专题关键词覆盖与代表论文",
            {
                "task": "topic_coverage",
                "sources": ["sql"],
                "sql_ops": ["topic_coverage"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": "先统计专题关键词命中，再判断投稿适合度并仅推荐证据中的论文。",
                "year_start": y0,
                "year_end": y1,
                "keywords": kws[:8],
            },
        )

    # Q18: author trajectory (SQL-only; collaborators come from author_profile)
    if _is_author_trajectory(q, entities):
        return (
            "sql",
            "单源：作者研究轨迹",
            {
                "task": "author_profile",
                "sources": ["sql"],
                "sql_ops": ["author_profile"],
                "kg_ops": [],
                "rag_queries": [],
                "author_name": entities.get("author_name"),
                "focus": (
                    f"仅基于作者 {entities.get('author_name')} 本人的发文、"
                    "关键词与合作者回答研究轨迹；禁止引用他人论文。"
                ),
                "year_start": None,
                "year_end": None,
                "keywords": [],
            },
        )

    # Q1/Q2: yearly growth / growth-decline periods
    if re.search(
        r"(每年|逐年).*(发文|数量)|发文数量.*增长|增长最快|"
        r"增长期|下降期|同比|年度发文趋势",
        q,
    ) and not re.search(r"合作|机构|团队|方向.*成果|发展历程|热点", q):
        return (
            "sql",
            "单源统计：逐年发文与增速",
            {
                "task": "yearly_growth",
                "sources": ["sql"],
                "sql_ops": ["yearly_counts", "yoy_growth"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": (
                    "列出每年发文量与同比增速，识别快速增长期与下降期，"
                    "并指出增长最快的年份；对未完年份需注明。"
                ),
                "year_start": y0,
                "year_end": y1,
                "keywords": [],
            },
        )

    # keyword → authors
    if re.search(r"作者|哪些人|谁", q) and re.search(r"关键词|主题词|包含|含有", q):
        kws = list(entities.get("keywords") or [])
        return (
            "sql",
            "单源：关键词查作者",
            {
                "task": "keyword_authors",
                "sources": ["sql"],
                "sql_ops": ["authors_by_keyword"],
                "year_start": y0,
                "year_end": y1,
                "keywords": kws,
                "focus": "列出关键词相关作者。",
            },
        )

    # two-author coauthored papers
    if (
        entities.get("author_name")
        and entities.get("author_name_b")
        and re.search(r"合作|合著|共著", q)
        and re.search(r"发文|论文|著作|文章|哪些", q)
    ):
        return (
            "sql",
            "单源：两人合著论文",
            {
                "task": "coauthored_papers",
                "sources": ["sql"],
                "sql_ops": ["coauthored_papers"],
                "author_name": entities.get("author_name"),
                "author_name_b": entities.get("author_name_b"),
                "focus": (
                    f"只列出 {entities.get('author_name')} 与 "
                    f"{entities.get('author_name_b')} 的合著论文。"
                ),
            },
        )

    # author profile (single author)
    if (
        entities.get("author_name")
        and not entities.get("author_name_b")
        and re.search(r"发文|概况|情况|合作|机构|统计|基金|轨迹", q)
    ):
        sources = ["sql"]
        kg_ops: List[str] = []
        return (
            "sql",
            "单源：作者个人统计",
            {
                "task": "author_profile",
                "sources": sources,
                "sql_ops": ["author_profile"],
                "kg_ops": kg_ops,
                "author_name": entities.get("author_name"),
                "focus": f"回答作者 {entities.get('author_name')} 的相关问题。",
            },
        )

    # pure rag literature — block when structured stats cues present
    if re.search(r"(相关|有哪些).*(研究|论文|文献)|讲了什么|如何|综述", q) and not re.search(
        r"统计|发文量|趋势|合作|机构|团队|历程|排名|热点|投稿|被引",
        q,
    ):
        return (
            "rag",
            "单源：语义文献检索",
            {
                "task": "generic",
                "sources": ["rag"],
                "rag_queries": [q],
                "focus": "基于文献内容回答。",
                "year_start": y0,
                "year_end": y1,
            },
        )

    return None


def classify_complexity(
    question: str, entities: Dict[str, Any]
) -> Dict[str, Any]:
    q = question or ""
    simple = _simple_match(question, entities)

    # Prefer simple for refuse / structured SQL / author trajectory
    if simple:
        source, reason, plan = simple
        task = plan.get("task") or ""
        if task in {
            "unsupported_citations",
            "hotspot_compare",
            "top_institutions",
            "topic_coverage",
            "yearly_growth",
            "author_profile",
            "coauthored_papers",
            "keyword_authors",
        } or _is_author_trajectory(q, entities):
            return {
                "complexity": "simple",
                "suggested_source": source,
                "reason": reason,
                "escalated": False,
                "query_plan": plan,
            }

    complex_hits = _complex_features(question, entities)
    if complex_hits:
        return {
            "complexity": "complex",
            "suggested_source": None,
            "reason": "复杂特征: " + ",".join(complex_hits),
            "escalated": False,
            "complex_features": complex_hits,
        }
    if simple:
        source, reason, plan = simple
        return {
            "complexity": "simple",
            "suggested_source": source,
            "reason": reason,
            "escalated": False,
            "query_plan": plan,
        }

    # Soft complex — but never for structured ranking cues
    if re.search(r"统计|排名|机构|热点|发文量|被引|投稿", q):
        # Force SQL-ish default rather than RAG when cues present but no match
        y0, y1 = _years(q, entities, default_n=20)
        if re.search(r"机构", q):
            return {
                "complexity": "simple",
                "suggested_source": "sql",
                "reason": "结构化线索：机构统计",
                "escalated": False,
                "query_plan": {
                    "task": "top_institutions",
                    "sources": ["sql"],
                    "sql_ops": ["top_institutions"],
                    "year_start": y0,
                    "year_end": y1,
                    "top_n": _top_n_from_question(q, 10),
                    "focus": "列出发文量靠前的机构。",
                },
            }
        if re.search(r"热点", q):
            return {
                "complexity": "simple",
                "suggested_source": "sql",
                "reason": "结构化线索：热点对比",
                "escalated": False,
                "query_plan": {
                    "task": "hotspot_compare",
                    "sources": ["sql"],
                    "sql_ops": ["hotspot_compare"],
                    "year_start": y0,
                    "year_end": y1,
                    "focus": "对比时间窗热词变化。",
                },
            }

    if re.search(r"识别|预测|演变|对比|并分析|同时", q):
        return {
            "complexity": "complex",
            "suggested_source": None,
            "reason": "分析类默认走复杂编排",
            "escalated": False,
        }
    return {
        "complexity": "simple",
        "suggested_source": "rag",
        "reason": "默认单源语义检索",
        "escalated": False,
        "query_plan": {
            "task": "generic",
            "sources": ["rag"],
            "rag_queries": [q],
            "focus": "基于检索结果回答。",
        },
    }


def router_node(state: JournalState) -> Dict[str, Any]:
    question = state.get("question") or ""
    entities = dict(state.get("entities") or {})
    route = classify_complexity(question, entities)
    plan = route.pop("query_plan", None) or state.get("query_plan") or {}
    if route.get("complexity") == "simple" and plan:
        # align years into entities
        if plan.get("year_start") is not None:
            entities["year_start"] = plan["year_start"]
        if plan.get("year_end") is not None:
            entities["year_end"] = plan["year_end"]
        if plan.get("keywords"):
            entities["keywords"] = plan["keywords"]
        if plan.get("author_name"):
            entities["author_name"] = plan["author_name"]
        if plan.get("author_name_b"):
            entities["author_name_b"] = plan["author_name_b"]

    sources = list(plan.get("sources") or [])
    if route.get("complexity") == "simple" and route.get("suggested_source"):
        # Keep multi-source simple plans (e.g. author_profile sql+kg)
        if len(sources) <= 1:
            sources = [route["suggested_source"]]
            plan["sources"] = sources

    reason = route.get("reason") or ""
    return {
        "route": route,
        "query_plan": plan,
        "entities": entities,
        "intents": sources or (["rag"] if route.get("complexity") == "simple" else []),
        "route_reason": f"路由:{route.get('complexity')}; {reason}",
        "stage": "routed",
        "goal": plan.get("focus") or question,
        "evidence_bundle": list(state.get("evidence_bundle") or []),
        "react_trace": list(state.get("react_trace") or []),
    }
