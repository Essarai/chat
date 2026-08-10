"""Closed Intent Schema for query understanding + capability planning."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.agents.understand import (
    _looks_like_person_name,
    extract_year_window,
)

ENTITIES: Set[str] = {"paper", "author", "institution", "topic", "journal"}
OPERATIONS: Set[str] = {
    "search",
    "rank",
    "trend",
    "compare",
    "summarize",
    "recommend",
    "profile",
    "coverage",
}
GOALS: Set[str] = {
    "research_analysis",
    "submission_fit",
    "inventory",
    "refuse",
}
SOURCES: Set[str] = {"sql", "kg", "rag"}
METRICS: Set[str] = {
    "publication_count",
    "keyword_freq",
    "collaboration",
    "coverage",
    "",
}

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
    "期刊",
    "本刊",
    "适合",
    "投稿",
}

# Meta phrases that look like topics but are question scaffolding
_META_TOPIC = {
    "热点",
    "主题",
    "领域",
    "研究领域",
    "接受文章",
    "收录领域",
    "投稿领域",
    "研究方向",
    "数量变化",
    "研究热点",
    "热门关键词",
    "热门词",
    "热词",
    "关键词",
    "热点演变",
    "研究热点演变",
    "新兴研究方向",
    "研究主题",
    "研究主题差异",
    "主题差异",
    "合作关系",
    "合作伙伴",
    "高产",
    "应用研究",
    "发展趋势",
    "发展变化",
    "发展历程",
    "核心团队",
    "研究团队",
    "代表性论文",
    "浙江大学",  # institution, not topic
}

_TOPIC_ALIAS = {
    "AI": "人工智能",
    "AI技术": "人工智能",
    "人工智能技术": "人工智能",
    "机器学习": "人工智能",
    "深度学习": "人工智能",
    "基因组编辑": "基因编辑",
}

_CN_N = {
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

CONFIDENCE_THRESHOLD = 0.7


def empty_intent() -> Dict[str, Any]:
    return {
        "entity": "journal",
        "operation": "search",
        "goal": "research_analysis",
        "sources": ["sql"],
        "topic": [],
        "author_name": None,
        "author_name_b": None,
        "institution": None,
        "time_range": {"start": None, "end": None, "last_n": None},
        "metric": "publication_count",
        "top_n": None,
        "confidence": 0.0,
        "legacy_task": None,
        "notes": "",
        "requested_operations": [],
    }


def _clean_topic(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().strip("“”\"'‘’？?。！!")
    if not s or s in _TOPIC_STOP or s in _META_TOPIC:
        return None
    if len(s) < 2 or len(s) > 16 or " " in s or "包含" in s:
        return None
    # single function words / copulas
    if s in {"是", "的", "在", "与", "和", "及", "等", "中", "为"}:
        return None
    if any(
        tok in s
        for tok in (
            "适合",
            "投稿",
            "发文",
            "作者",
            "有哪些",
            "吗",
            "演变",
            "差异",
            "趋势",
            "合作",
            "高产",
            "历程",
            "团队",
            "近年",
            "该刊",
            "期刊",
            "变化",
        )
    ):
        return None
    # reject meta compounds like「应用研究」but keep「基因编辑应用」via length/domain terms
    if s in {"应用", "应用研究", "相关研究"}:
        return None
    s = _TOPIC_ALIAS.get(s, s)
    return s


_SUBMISSION_DOMAIN_TERMS = (
    "基因编辑",
    "CRISPR",
    "农业机器人",
    "智能农业",
    "精准农业",
    "精确农业",
    "病虫害",
    "害虫",
    "病害",
    "植保",
    "遥感",
    "机器学习",
    "深度学习",
    "神经网络",
    "人工智能",
    "数字经济",
    "共同富裕",
    "水稻",
    "番茄",
)


def extract_quoted_topic_phrase(question: str) -> Optional[str]:
    """Pull the full phrase after 主题是/研究方向是, preferably quoted."""
    q = question or ""
    for pat in (
        r"(?:论文)?主题[是为：:\s]*[“\"「]([^”\"」]{2,40})[”\"」]",
        r"研究方向[是为：:\s]*[“\"「]([^”\"」]{2,40})[”\"」]",
        r"(?:论文)?主题[是为：:\s]+([^\s，。；？?]{2,40})",
        r"研究方向[是为：:\s]+([^\s，。；？?]{2,40})",
    ):
        m = re.search(pat, q)
        if m:
            phrase = m.group(1).strip().strip("“”\"'「」")
            phrase = re.sub(r"(，适合投.*|适合投.*)$", "", phrase).strip()
            if phrase and phrase not in {"是", "的"}:
                return phrase
    return None


def expand_submission_keywords(question: str, topics: Optional[List[str]] = None) -> List[str]:
    """Decompose a submission topic phrase into searchable keyword slots."""
    q = question or ""
    out: List[str] = []

    def _add(term: str) -> None:
        t = _TOPIC_ALIAS.get(term, term)
        t = str(t).strip()
        if not t or t in out or t in _META_TOPIC:
            return
        # SQL keywords should be short topical tokens, not whole sentences
        if len(t) < 2 or len(t) > 8:
            return
        if re.search(r"的应用|适合|投稿|主题", t):
            return
        out.append(t)

    phrase = extract_quoted_topic_phrase(q)
    bag = " ".join([phrase or "", " ".join(topics or []), q])

    # Domain lexicon hits inside phrase/question
    for term in _SUBMISSION_DOMAIN_TERMS:
        if term.lower() in bag.lower() or term in bag:
            _add(term)
    if re.search(r"AI|人工智能", bag, re.I):
        _add("人工智能")
    if "病虫害" in bag:
        _add("病虫害")
        _add("害虫")
        _add("病害")
    if "智能" in bag and "农业" in bag:
        _add("智能农业")

    # Keep short cleaned topics from schema
    for t in topics or []:
        ct = _clean_topic(t)
        if ct:
            _add(ct)

    # If phrase exists but still empty, take 2-6 char CJK chunks that look topical
    if phrase and not out:
        for m in re.finditer(r"[\u4e00-\u9fff]{2,6}", phrase):
            ct = _clean_topic(m.group(0))
            if ct:
                _add(ct)

    return out[:8]


# Broad method/tech tokens: alone they must not inflate submission_fit when the
# topic also has a domain facet (e.g. AI治理 vs AI+农业病虫害).
_GENERIC_TECH_KEYWORDS = {
    "人工智能",
    "机器学习",
    "深度学习",
    "神经网络",
    "大数据",
    "信息化",
    "智能化",
    "算法",
    "计算机",
    "信息技术",
    "数字技术",
    "智能",
}


def score_submission_fit_from_keyword_rows(
    topic_keywords: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Facet-aware fit: domain keywords gate the label; generic-only → weak."""
    rows = topic_keywords or []
    generic_rows: List[Dict[str, Any]] = []
    specific_rows: List[Dict[str, Any]] = []
    for r in rows:
        kw = str(r.get("keyword") or "").strip()
        if not kw:
            continue
        if kw in _GENERIC_TECH_KEYWORDS:
            generic_rows.append(r)
        else:
            specific_rows.append(r)

    def _hits(rs: List[Dict[str, Any]]) -> int:
        return sum(int(x.get("paper_count") or 0) for x in rs)

    def _nonzero(rs: List[Dict[str, Any]]) -> int:
        return sum(1 for x in rs if int(x.get("paper_count") or 0) > 0)

    generic_hits = _hits(generic_rows)
    specific_hits = _hits(specific_rows)
    total = generic_hits + specific_hits
    has_specific_slot = bool(specific_rows)

    if has_specific_slot and specific_hits == 0:
        # e.g. humanities journal: 人工智能=24, 病虫害/害虫/病害=0
        fit_label = "weak"
        score_basis = "generic_only"
        effective_hits = 0
    elif has_specific_slot:
        # Score by domain facet; generic tech is supporting evidence only
        effective_hits = specific_hits
        if specific_hits >= 15:
            fit_label = "strong"
        elif specific_hits >= 5:
            fit_label = "moderate"
        else:
            fit_label = "weak"
        score_basis = "domain_facet"
    else:
        effective_hits = total
        if total >= 15:
            fit_label = "strong"
        elif total >= 5:
            fit_label = "moderate"
        else:
            fit_label = "weak"
        score_basis = "all_keywords"

    return {
        "fit_label": fit_label,
        "score_basis": score_basis,
        "total_hits": total,
        "effective_hits": effective_hits,
        "generic_hits": generic_hits,
        "specific_hits": specific_hits,
        "generic_terms_hit": _nonzero(generic_rows),
        "specific_terms_hit": _nonzero(specific_rows),
        "has_specific_slot": has_specific_slot,
    }


