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
from app.config import bind_corpus


GENE_EDIT_KWS = ["基因编辑", "CRISPR", "基因组编辑", "基因敲除"]


def _normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure every downstream capability receives the same plan contract."""
    from app.agents.operation_contracts import normalize_query_plan

    return normalize_query_plan(plan)


def _years(question: str, entities: Dict[str, Any], default_n: Optional[int] = None):
    q = question or ""
    # Prefer shared extractor (digits + 近十年/近五年 等中文数字)
    y0, y1 = extract_year_window(question, default_last_n=None)
    if y0 is not None or y1 is not None:
        return y0, y1
    y0, y1 = entities.get("year_start"), entities.get("year_end")
    if y0 is None and y1 is None:
        y0, y1 = extract_year_window(question, default_last_n=default_n)
    return y0, y1


_CN_TOP_N = {
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十五": 15,
    "二十": 20,
}


def _top_n_from_question(question: str, default: int = 10) -> int:
    m = re.search(r"前\s*(\d{1,2})", question or "")
    if m:
        return max(1, min(int(m.group(1)), 50))
    m = re.search(r"前\s*(两|二|三|四|五|六|七|八|九|十|十五|二十)", question or "")
    if m and m.group(1) in _CN_TOP_N:
        return _CN_TOP_N[m.group(1)]
    return default


_TOPIC_STOP = {
    "发文",
    "发文量",
    "论文",
    "作者",
    "研究",
    "相关",
    "有哪些",
    "哪些",
    "前十",
    "近五",
    "近5",
}


def _is_clean_topic(s: str) -> bool:
    if not s or s in _TOPIC_STOP:
        return False
    if len(s) > 12 or " " in s:
        return False
    # Reject ranking / window leftovers from bad keyword extracts
    if re.search(
        r"发文|作者|前十|前\d|近\d|近五|近十|有哪些|排名|机构|趋势|热门",
        s,
    ):
        return False
    return True


def _topic_keyword(question: str, entities: Dict[str, Any]) -> Optional[str]:
    """Extract a short research topic for keyword-scoped author ranking."""
    q = question or ""
    for pat in (
        r"研究过\s*([\u4e00-\u9fffA-Za-z0-9]{1,8}?)(?=发文|论文|作者|的|相关|研究|前|有)",
        r"关于\s*([\u4e00-\u9fffA-Za-z0-9]{1,8}?)(?=的|发文|论文|作者|相关|研究)",
        r"(?:主题|专题|关键词|主题词)[为是「\"'：:\s]*([\u4e00-\u9fffA-Za-z0-9]{1,12})",
        r"[“\"‘']([^”\"’']{1,12})[”\"’']",
    ):
        m = re.search(pat, q)
        if m:
            s = m.group(1).strip()
            if _is_clean_topic(s):
                return s
    for k in entities.get("keywords") or []:
        s = str(k).strip().strip("“”\"'‘’")
        if _is_clean_topic(s):
            return s
    for term in (
        "水稻",
        "番茄",
        "镉",
        "玉米",
        "小麦",
        "大豆",
        "人工智能",
        "共同富裕",
        "数字经济",
        "基因编辑",
    ):
        if term in q:
            return term
    return None


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
    if state.get("journal_id"):
        bind_corpus(state.get("journal_id"))
    question = state.get("question") or ""
    locked = state.get("query_plan") or {}
    if locked.get("locked"):
        entities = dict(state.get("entities") or {})
        entities.update(
            {
                "year_start": locked.get("year_start"),
                "year_end": locked.get("year_end"),
                "keywords": list(locked.get("keywords") or []),
            }
        )
        return {"entities": entities, "stage": "extracted"}
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
    if re.search(r"合作.*(作者|机构)|作者和机构|最紧密|机构.{0,8}合作|合作.{0,8}机构", q) and not re.search(
        r"合作作者变化|合作者变化|研究轨迹|合作伙伴", q
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
    if re.search(r"(?:研究|期刊)?主题.*(?:发展|演变|变化|变迁)|(?:发展|演变|变化|变迁).*主题", q) and not (
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

    # Comprehensive overview (before submission_fit / trend)
    if re.search(
        r"综合分析|优势领域|发展历程.{0,20}核心作者|核心作者团队和未来",
        q,
    ):
        return (
            "sql",
            "单源：期刊发展概览",
            {
                "task": "journal_overview",
                "sources": ["sql"],
                "sql_ops": ["journal_overview"],
                "year_start": y0,
                "year_end": y1,
                "keywords": [],
                "focus": "概括发展历程、核心作者/机构与方向趋势。",
            },
        )

    # Hotspot / theme evolution → compare windows
    if re.search(
        r"热点演变|主题差异|(?:研究|期刊)?主题.*(?:差异|变化|演变|变迁)|前\d+年和后\d+年|"
        r"从[\u4e00-\u9fff]{2,8}到[\u4e00-\u9fff]{2,8}的变化|演变过程",
        q,
    ):
        return (
            "sql",
            "单源：研究热点/主题对比",
            {
                "task": "hotspot_compare",
                "sources": ["sql"],
                "sql_ops": ["hotspot_compare"],
                "year_start": None,
                "year_end": None,
                "keywords": [],
                "focus": "对比时间窗热门关键词变化。",
            },
        )

    # Emerging / future directions
    if re.search(r"新兴|未来可能|重点发展|重点关注", q) and not re.search(
        r"适合投|是否适合|投稿", q
    ):
        end = datetime.now().year
        return (
            "sql",
            "单源：新兴/重点研究方向",
            {
                "task": "top_directions_with_papers",
                "sources": ["sql"],
                "sql_ops": ["top_keywords", "papers_by_top_keywords"],
                "year_start": end - 4,
                "year_end": end,
                "top_n_directions": _top_n_from_question(q, 8),
                "keywords": [],
                "focus": "基于近区间热门关键词归纳新兴/重点方向及代表论文。",
            },
        )

    # Topic-scoped trend (水稻/AI…) — not whole-journal volume
    topic_kw = _topic_keyword(q, entities)
    if topic_kw and re.search(r"趋势|发展|变化|演变", q) and not re.search(
        r"每年发文|发文数量|发表论文数量|增长最快|合作|机构排名",
        q,
    ):
        return (
            "sql",
            f"单源：主题「{topic_kw}」演变",
            {
                "task": "topic_evolution",
                "sources": ["sql"],
                "sql_ops": ["topic_keyword_counts", "topic_yearly"],
                "keywords": [topic_kw],
                "year_start": y0,
                "year_end": y1,
                "focus": f"围绕「{topic_kw}」给出命中与逐年变化，勿用全刊趋势冒充。",
            },
        )

    # Multi-direction authors
    if re.search(r"同时覆盖|多个研究方向|跨方向|多方向", q):
        return (
            "sql",
            "单源：多方向作者（关键词样本）",
            {
                "task": "top_teams",
                "sources": ["sql"],
                "sql_ops": ["top_authors", "author_keywords_sample"],
                "year_start": y0,
                "year_end": y1,
                "focus": "列出高产作者及其关键词，识别跨方向作者。",
            },
        )

    # Journal main research directions → keyword inventory (NOT RAG editorial notices)
    if (
        re.search(
            r"(主要|核心)?研究方向(有哪些|是什么|分析|归纳|总结)|"
            r"(本刊|该刊|期刊).{0,8}(主要|核心)?研究方向|"
            r"研究方向.{0,6}(有哪些|是什么)",
            q,
        )
        and not re.search(
            r"研究方向为|投稿|演变|发展变化|三个研究方向|发表最多|近\s*\d+\s*年发表最多",
            q,
        )
    ):
        # Broad window: “主要方向” is a corpus-level inventory, not near-term RAG.
        y0b, y1b = _years(q, entities, default_n=20)
        top_n = _top_n_from_question(q, 8)
        return (
            "sql",
            "单源统计：期刊主要研究方向（热门关键词）",
            {
                "task": "top_directions_with_papers",
                "sources": ["sql"],
                "sql_ops": ["top_keywords", "papers_by_top_keywords"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": (
                    "基于关键词发文量归纳本刊主要研究方向，并为每个方向列出代表论文；"
                    "严禁把办刊通告、影响因子、获奖、在线优先出版等当作研究方向。"
                ),
                "year_start": y0b,
                "year_end": y1b,
                "top_n_directions": top_n,
                "keywords": [],
            },
        )

    # Institution authors + representative papers (NOT RAG papers *about* the org)
    if re.search(
        r"(相关)?作者.*(代表|成果|论文)|代表性成果|代表论文|"
        r"(大学|学院|研究院|研究所).{0,12}(作者|学者).{0,12}(成果|论文)",
        q,
    ) and re.search(
        r"大学|学院|研究院|研究所|科学院|机构|单位",
        q,
    ):
        m = re.search(
            r"([\u4e00-\u9fff]{2,20}(?:大学|学院|研究院|研究所|科学院))",
            q,
        )
        inst = m.group(1) if m else ""
        # Prefer full「浙江大学」over accidental shorter matches
        for cand in ("浙江大学", "杭州师范大学", "中国科学院"):
            if cand in q:
                inst = cand
                break
        if inst:
            top_n = _top_n_from_question(q, 8)
            # Only bind years when the question explicitly asks for a window
            y0i, y1i = _years(q, entities, default_n=None)
            return (
                "sql",
                f"单源统计：{inst} 作者及代表论文",
                {
                    "task": "institution_authors",
                    "sources": ["sql"],
                    "sql_ops": ["institution_authors"],
                    "kg_ops": [],
                    "rag_queries": [],
                    "institution": inst,
                    "top_n_authors": top_n,
                    "papers_per_author": 3,
                    "focus": (
                        f"列出署名单位含「{inst}」的高产作者及其在本刊的学科代表论文；"
                        "优先非校史/办学纪念类文献；"
                        "严禁把以该机构为研究对象的文章当作作者成果。"
                    ),
                    "year_start": y0i,
                    "year_end": y1i,
                    "keywords": [],
                },
            )

    # Topic-scoped top authors: 「近5年研究过水稻发文量前十的作者」
    topic_kw = _topic_keyword(q, entities)
    if (
        topic_kw
        and re.search(r"作者", q)
        and re.search(
            r"前\s*(\d{1,2}|十|五|三|八)|发文量.*前|高产|作者.*排名|"
            r"研究过|关于|主题|专题|关键词|主题词|包含|含有",
            q,
        )
        and not re.search(r"机构|单位|合作者|合著", q)
    ):
        top_n = _top_n_from_question(q, 10)
        y0k, y1k = _years(q, entities, default_n=None)
        return (
            "sql",
            f"单源统计：主题「{topic_kw}」高产作者",
            {
                "task": "keyword_authors",
                "sources": ["sql"],
                "sql_ops": ["authors_by_keyword"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": (
                    f"在关键词/主题含「{topic_kw}」的论文中，"
                    f"按发文量列出前 {top_n} 位作者；勿忽略时间窗与主题约束。"
                ),
                "year_start": y0k,
                "year_end": y1k,
                "top_n": top_n,
                "papers_per_author": 2,
                "keywords": [topic_kw],
            },
        )

    # Top authors by paper count (must beat soft「发文量→趋势」fallback)
    if (
        re.search(r"作者", q)
        and re.search(
            r"前\s*(\d{1,2}|十|五|三|八)|发文量.*前|高产作者|作者.*排名|发文最多的作者",
            q,
        )
        and not re.search(r"机构|单位|关键词|合作者|合著", q)
        and not topic_kw
    ):
        top_n = _top_n_from_question(q, 10)
        y0a, y1a = _years(q, entities, default_n=None)
        if y0a is None and y1a is None and re.search(r"近|过去|最近", q):
            y0a, y1a = _years(q, entities, default_n=5)
        return (
            "sql",
            "单源统计：高产作者排名",
            {
                "task": "top_authors",
                "sources": ["sql"],
                "sql_ops": ["top_authors"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": f"按发文量列出前 {top_n} 位作者。",
                "year_start": y0a,
                "year_end": y1a,
                "top_n": top_n,
                "keywords": [],
            },
        )

    # Q15: institution top-N / 发文最多的机构
    if re.search(r"机构|单位", q) and re.search(
        r"前\s*\d+|前\s*(十|五|三)|排名|最多|高产|发文(?:量|数量)?最多",
        q,
    ) and not re.search(
        r"合作|机构.*作者|作者.*机构|代表性成果|代表论文",
        q,
    ):
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

    # Q12: submission fit / gene-edit suitability — SQL coverage before RAG
    # Q12: submission fit / gene-edit suitability — SQL coverage before RAG
    if re.search(
        r"适合投稿|是否适合投|适合投|投稿前|适合发|是否适合.{0,24}投稿|"
        r"论文主题.{0,40}适合|我的研究方向.{0,20}(相关|适合|投稿)",
        q,
    ) or (
        re.search(r"基因编辑|CRISPR|基因组编辑", q, re.I)
        and re.search(r"投稿|相关论文|参考|研究方向为", q)
    ):
        from app.agents.intent_schema import (
            expand_submission_keywords,
            extract_quoted_topic_phrase,
        )

        kws = expand_submission_keywords(q, list(entities.get("keywords") or []))
        if not kws:
            kws = list(GENE_EDIT_KWS)
        return (
            "sql",
            "单源：投稿适配（专题覆盖）",
            {
                "task": "submission_fit",
                "sources": ["sql"],
                "sql_ops": ["submission_fit"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": (
                    "严格依据关键词命中评估适合度；禁止用证据外旧文论证适合投稿。"
                ),
                "year_start": y0,
                "year_end": y1,
                "keywords": kws[:8],
                "topic_phrase": extract_quoted_topic_phrase(q),
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

    # Demo / overview: 发文趋势 + 热门关键词 → SQL only (avoid RAG editorial notices)
    if re.search(
        r"(发文趋势|逐年发文|年度发文).{0,12}(热门)?(关键词|热词)|"
        r"(热门)?(关键词|热词).{0,12}(发文趋势|逐年发文|年度发文)|"
        r"近\s*\d+\s*年.*(发文趋势|热门关键词)|"
        r"(过去|最近)\s*\d+\s*年.*(发文趋势|热门关键词)",
        q,
    ) and not re.search(r"合作|机构|团队|发展历程|投稿|被引", q):
        return (
            "sql",
            "单源统计：发文趋势与热门关键词",
            {
                "task": "yearly_growth",
                "sources": ["sql"],
                "sql_ops": ["yearly_counts", "yoy_growth", "top_keywords"],
                "kg_ops": [],
                "rag_queries": [],
                "focus": (
                    "列出近区间逐年发文量与增速，并给出热门关键词（学科主题词）排名；"
                    "严禁用办刊通告、获奖、影响因子、引证报告等非研究文献作答。"
                ),
                "year_start": y0,
                "year_end": y1,
                "keywords": [],
            },
        )

    # Q1/Q2: yearly growth / growth-decline periods
    if re.search(
        r"(每年|逐年).*(发文|数量)|发文数量.*增长|增长最快|"
        r"增长期|下降期|同比|年度发文趋势|发文趋势",
        q,
    ) and not re.search(r"合作|机构|团队|方向.*成果|发展历程|热点|关键词|热词", q):
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
    if re.search(r"作者|哪些人|谁", q) and (
        re.search(r"关键词|主题词|包含|含有|研究过|关于", q) or _topic_keyword(q, entities)
    ):
        kw = _topic_keyword(q, entities)
        kws = [kw] if kw else list(entities.get("keywords") or [])
        top_n = _top_n_from_question(q, 30)
        return (
            "sql",
            "单源：关键词查作者",
            {
                "task": "keyword_authors",
                "sources": ["sql"],
                "sql_ops": ["authors_by_keyword"],
                "year_start": y0 if re.search(r"近|过去|最近|\d{4}", q) else None,
                "year_end": y1 if re.search(r"近|过去|最近|\d{4}", q) else None,
                "top_n": top_n if re.search(r"前\s*(\d|十|五|三)", q) else 30,
                "papers_per_author": 2 if re.search(r"前\s*(\d|十|五|三)", q) else 8,
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
        and re.search(
            r"发文|发表|论文|概况|情况|合作|机构|统计|基金|轨迹|主题|伙伴|团队|"
            r"有没有|是否有|有发文|在平台",
            q,
        )
        and not re.search(r"发文(?:量|数量)?最多的机构|机构.*最多|最多的机构", q)
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
            "top_authors",
            "institution_authors",
            "topic_coverage",
            "submission_fit",
            "yearly_growth",
            "topic_evolution",
            "author_profile",
            "coauthored_papers",
            "keyword_authors",
            "journal_overview",
            "top_directions_with_papers",
            "top_teams",
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
    if re.search(r"统计|排名|机构|热点|发文量|发文趋势|热门关键词|热词|被引|投稿", q):
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
        if re.search(r"作者", q) and re.search(r"前|排名|高产|发文量", q):
            return {
                "complexity": "simple",
                "suggested_source": "sql",
                "reason": "结构化线索：作者发文排名",
                "escalated": False,
                "query_plan": {
                    "task": "top_authors",
                    "sources": ["sql"],
                    "sql_ops": ["top_authors"],
                    "year_start": y0,
                    "year_end": y1,
                    "top_n": _top_n_from_question(q, 10),
                    "focus": "按发文量列出高产作者。",
                    "keywords": [],
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
        # 「发文量」 alone used to wrongly become yearly trend — require trend/keyword cues
        if re.search(r"发文趋势|热门关键词|热词|逐年发文|年度发文", q):
            return {
                "complexity": "simple",
                "suggested_source": "sql",
                "reason": "结构化线索：发文趋势/热词",
                "escalated": False,
                "query_plan": {
                    "task": "yearly_growth",
                    "sources": ["sql"],
                    "sql_ops": ["yearly_counts", "yoy_growth", "top_keywords"],
                    "year_start": y0,
                    "year_end": y1,
                    "focus": (
                        "用逐年发文与热门关键词作答；"
                        "勿引用办刊通告/获奖/影响因子类文献。"
                    ),
                    "keywords": [],
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


def _route_from_schema(
    question: str, intent: Dict[str, Any], entities: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """High-confidence Intent Schema → query_plan; None means fall back to regex."""
    from app.agents.capability_planner import plan_from_intent

    if not intent:
        return None
    plan = plan_from_intent(intent, question)
    if not plan:
        return None
    complexity = plan.get("complexity") or "simple"
    sources = list(plan.get("sources") or ["sql"])
    suggested = sources[0] if complexity == "simple" and sources else None
    return {
        "complexity": complexity,
        "suggested_source": suggested,
        "reason": (
            f"schema:{intent.get('entity')}/{intent.get('operation')}"
            f"/goal={intent.get('goal')} conf={intent.get('confidence')}"
        ),
        "escalated": False,
        "query_plan": plan,
        "plan_source": "schema",
    }


def router_node(state: JournalState) -> Dict[str, Any]:
    question = state.get("question") or ""
    entities = dict(state.get("entities") or {})
    intent = dict(state.get("intent") or {})

    locked_plan = dict(state.get("query_plan") or {})
    if locked_plan.get("locked"):
        from app.agents.operation_contracts import normalize_query_plan
        locked_plan = normalize_query_plan(locked_plan, question, intent)
        sources = list(locked_plan.get("sources") or ["sql"])
        plan_source = locked_plan.get("plan_source") or "conversation"
        reason = f"{plan_source}: locked query plan"
        route = {
            "complexity": locked_plan.get("complexity") or "simple",
            "suggested_source": sources[0] if sources else "sql",
            "reason": reason,
            "escalated": False,
            "plan_source": plan_source,
        }
        return {
            "route": route,
            "query_plan": locked_plan,
            "entities": entities,
            "intents": sources,
            "route_reason": reason,
            "stage": "routed",
            "goal": locked_plan.get("focus") or question,
            "evidence_bundle": list(state.get("evidence_bundle") or []),
            "react_trace": list(state.get("react_trace") or []),
        }

    schema_route = _route_from_schema(question, intent, entities)
    if schema_route:
        route = schema_route
        plan_tag = "schema"
    else:
        route = classify_complexity(question, entities)
        route["plan_source"] = "regex"
        plan_tag = "regex"

    plan = route.pop("query_plan", None) or state.get("query_plan") or {}
    from app.agents.operation_contracts import normalize_query_plan
    plan = normalize_query_plan(plan, question, intent)
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
        if plan.get("institution"):
            entities["institution"] = plan["institution"]
        # Drop polluted author names when doing topic/author rankings
        if plan.get("task") in {
            "top_authors",
            "keyword_authors",
            "institution_authors",
            "submission_fit",
            "topic_coverage",
        }:
            entities.pop("author_name", None)

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
        "route_reason": f"{plan_tag}: 路由:{route.get('complexity')}; {reason}",
        "stage": "routed",
        "goal": plan.get("focus") or question,
        "evidence_bundle": list(state.get("evidence_bundle") or []),
        "react_trace": list(state.get("react_trace") or []),
    }
