from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

"""Query Planner Agent: intent recognition, entity extraction, query plan."""

from app.agents.state import JournalState
from app.agents.understand import (
    extract_year_window,
    llm_confirm_entities,
    regex_extract,
)
from app.config import get_settings
from app.services.minimax_chat import MiniMaxChat

AI_TOPIC_KWS = [
    "人工智能",
    "机器学习",
    "深度学习",
    "神经网络",
    "智能算法",
    "计算机视觉",
    "图像识别",
    "遥感",
    "光谱",
    "近红外",
    "高光谱",
]


def _years(entities: Dict[str, Any], question: str, default_n: Optional[int] = None):
    # Prefer explicit「过去/近 N 年 / N年来」window over LLM entity drift.
    from datetime import datetime

    q = question or ""
    m_n = re.search(r"(?:过去|近|最近)\s*(\d{1,2})\s*年|(\d{1,2})\s*年来", q)
    if m_n:
        n = int(m_n.group(1) or m_n.group(2))
        end = datetime.now().year
        return end - n + 1, end
    y0 = entities.get("year_start")
    y1 = entities.get("year_end")
    if y0 is None and y1 is None:
        y0, y1 = extract_year_window(question, default_last_n=default_n)
    return y0, y1


def _pick_topic_kw(question: str, entities: Dict[str, Any]) -> List[str]:
    kws = [str(k).strip() for k in (entities.get("keywords") or []) if str(k).strip()]
    # Prefer domain terms over sentence fragments
    short = [k for k in kws if 1 < len(k) <= 12 and " " not in k]
    if short:
        return short[:5]
    for term in ("水稻", "番茄", "人工智能", "机器学习", "深度学习"):
        if term in (question or ""):
            return [term]
    return short[:5]