def _seed_topics_from_question(question: str) -> List[str]:
    q = question or ""
    out: List[str] = []
    # Prefer quoted submission / research topic phrases (decomposed)
    if re.search(r"适合投|是否适合|投稿|论文主题|研究方向是", q):
        for t in expand_submission_keywords(q):
            if t not in out:
                out.append(t)
        if out:
            return out[:5]
    for pat in (
        r"研究过\s*([\u4e00-\u9fffA-Za-z0-9]{1,8}?)(?=发文|论文|作者|的|相关|研究|前|有)",
        r"关于\s*([\u4e00-\u9fffA-Za-z0-9]{1,8}?)(?=的|发文|论文|作者|相关|研究|投稿)",
        r"(?:论文)?主题[是为：:\s]*[“\"「]([^”\"」]{2,40})[”\"」]",
        r"(?:主题|专题|关键词|主题词)[为是「\"'：:\s]*([\u4e00-\u9fffA-Za-z0-9]{2,12})",
        r"从([\u4e00-\u9fff]{2,8})到([\u4e00-\u9fff]{2,8})(?:的)?(?:变化|演变)?",
    ):
        m = re.search(pat, q)
        if not m:
            continue
        if m.lastindex and m.lastindex >= 2:
            for g in m.groups():
                ct = _clean_topic(re.sub(r"(的)?(变化|演变)$", "", g or ""))
                if ct and ct not in out:
                    out.append(ct)
        else:
            raw = m.group(1)
            # Long quoted phrase → expand rather than clean as one token
            if len(raw) > 8 and re.search(r"[“\"「]|主题", q):
                for t in expand_submission_keywords(q, [raw]):
                    if t not in out:
                        out.append(t)
            else:
                ct = _clean_topic(raw)
                if ct and ct not in out:
                    out.append(ct)
    for term in _SUBMISSION_DOMAIN_TERMS:
        if term.lower() in q.lower() or term in q:
            ct = _clean_topic(term) or _TOPIC_ALIAS.get(term, term)
            if ct and ct not in out and ct not in _META_TOPIC:
                out.append(ct)
    if re.search(r"\bAI\b|AI技术", q, re.I):
        if "人工智能" not in out:
            out.append("人工智能")
    return out[:5]