def rule_plan(question: str, entities: Dict[str, Any], intents: List[str]) -> Optional[Dict[str, Any]]:
    q = question or ""

    # Q1: yearly counts + fastest growth
    if re.search(r"(每年|逐年).*(发文|数量)|发文数量.*增长|增长最快", q) and re.search(
        r"发文|数量|增长", q
    ):
        y0, y1 = _years(entities, q, default_n=20)
        return {
            "task": "yearly_growth",
            "sources": ["sql"],
            "sql_ops": ["yearly_counts", "yoy_growth"],
            "kg_ops": [],
            "rag_queries": [],
            "focus": "列出每年发文量，计算同比增速，指出增长最快的年份并简要说明。",
            "year_start": y0,
            "year_end": y1,
            "keywords": [],
        }

    # Q2: AI / named topic evolution
    if re.search(r"人工智能|机器学习|深度学习|智能(算法|识别)?", q) and re.search(
        r"主题|发展|演变|变化|趋势", q
    ):
        y0, y1 = _years(entities, q, default_n=20)
        return {
            "task": "topic_evolution",
            "sources": ["sql", "rag"],
            "sql_ops": ["topic_keyword_counts", "topic_yearly"],
            "kg_ops": [],
            "rag_queries": AI_TOPIC_KWS[:6],
            "focus": "围绕人工智能及相关技术主题总结发展变化；若文献极少须明确说明，勿用全刊热词冒充。",
            "year_start": y0,
            "year_end": y1,
            "keywords": list(AI_TOPIC_KWS),
        }

    # Q3: keyword collaboration (authors + institutions)
    if re.search(r"合作.*(作者|机构)|作者和机构|最紧密", q) and (
        re.search(r"水稻|番茄|关键词", q) or _pick_topic_kw(q, entities)
    ):
        y0, y1 = _years(entities, q, default_n=20)
        kws = _pick_topic_kw(q, entities) or ["水稻"]
        if "水稻" in q and "水稻" not in kws:
            kws = ["水稻"] + [k for k in kws if k != "水稻"]
        return {
            "task": "keyword_collab",
            "sources": ["sql", "kg"],
            "sql_ops": ["authors_by_keyword", "institutions_by_keyword"],
            "kg_ops": ["keyword_ego"],
            "rag_queries": [],
            "focus": f"基于关键词「{kws[0]}」找出合作最紧密的作者与机构，用数据支撑。",
            "year_start": y0,
            "year_end": y1,
            "keywords": kws[:3],
        }

    # Q6 before Q4/Q5: full journal academic development overview
    if re.search(
        r"(学术发展历程|发展历程|演变趋势|研究方向演变|核心作者团队|代表性机构|"
        r"主要研究方向演变|预测未来.*研究方向|值得关注的研究方向)",
        q,
    ) or (
        re.search(r"学术发展|发展历程", q)
        and re.search(r"作者|机构|方向|演变", q)
    ):
        y0, y1 = _years(entities, q, default_n=20)
        return {
            "task": "journal_overview",
            "sources": ["sql"],
            "sql_ops": ["journal_overview"],
            "kg_ops": [],
            "rag_queries": [],
            "focus": "概括期刊学术发展历程：阶段演变、核心作者、代表性机构与未来方向。",
            "year_start": y0,
            "year_end": y1,
            "keywords": [],
        }

    # Q4: top directions + important papers (specific phrasing only)
    if re.search(
        r"发表最多的.*(方向|主题)|最多的三个研究方向|三个研究方向|"
        r"三大方向|Top\s*3.*方向|(研究方向|热门方向).{0,12}(重要)?成果",
        q,
        re.I,
    ):
        y0, y1 = _years(entities, q, default_n=5)
        return {
            "task": "top_directions_with_papers",
            "sources": ["sql", "rag"],
            "sql_ops": ["top_keywords", "papers_by_top_keywords"],
            "kg_ops": [],
            "rag_queries": [q],
            "focus": "给出近区间发文最多的三个研究方向，并为每个方向总结重要研究成果（须引用证据中的论文）。",
            "year_start": y0,
            "year_end": y1,
            "keywords": [],
            "top_n_directions": 3,
        }

    # Q5: top teams + direction change
    if re.search(r"研究团队|核心团队|影响力最高|高产作者", q) or (
        re.search(r"团队", q) and re.search(r"方向|影响|变化", q)
    ):
        y0, y1 = _years(entities, q, default_n=10)
        return {
            "task": "top_teams",
            "sources": ["sql"],
            "sql_ops": [
                "top_authors",
                "top_institutions",
                "keywords_by_periods",
                "author_keywords_sample",
            ],
            "kg_ops": [],
            "rag_queries": [],
            "focus": "识别近区间发文领先的研究团队（高产作者/机构），并分析其研究方向随阶段的变化。",
            "year_start": y0,
            "year_end": y1,
            "keywords": [],
        }

    # Keyword → authors
    if re.search(r"作者|哪些人|谁", q) and re.search(r"关键词|主题词|包含|含有", q):
        kws = _pick_topic_kw(q, entities)
        return {
            "task": "keyword_authors",
            "sources": ["sql"],
            "sql_ops": ["authors_by_keyword"],
            "kg_ops": [],
            "rag_queries": [],
            "focus": "列出关键词相关作者及论文。",
            "year_start": entities.get("year_start"),
            "year_end": entities.get("year_end"),
            "keywords": kws,
        }

    # Two authors' coauthored papers: 徐建明和施加春合作的发文
    name_b = entities.get("author_name_b")
    names = entities.get("author_names") or []
    if (
        (name_b or len(names) >= 2)
        and entities.get("author_name")
        and re.search(r"合作|合著|共著", q)
        and re.search(r"发文|论文|著作|文章|文献|哪些", q)
    ):
        a = entities.get("author_name")
        b = name_b or (names[1] if len(names) > 1 else None)
        return {
            "task": "coauthored_papers",
            "sources": ["sql"],
            "sql_ops": ["coauthored_papers"],
            "kg_ops": [],
            "rag_queries": [],
            "focus": f"只列出 {a} 与 {b} 的合著论文，不要给出各自全部发文或全部合作者。",
            "year_start": entities.get("year_start"),
            "year_end": entities.get("year_end"),
            "keywords": [],
            "author_name": a,
            "author_name_b": b,
        }

    # Author profile (single author only)
    if (
        entities.get("author_name")
        and not entities.get("author_name_b")
        and re.search(r"发文|概况|情况|合作|机构|统计|基金", q)
    ):
        sources = ["sql"]
        kg_ops: List[str] = []
        if re.search(r"合作|机构|合著", q):
            sources.append("kg")
            kg_ops.append("author_ego")
        return {
            "task": "author_profile",
            "sources": sources,
            "sql_ops": ["author_profile"],
            "kg_ops": kg_ops,
            "rag_queries": [],
            "focus": f"回答作者 {entities.get('author_name')} 的相关统计/合作问题。",
            "year_start": entities.get("year_start"),
            "year_end": entities.get("year_end"),
            "keywords": [],
            "author_name": entities.get("author_name"),
        }

    # Generic topic evolution (named keyword + 演变/变化)
    if re.search(r"(主题|研究).*(发展|演变|变化)|发展变化", q) and _pick_topic_kw(q, entities):
        y0, y1 = _years(entities, q, default_n=20)
        kws = _pick_topic_kw(q, entities)
        return {
            "task": "topic_evolution",
            "sources": ["sql", "rag"],
            "sql_ops": ["topic_keyword_counts", "topic_yearly"],
            "kg_ops": [],
            "rag_queries": kws[:4],
            "focus": f"总结主题「{'、'.join(kws)}」在本刊的发展变化。",
            "year_start": y0,
            "year_end": y1,
            "keywords": kws,
        }

    return None


def _generic_plan(
    question: str, entities: Dict[str, Any], intents: List[str]
) -> Dict[str, Any]:
    y0, y1 = _years(entities, question, default_n=10 if "sql" in intents else None)
    sources = list(intents) or ["rag"]
    sql_ops: List[str] = []
    kg_ops: List[str] = []
    rag_queries: List[str] = []
    if "sql" in sources:
        sql_ops = ["yearly_counts", "top_keywords"]
        if entities.get("author_name"):
            sql_ops = ["author_profile"]
    if "kg" in sources:
        kg_ops = ["author_ego"] if entities.get("author_name") else ["keyword_ego"]
    if "rag" in sources:
        rag_queries = [question]
    return {
        "task": "generic",
        "sources": sources,
        "sql_ops": sql_ops,
        "kg_ops": kg_ops,
        "rag_queries": rag_queries,
        "focus": "严格基于多路证据回答用户问题，勿答非所问。",
        "year_start": y0,
        "year_end": y1,
        "keywords": list(entities.get("keywords") or [])[:5],
        "author_name": entities.get("author_name"),
    }


def llm_plan(
    question: str,
    entities: Dict[str, Any],
    intents: List[str],
    chat: Optional[MiniMaxChat] = None,
) -> Dict[str, Any]:
    chat = chat or MiniMaxChat(get_settings())
    prompt = f"""为期刊问答系统设计查询计划，只输出 JSON：
{{
  "task": "yearly_growth|topic_evolution|keyword_collab|top_directions_with_papers|top_teams|journal_overview|author_profile|keyword_authors|generic",
  "sources": ["sql","kg","rag"],
  "sql_ops": ["yearly_counts","top_keywords","top_authors","top_institutions","journal_overview","authors_by_keyword","institutions_by_keyword","papers_by_top_keywords","topic_keyword_counts","topic_yearly","yoy_growth","keywords_by_periods","author_profile","author_keywords_sample"],
  "kg_ops": ["keyword_ego","author_ego"],
  "rag_queries": ["检索词"],
  "focus": "一句话任务",
  "keywords": ["主题词"]
}}
已有意图: {intents}
实体: {json.dumps({k: entities.get(k) for k in ('author_name','year_start','year_end','keywords')}, ensure_ascii=False)}
问题: {question}
"""
    raw = chat.chat(
        [
            {"role": "system", "content": "你只输出合法 JSON 查询计划。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=500,
    )
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    base = _generic_plan(question, entities, intents)
    for key in (
        "task",
        "sources",
        "sql_ops",
        "kg_ops",
        "rag_queries",
        "focus",
        "keywords",
    ):
        if data.get(key) is not None:
            base[key] = data[key]
    # sanitize sources
    base["sources"] = [s for s in (base.get("sources") or []) if s in {"sql", "kg", "rag"}]
    if not base["sources"]:
        base["sources"] = intents or ["rag"]
    return base


def _extract_entities(question: str, prior_dois: Optional[List[str]] = None) -> Dict[str, Any]:
    from app.agents.understand import _restore_author_pair

    draft = regex_extract(question, prior_dois)
    try:
        entities = llm_confirm_entities(question, draft)
    except Exception as e:
        entities = dict(draft)
        entities["confirmed_intents"] = []
        entities["extract_meta"] = {
            "regex_draft": draft,
            "llm_confirmed": False,
            "fixes": f"llm确认失败，沿用正则: {e}",
        }
    return _restore_author_pair(question, entities, draft)


def _seed_intents(question: str, entities: Dict[str, Any]) -> List[str]:
    """Lightweight source hints (replaces standalone router). Plan rules override."""
    q = question or ""
    confirmed = [i for i in (entities.get("confirmed_intents") or []) if i in {"sql", "kg", "rag"}]
    if re.search(r"合作.*(作者|机构)|作者和机构|最紧密", q):
        return ["sql", "kg"]
    if re.search(r"发表最多的三个|三个研究方向", q):
        return ["sql", "rag"]
    if re.search(r"人工智能|机器学习|深度学习", q) and re.search(
        r"主题|演变|发展|变化", q
    ):
        return ["sql", "rag"]
    if re.search(
        r"(学术发展历程|发展历程|研究方向演变|核心作者团队|代表性研究机构)",
        q,
    ):
        return ["sql"]
    if confirmed:
        return confirmed
    if re.search(r"统计|发文|趋势|热门|数量|排名", q):
        return ["sql"]
    if re.search(r"合作|合著|知识图谱|关系", q):
        return ["kg", "sql"] if entities.get("author_name") else ["kg"]
    return ["rag"]


def planner_node(state: JournalState) -> Dict[str, Any]:
    """Query Planner Agent node: entities + intents + QueryPlan in one step."""
    question = state.get("question") or ""
    prior_dois = []
    if state.get("entities"):
        prior_dois = state["entities"].get("dois") or []

    entities = _extract_entities(question, prior_dois)
    seed = _seed_intents(question, entities)

    plan = rule_plan(question, entities, seed)
    used = "rule"
    if plan is None:
        try:
            plan = llm_plan(question, entities, seed)
            used = "llm"
        except Exception:
            plan = _generic_plan(question, entities, seed)
            used = "generic_fallback"

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
        entities["author_names"] = [
            plan.get("author_name"),
            plan.get("author_name_b"),
        ]

    sources = [s for s in (plan.get("sources") or []) if s in {"sql", "kg", "rag"}]
    if not sources:
        sources = seed or ["rag"]
    plan["sources"] = sources
    plan["planner"] = used

    meta = entities.get("extract_meta") or {}
    reason = f"规划:{plan.get('task')}({used})"
    if meta.get("fixes"):
        reason += f"；实体修正: {meta.get('fixes')}"

    return {
        "query_plan": plan,
        "intents": sources,
        "entities": entities,
        "route_reason": reason,
        "stage": "planned",
    }


# Backward-compatible alias
plan_node = planner_node