def _norm_author(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    s = re.sub(r"^(和|与|跟)", "", s)
    s = re.sub(r"(老师|教授|研究员|的|有)$", "", s).strip()
    if not s or s.lower() in {"null", "none", "无"}:
        return None
    if not _looks_like_person_name(s):
        return None
    return s


def _norm_top_n(raw: Any, question: str = "") -> Optional[int]:
    if raw is not None and raw != "":
        try:
            n = int(raw)
            return max(1, min(n, 50))
        except (TypeError, ValueError):
            pass
    m = re.search(r"前\s*(\d{1,2})", question or "")
    if m:
        return max(1, min(int(m.group(1)), 50))
    m = re.search(r"前\s*(两|二|三|四|五|六|七|八|九|十|十五|二十)", question or "")
    if m and m.group(1) in _CN_N:
        return _CN_N[m.group(1)]
    return None


def _resolve_time_range(
    tr: Any, question: str, draft: Optional[Dict[str, Any]] = None
) -> Dict[str, Optional[int]]:
    draft = draft or {}
    out: Dict[str, Optional[int]] = {"start": None, "end": None, "last_n": None}
    y0 = draft.get("year_start")
    y1 = draft.get("year_end")
    ry0, ry1 = extract_year_window(question)
    if ry0 is not None or ry1 is not None:
        out["start"], out["end"] = ry0, ry1
        m = re.search(r"(?:近|最近|过去|前)\s*(\d{1,2})\s*年", question or "")
        if m:
            out["last_n"] = int(m.group(1))
        m2 = re.search(
            r"(?:近|最近|过去|前)\s*(两|二|三|四|五|六|七|八|九|十|十五|二十)\s*年",
            question or "",
        )
        if m2 and m2.group(1) in _CN_N:
            out["last_n"] = _CN_N[m2.group(1)]
        return out

    if isinstance(tr, dict):
        last_n = tr.get("last_n")
        try:
            if last_n is not None and str(last_n).strip() != "":
                n = int(last_n)
                if 1 <= n <= 50:
                    end = datetime.now().year
                    out["last_n"] = n
                    out["start"] = end - n + 1
                    out["end"] = end
                    return out
        except (TypeError, ValueError):
            pass
        try:
            if tr.get("start") is not None:
                out["start"] = int(tr["start"])
            if tr.get("end") is not None:
                out["end"] = int(tr["end"])
        except (TypeError, ValueError):
            pass

    if out["start"] is None and y0 is not None:
        try:
            out["start"] = int(y0)
        except (TypeError, ValueError):
            pass
    if out["end"] is None and y1 is not None:
        try:
            out["end"] = int(y1)
        except (TypeError, ValueError):
            pass
    if out["start"] and out["end"] and out["start"] > out["end"]:
        out["start"], out["end"] = out["end"], out["start"]
    return out


def normalize_intent(
    raw: Dict[str, Any] | None,
    question: str = "",
    draft: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate and normalize an Intent Schema dict."""
    draft = draft or {}
    base = empty_intent()
    raw = raw or {}

    entity = str(raw.get("entity") or "").strip().lower()
    base["entity"] = entity if entity in ENTITIES else "journal"

    operation = str(raw.get("operation") or "").strip().lower()
    base["operation"] = operation if operation in OPERATIONS else "search"

    requested = raw.get("requested_operations") or []
    if not isinstance(requested, list):
        requested = []
    base["requested_operations"] = [
        (dict(item) if isinstance(item, dict) else str(item).strip())
        for item in requested[:12]
        if (isinstance(item, dict) and item.get("type"))
        or (not isinstance(item, dict) and str(item).strip())
    ]

    goal = str(raw.get("goal") or "").strip().lower()
    base["goal"] = goal if goal in GOALS else "research_analysis"

    sources = raw.get("sources") or ["sql"]
    if not isinstance(sources, list):
        sources = ["sql"]
    base["sources"] = [s for s in sources if s in SOURCES] or ["sql"]

    metric = str(raw.get("metric") or "publication_count").strip().lower()
    base["metric"] = metric if metric in METRICS else "publication_count"

    topics: List[str] = []
    for t in raw.get("topic") or raw.get("topics") or draft.get("keywords") or []:
        ct = _clean_topic(t)
        if ct and ct not in topics:
            topics.append(ct)
    seeded = _seed_topics_from_question(question)
    if not topics:
        topics = seeded
    else:
        for t in seeded:
            if t not in topics:
                topics.append(t)
    # Submission questions: always expand compound phrases (AI / 病虫害 …)
    q_probe = question or ""
    if re.search(r"适合投|是否适合|投稿|论文主题|我的研究方向", q_probe):
        topics = expand_submission_keywords(q_probe, topics)
    base["topic"] = topics[:8]

    author = _norm_author(raw.get("author_name") or draft.get("author_name"))
    author_b = _norm_author(raw.get("author_name_b") or draft.get("author_name_b"))
    # Do not wipe author for「核心作者」alone when a specific person is named
    if re.search(
        r"(发展历程|演变趋势|学术发展|各阶段|核心作者团队|代表性研究机构)",
        question or "",
    ) and not author:
        author = None
        author_b = None
    if re.search(
        r"(发展历程|演变趋势|学术发展|各阶段|核心作者团队|代表性研究机构|主要研究方向)",
        question or "",
    ) and re.search(r"综合|优势|未来趋势|投稿建议", question or ""):
        # Overview questions: drop accidental person slots
        if not re.search(r"[一-龥]{2,4}(?:老师|教授)", question or ""):
            author = None
            author_b = None
    base["author_name"] = author
    base["author_name_b"] = author_b

    inst = raw.get("institution") or draft.get("institution")
    if inst:
        inst = str(inst).strip()
        if len(inst) < 2 or len(inst) > 40:
            inst = None
    if not inst:
        for cand in ("浙江大学", "杭州师范大学", "中国科学院"):
            if cand in (question or ""):
                inst = cand
                break
        if not inst:
            m = re.search(
                r"([\u4e00-\u9fff]{2,20}(?:大学|学院|研究院|研究所|科学院))",
                question or "",
            )
            if m:
                inst = m.group(1)
    base["institution"] = inst

    base["time_range"] = _resolve_time_range(raw.get("time_range"), question, draft)
    base["top_n"] = _norm_top_n(raw.get("top_n"), question)

    try:
        conf = float(raw.get("confidence") if raw.get("confidence") is not None else 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    base["confidence"] = max(0.0, min(conf, 1.0))

    # Goal heuristics — only clear submission-suitability asks
    q = question or ""
    if re.search(
        r"适合投稿|是否适合投|适合投|投稿前|适合发|"
        r"是否适合.{0,24}投稿|适合.{0,24}(?:方向)?投稿|"
        r"我的研究方向.{0,20}(相关论文|适合|投稿)|"
        r"我的论文主题.{0,20}(推荐|相关)",
        q,
    ) and not re.search(r"综合分析|优势领域|发展历程", q):
        base["goal"] = "submission_fit"
        base["operation"] = "coverage"
        base["entity"] = "topic"
        base["sources"] = ["sql"]
        if base["confidence"] < 0.75:
            base["confidence"] = max(base["confidence"], 0.8)
        if not base["topic"]:
            m = re.search(
                r"适合投(?:稿)?(?:给|向|到)?\s*([\u4e00-\u9fffA-Za-z0-9]{2,12})",
                q,
            )
            if m:
                ct = _clean_topic(m.group(1))
                if ct:
                    base["topic"] = [ct]

    if re.search(r"被引用|引用次数|高被引|被引次数|citation", q, re.I):
        base["goal"] = "refuse"
        base["legacy_task"] = "unsupported_citations"
        base["confidence"] = max(base["confidence"], 0.9)

    legacy = raw.get("legacy_task")
    if legacy:
        base["legacy_task"] = str(legacy)
    base["notes"] = str(raw.get("notes") or "")[:200]
    return refine_intent(base, question, draft)


def refine_intent(
    intent: Dict[str, Any],
    question: str = "",
    draft: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Post-normalize routing corrections for known failure modes."""
    draft = draft or {}
    out = dict(intent)
    q = question or ""
    topics = [t for t in (out.get("topic") or []) if _clean_topic(t)]
    # Drop institution names mistaken as topics
    topics = [t for t in topics if t != out.get("institution")]
    out["topic"] = topics[:5]

    author = out.get("author_name") or _norm_author(draft.get("author_name"))
    author_b = out.get("author_name_b") or _norm_author(draft.get("author_name_b"))
    if not author:
        for pat in (
            r"([一-龥]{2,4})(?:老师|教授)?研究团队",
            r"^([一-龥]{2,4})(?:老师|教授)?(?:在|于|的|和|与|跟)",
            r"([一-龥]{2,4})(?:老师|教授)?(?:在|于).{0,12}(?:发表|发文)",
        ):
            m = re.search(pat, q.strip())
            if m and _norm_author(m.group(1)):
                author = _norm_author(m.group(1))
                break
    out["author_name"] = author
    out["author_name_b"] = author_b

    # Author / team direction change → profile (before topic trend)
    if author and not author_b and re.search(
        r"研究团队|合作伙伴|研究方向有什么变化|主题变化|轨迹|主要合作",
        q,
    ):
        out["entity"] = "author"
        out["operation"] = "profile"
        out["goal"] = "research_analysis"
        out["sources"] = ["sql"]
        out["topic"] = []
        out["confidence"] = max(float(out.get("confidence") or 0), 0.85)

    # Q7-like: named author paper inventory → profile
    if author and not author_b and re.search(
        r"发表过|发表了|哪些论文|全部发文|发文情况|列出.*(?:标题|年份|主题)",
        q,
    ):
        out["entity"] = "author"
        out["operation"] = "profile"
        out["goal"] = "research_analysis"
        out["sources"] = ["sql"]
        out["confidence"] = max(float(out.get("confidence") or 0), 0.88)

    # Institution ranking (发文最多的机构 / 前十机构)
    if re.search(r"机构|单位", q) and re.search(
        r"最多|前\s*\d+|前\s*(十|五|三)|排名|高产", q
    ) and not re.search(r"合作|作者.*机构|机构.*作者|代表性成果", q):
        out["entity"] = "institution"
        out["operation"] = "rank"
        out["goal"] = "research_analysis"
        out["sources"] = ["sql"]
        out["author_name"] = None
        out["author_name_b"] = None
        out["topic"] = []
        out["confidence"] = max(float(out.get("confidence") or 0), 0.88)
        # skip later trend overrides
        return out

    # Author ranking (发文量前十作者) — before volume trend
    if (
        re.search(r"作者", q)
        and re.search(r"最多|前\s*\d+|前\s*(十|五|三)|排名|高产|发文量", q)
        and not re.search(r"机构|合作|研究过|关键词|主题", q)
    ):
        out["entity"] = "author"
        out["operation"] = "rank"
        out["goal"] = "research_analysis"
        out["sources"] = ["sql"]
        out["author_name"] = None
        out["topic"] = []
        out["confidence"] = max(float(out.get("confidence") or 0), 0.85)

    # Hotspot / theme evolution / A→B change → compare
    if re.search(
        r"热点演变|主题差异|研究主题.*(?:差异|变化)|从前\d+年到|前\d+年和后\d+年|"
        r"从[\u4e00-\u9fff]{2,8}到[\u4e00-\u9fff]{2,8}的变化|演变过程",
        q,
    ):
        out["entity"] = "journal"
        out["operation"] = "compare"
        out["goal"] = "research_analysis"
        out["sources"] = ["sql"]
        out["confidence"] = max(float(out.get("confidence") or 0), 0.88)

    # Emerging / future directions → inventory rank
    if re.search(r"新兴|未来可能|重点发展|重点关注|值得关注", q) and not re.search(
        r"适合投|是否适合", q
    ):
        out["entity"] = "topic"
        out["operation"] = "rank"
        out["goal"] = "inventory"
        out["sources"] = ["sql"]
        out["confidence"] = max(float(out.get("confidence") or 0), 0.85)

    # Topic-scoped trend (水稻/AI…) — keep real topics, force topic entity
    if (
        topics
        and not author
        and re.search(r"趋势|发展|变化", q)
        and not re.search(r"每年发文|发文数量|发表论文数量|增长最快", q)
    ):
        if out.get("operation") in {"trend", "search", "summarize"} or re.search(
            r"趋势|发展", q
        ):
            out["entity"] = "topic"
            out["operation"] = "trend"
            out["goal"] = "research_analysis"
            out["sources"] = ["sql"]
            out["confidence"] = max(float(out.get("confidence") or 0), 0.85)

    # Pure journal volume trend — not ranking of authors/institutions
    if (
        re.search(r"每年发文|发文数量|发表论文数量|增长最快|同比", q)
        and not topics
        and not re.search(r"机构|单位|作者|学者", q)
    ):
        out["entity"] = "journal"
        out["operation"] = "trend"
        out["topic"] = []

    # Institution collaboration network
    if re.search(r"机构.{0,8}合作|合作.{0,8}机构|机构之间", q) and not re.search(
        r"合作(?:者|的作者).{0,6}(?:机构|单位)|作者.*所属机构", q
    ):
        out["entity"] = "institution"
        out["operation"] = "compare"
        out["goal"] = "research_analysis"
        out["sources"] = ["sql", "kg"]
        out["confidence"] = max(float(out.get("confidence") or 0), 0.82)
        out["notes"] = "institution_collab"

    # Authors spanning multiple directions
    if re.search(r"同时覆盖|多个研究方向|跨方向|多方向", q):
        out["entity"] = "author"
        out["operation"] = "rank"
        out["metric"] = "keyword_freq"
        out["topic"] = []
        out["goal"] = "research_analysis"
        out["sources"] = ["sql"]
        out["confidence"] = max(float(out.get("confidence") or 0), 0.85)
        out["notes"] = "multi_direction_authors"

    # Comprehensive journal overview (not submission_fit)
    if re.search(
        r"综合分析|优势领域|发展历程.{0,20}核心作者|核心作者团队和未来|"
        r"优势领域.{0,12}研究热点",
        q,
    ):
        out["goal"] = "research_analysis"
        out["entity"] = "journal"
        out["operation"] = "summarize"
        out["sources"] = ["sql"]
        out["topic"] = []
        out["author_name"] = None
        out["author_name_b"] = None
        out["confidence"] = max(float(out.get("confidence") or 0), 0.88)
        out["notes"] = "journal_overview"

    # Core author network
    if re.search(r"核心作者网络|作者网络结构", q):
        out["entity"] = "author"
        out["operation"] = "summarize"
        out["goal"] = "research_analysis"
        out["sources"] = ["sql", "kg"]
        out["confidence"] = max(float(out.get("confidence") or 0), 0.82)
        out["notes"] = "author_network"

    return out


def intent_to_entities(intent: Dict[str, Any]) -> Dict[str, Any]:
    """Project schema slots into the entities dict used by executors."""
    tr = intent.get("time_range") or {}
    out: Dict[str, Any] = {
        "year_start": tr.get("start"),
        "year_end": tr.get("end"),
        "keywords": list(intent.get("topic") or []),
    }
    if intent.get("author_name"):
        out["author_name"] = intent["author_name"]
    if intent.get("author_name_b"):
        out["author_name_b"] = intent["author_name_b"]
        out["author_names"] = [
            intent["author_name"],
            intent["author_name_b"],
        ]
    if intent.get("institution"):
        out["institution"] = intent["institution"]
    return out
