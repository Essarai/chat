from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.agents.intent_schema import (
    _GENERIC_TECH_KEYWORDS,
    score_submission_fit_from_keyword_rows,
)
from app.agents.understand import (
    _looks_like_person_name,
    extract_author_name,
    extract_year_window,
)
from app.capabilities.schemas import CapabilityResult, err_result, ok_result
from app.config import bind_corpus, get_corpus_settings, get_settings
from app.services.sqlite_repo import SQLiteRepo

_KEYWORD_AUTHOR_RE = re.compile(
    r"(关键词|主题词).{0,12}(包含|含有|有)|"
    r"(包含|含有).{0,12}(关键词|主题词)|"
    r"作者.{0,16}(关键词|主题词)|(关键词|主题词).{0,16}作者",
    re.I,
)
_JOURNAL_OVERVIEW_RE = re.compile(
    r"(学术发展历程|发展历程|研究方向演变|各阶段|核心作者团队|"
    r"代表性研究机构|主要研究方向演变|值得关注的研究方向)",
    re.I,
)

_DB_CACHE: Dict[str, SQLiteRepo] = {}


def _db(journal_id: Optional[str] = None) -> SQLiteRepo:
    settings = bind_corpus(journal_id) if journal_id else get_settings()
    jid = settings.journal_id
    repo = _DB_CACHE.get(jid)
    if repo is None:
        repo = SQLiteRepo(get_corpus_settings(jid))
        _DB_CACHE[jid] = repo
    return repo

_TOPIC_STOP = {
    "热点",
    "研究热点",
    "主题",
    "领域",
    "研究领域",
    "研究方向",
    "发文",
    "发文量",
    "论文",
    "作者",
    "研究",
    "相关",
    "前十",
    "有哪些",
    "哪些",
    "近五",
    "近5",
    "热门关键词",
    "热门词",
    "热词",
}


def _pick_keyword(question: str, entities: Dict[str, Any]) -> Optional[str]:
    for k in entities.get("keywords") or []:
        s = str(k).strip().strip("“”\"'‘’")
        # reject polluted extracts like「近5年 过水稻发文量前十的 有」
        if s and 1 <= len(s) <= 12 and " " not in s and s not in _TOPIC_STOP:
            return s
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
            if s and s not in _TOPIC_STOP:
                return s
    # common short domain tokens appearing in the question
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
    ):
        if term in q:
            return term
    return None


def yoy_growth(yearly: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = sorted(
        [
            {"year": int(r["year"]), "paper_count": int(r.get("paper_count") or 0)}
            for r in (yearly or [])
            if r.get("year") is not None
        ],
        key=lambda x: x["year"],
    )
    out: List[Dict[str, Any]] = []
    prev = None
    for r in rows:
        delta = rate = None
        if prev is not None:
            delta = r["paper_count"] - prev
            if prev > 0:
                rate = round(delta / prev * 100, 1)
        out.append(
            {
                "year": r["year"],
                "paper_count": r["paper_count"],
                "delta": delta,
                "yoy_pct": rate,
            }
        )
        prev = r["paper_count"]
    return out


def fastest_growth(yoy: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = [r for r in yoy if r.get("delta") is not None]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda r: (int(r.get("delta") or 0), float(r.get("yoy_pct") or -9999)),
    )


def _period_triples(y0: int, y1: int) -> List[tuple]:
    span = max(y1 - y0 + 1, 1)
    step = max(span // 3, 1)
    chunks = [
        (y0, y0 + step - 1),
        (y0 + step, min(y0 + 2 * step - 1, y1)),
        (min(y0 + 2 * step, y1), y1),
    ]
    periods = []
    seen = set()
    for a, b in chunks:
        if a > b or (a, b) in seen:
            continue
        seen.add((a, b))
        periods.append((f"{a}-{b}", a, b))
    return periods


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


def _parse_top_n(plan: Dict[str, Any], question: str, default: int = 10) -> int:
    if plan.get("top_n") is not None:
        try:
            return max(1, min(int(plan["top_n"]), 50))
        except (TypeError, ValueError):
            pass
    m = re.search(r"前\s*(\d{1,2})", question or "")
    if m:
        return max(1, min(int(m.group(1)), 50))
    m = re.search(r"前\s*(两|二|三|四|五|六|七|八|九|十|十五|二十)", question or "")
    if m and m.group(1) in _CN_TOP_N:
        return _CN_TOP_N[m.group(1)]
    return default


def _hotspot_windows(
    question: str, y0: Optional[int], y1: Optional[int]
) -> List[tuple]:
    """Build prior-N vs recent-N keyword windows (default 10+10 years).

    Do not clamp the prior window with a 「近N年」-derived y0 — that would
    collapse prior into an empty/invalid range.
    """
    q = question or ""
    end = datetime.now().year
    # Prefer explicit 近N年 end, else plan end_year, else current year
    m_near = re.search(r"近\s*(\d{1,2})\s*年", q)
    m_prior = re.search(r"前\s*(\d{1,2})\s*年", q)
    near_n = int(m_near.group(1)) if m_near else 10
    prior_n = int(m_prior.group(1)) if m_prior else near_n
    if y1 is not None and not m_near:
        end = int(y1)
    near_start = end - near_n + 1
    prior_end = near_start - 1
    prior_start = prior_end - prior_n + 1
    if prior_start > prior_end:
        prior_start = prior_end
    return [
        (f"前{prior_n}年({prior_start}-{prior_end})", prior_start, prior_end),
        (f"近{near_n}年({near_start}-{end})", near_start, end),
    ]


def _execute_single_plan(
    plan: Dict[str, Any],
    question: str = "",
    entities: Dict[str, Any] | None = None,
    db: SQLiteRepo | None = None,
) -> Dict[str, Any]:
    """Run sql_ops from a QueryPlan and return aggregated business evidence."""
    db = db or _db()
    entities = entities or {}
    ops = list(plan.get("sql_ops") or [])
    task = plan.get("task") or "generic"
    y0 = plan.get("year_start")
    y1 = plan.get("year_end")
    if y0 is None and y1 is None:
        y0 = entities.get("year_start")
        y1 = entities.get("year_end")
    kws = [
        str(k).strip()
        for k in (plan.get("keywords") or entities.get("keywords") or [])
        if str(k).strip()
    ]
    author = plan.get("author_name") or entities.get("author_name")
    if author and not _looks_like_person_name(str(author)):
        author = None

    evidence: Dict[str, Any] = {
        "scope": task,
        "task": task,
        "start_year": y0,
        "end_year": y1,
        "source": "sqlite",
    }

    if "keyword_growth" in ops:
        data = db.keyword_growth(int(plan.get("top_n_directions") or plan.get("top_n") or 20), y0, y1)
        data["task"] = "keyword_growth"
        return data

    if "topic_period_compare" in ops:
        data = db.topic_period_compare(kws, y0, y1, int(plan.get("top_n") or 20))
        data["task"] = "topic_period_compare"
        return data

    if "author_direction_evolution" in ops:
        aid = None
        ids = plan.get("author_ids") or []
        if ids:
            aid = ids[0]
        data = db.author_direction_evolution(str(author) if author else None, aid, y0, y1)
        data["task"] = "author_direction_evolution"
        return data

    if "author_direction_diversity" in ops:
        data = db.author_direction_diversity(int(plan.get("top_n") or 10), y0, y1)
        data["task"] = "author_direction_diversity"
        return data

    if "institution_stability" in ops:
        data = db.institution_stability(int(plan.get("top_n") or 10), y0, y1)
        data["task"] = "institution_stability"
        return data

    if "present_resultset" in ops or task == "resultset_papers":
        grouped: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        targets = [t for t in (plan.get("targets") or []) if isinstance(t, dict)]
        for paper in targets[:100]:
            aid = str(paper.get("author_id") or "unknown")
            if aid not in grouped:
                order.append(aid)
                grouped[aid] = {
                    "author_id": aid,
                    "name_zh": paper.get("author_name") or "上一轮论文",
                    "papers": [],
                }
            grouped[aid]["papers"].append(
                {
                    "doi": paper.get("doi"),
                    "title_zh": paper.get("title"),
                    "year": paper.get("year"),
                }
            )
        evidence.update(
            {
                "task": "authors_papers",
                "scope": "authors_papers",
                "authors": [grouped[aid] for aid in order],
                "total_count": len(targets),
                "shown_count": min(len(targets), 100),
                "offset": 0,
                "next_offset": min(len(targets), 100),
                "has_more": len(targets) > 100,
            }
        )
        return evidence

    if "papers_for_authors" in ops or task == "authors_papers":
        author_ids = [str(v).strip() for v in (plan.get("author_ids") or []) if str(v).strip()]
        grouped = db.papers_for_authors(author_ids, y0, y1)
        target_names = {
            str(t.get("id")): t.get("name")
            for t in (plan.get("targets") or [])
            if isinstance(t, dict) and t.get("id")
        }
        flattened: List[tuple] = []
        for author in grouped:
            if not author.get("name_zh"):
                author["name_zh"] = target_names.get(str(author.get("author_id")))
            for paper in author.get("papers") or []:
                flattened.append((author, paper))
        total = len(flattened)
        offset = max(0, int(plan.get("offset") or 0))
        max_items = max(1, min(int(plan.get("max_items") or 100), 100))
        max_chars = max(1000, min(int(plan.get("max_chars") or 24000), 24000))
        selected: List[tuple] = []
        estimated = 0
        for author, paper in flattened[offset:]:
            cost = len(str(paper.get("title_zh") or paper.get("title_en") or "")) + len(str(paper.get("doi") or "")) + 120
            if selected and (len(selected) >= max_items or estimated + cost > max_chars):
                break
            selected.append((author, paper))
            estimated += cost
        selected_by_author: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for author, paper in selected:
            aid = str(author.get("author_id"))
            if aid not in selected_by_author:
                order.append(aid)
                selected_by_author[aid] = {
                    "author_id": aid,
                    "name_zh": author.get("name_zh"),
                    "name_en": author.get("name_en"),
                    "papers": [],
                }
            selected_by_author[aid]["papers"].append(paper)
        shown = len(selected)
        evidence.update(
            {
                "task": "authors_papers",
                "scope": "authors_papers",
                "authors": [selected_by_author[aid] for aid in order],
                "author_ids": author_ids,
                "total_count": total,
                "shown_count": shown,
                "offset": offset,
                "next_offset": offset + shown,
                "has_more": offset + shown < total,
            }
        )
        return evidence

    if "unsupported_citations" in ops or task == "unsupported_citations":
        return {
            "scope": "unsupported",
            "task": "unsupported_citations",
            "reason": (
                "本库 papers 表无被引/引用次数字段，无法按被引用次数排名高被引论文。"
            ),
            "source": "sqlite",
            "start_year": y0,
            "end_year": y1,
        }

    if "hotspot_compare" in ops or task == "hotspot_compare":
        windows = _hotspot_windows(question, y0, y1)
        periods = []
        for label, a, b in windows:
            kws_rows = db.top_keywords(15, a, b)
            periods.append(
                {
                    "period": label,
                    "start_year": a,
                    "end_year": b,
                    "paper_count": sum(
                        int(r.get("paper_count") or 0)
                        for r in db.yearly_counts(a, b)
                    ),
                    "keywords": kws_rows,
                }
            )
        return {
            "scope": "hotspot_compare",
            "task": "hotspot_compare",
            "periods": periods,
            "start_year": periods[0]["start_year"] if periods else y0,
            "end_year": periods[-1]["end_year"] if periods else y1,
            "source": "sqlite",
        }

    if (
        "topic_coverage" in ops
        or task == "topic_coverage"
        or "submission_fit" in ops
        or task == "submission_fit"
    ):
        if not kws:
            kws = ["基因编辑", "CRISPR", "基因组编辑"]
        stats = db.topic_keyword_stats(kws, y0, y1)
        topic_rows = stats.get("topic_keywords") or []
        fit_meta = score_submission_fit_from_keyword_rows(topic_rows)
        fit_label = fit_meta["fit_label"]
        total = int(fit_meta["total_hits"])

        # Prefer domain-keyword papers for samples; if domain facet is empty,
        # keep generic-tech papers only as adjacent clues (not as fit evidence).
        specific_kws = [str(k) for k in kws if str(k) not in _GENERIC_TECH_KEYWORDS]
        generic_kws = [str(k) for k in kws if str(k) in _GENERIC_TECH_KEYWORDS]
        papers: List[Dict[str, Any]] = []
        generic_papers: List[Dict[str, Any]] = []
        seen: set = set()

        def _collect(keys: List[str], bucket: List[Dict[str, Any]], lim: int = 8) -> None:
            for kw in keys:
                for p in db.papers_by_keyword(
                    str(kw), limit=lim, start_year=y0, end_year=y1
                ):
                    doi = (p.get("doi") or "").lower()
                    if doi and doi not in seen:
                        seen.add(doi)
                        bucket.append(p)

        if specific_kws:
            _collect(specific_kws, papers)
        if not papers and generic_kws:
            _collect(generic_kws, generic_papers)
        elif generic_kws and fit_meta.get("score_basis") != "generic_only":
            _collect(generic_kws, papers, lim=3)

        # Rising: for domain-gated topics, use domain keywords only
        rising = False
        yearly_by = stats.get("yearly_by_keyword") or {}
        year_totals: Dict[int, int] = {}
        rise_keys = (
            specific_kws
            if fit_meta.get("has_specific_slot")
            else [str(k) for k in kws]
        )
        for kw in rise_keys:
            for row in yearly_by.get(str(kw)) or []:
                try:
                    y = int(row.get("year"))
                    year_totals[y] = year_totals.get(y, 0) + int(
                        row.get("paper_count") or 0
                    )
                except (TypeError, ValueError):
                    continue
        if year_totals:
            years_sorted = sorted(year_totals)
            mid = years_sorted[len(years_sorted) // 2]
            early = sum(c for y, c in year_totals.items() if y < mid)
            late = sum(c for y, c in year_totals.items() if y >= mid)
            rising = late > early

        is_fit = task == "submission_fit" or "submission_fit" in ops
        scope = "submission_fit" if is_fit else "topic_coverage"
        out_task = "submission_fit" if is_fit else "topic_coverage"
        sample = papers[:8] if papers else []
        return {
            **stats,
            "scope": scope,
            "task": out_task,
            "papers": sample,
            "sample_papers": sample,
            "generic_only_papers": generic_papers[:8],
            "total_hits": total,
            "effective_hits": fit_meta.get("effective_hits"),
            "keywords_queried": kws,
            "coverage_summary": {
                "hit_papers_est": total,
                "effective_hits": fit_meta.get("effective_hits"),
                "rising_recently": rising,
                "score_basis": fit_meta.get("score_basis"),
                "generic_hits": fit_meta.get("generic_hits"),
                "specific_hits": fit_meta.get("specific_hits"),
                "keyword_count": len(
                    [r for r in topic_rows if int(r.get("paper_count") or 0) > 0]
                ),
            },
            "fit_label": fit_label,
            "start_year": y0,
            "end_year": y1,
            "source": "sqlite",
        }

    if "journal_overview" in ops or task == "journal_overview":
        return db.journal_overview(y0, y1)

    if "coauthored_papers" in ops or task == "coauthored_papers":
        name_b = plan.get("author_name_b") or entities.get("author_name_b")
        names = entities.get("author_names") or []
        if not name_b and len(names) >= 2:
            author = author or names[0]
            name_b = names[1]
        if author and name_b:
            data = db.coauthored_papers(str(author), str(name_b))
            data["task"] = "coauthored_papers"
            return data

    if "author_profile" in ops:
        if not author:
            return {
                "author": None,
                "author_name": None,
                "scope": "author",
                "task": "author_profile",
                "found": False,
                "papers": [],
                "total_papers": 0,
                "source": "sqlite",
                "note": "问题未识别到有效作者姓名",
            }
        profile = db.author_profile(author)
        if not profile.get("author"):
            return {
                **profile,
                "author_name": author,
                "task": "author_profile",
                "found": False,
                "papers": [],
                "total_papers": 0,
                "note": f"本刊库中未找到作者「{author}」",
            }
        evidence = {**profile, "author_name": author, "task": task, "found": True}
        q = question or ""
        # Pair coauthor questions should not expand to full collaborator dumps.
        if entities.get("author_name_b") and re.search(r"合作|合著|共著", q):
            return evidence
        if any(
            k in q
            for k in (
                "机构",
                "单位",
                "合作",
                "发文",
                "概况",
                "情况",
                "分布",
                "轨迹",
                "主题",
                "首次发表",
            )
        ):
            evidence["collaborator_institutions"] = db.collaborator_institutions(author)
            evidence["collaborators"] = db.author_collaborators(author, limit=15)
        return evidence

    if "authors_by_keyword" in ops or task == "keyword_authors":
        kw = (kws[0] if kws else None) or _pick_keyword(question, entities) or ""
        top_n = _parse_top_n(
            plan, question, default=10 if task == "keyword_authors" else 30
        )
        # ranking questions usually want a short list without long paper dumps
        papers_per = int(plan.get("papers_per_author") or (2 if task == "keyword_authors" else 8))
        data = db.authors_by_keyword(
            kw,
            author_limit=top_n,
            papers_per_author=papers_per,
            start_year=y0,
            end_year=y1,
        )
        evidence.update(data)
        evidence["task"] = "keyword_authors"
        evidence["scope"] = "keyword_authors"
        evidence["top_n"] = top_n
        if task == "keyword_authors":
            return evidence

    if "institutions_by_keyword" in ops:
        kw = (kws[0] if kws else None) or _pick_keyword(question, entities) or ""
        inst = db.institutions_by_keyword(kw, 15, y0, y1)
        evidence["institutions"] = inst.get("institutions") or []
        evidence["keyword"] = kw or evidence.get("keyword")
        evidence["scope"] = (
            "keyword_collab" if task == "keyword_collab" else evidence.get("scope")
        )
        evidence["task"] = task

    if "topic_keyword_counts" in ops or "topic_yearly" in ops:
        stats = db.topic_keyword_stats(kws, y0, y1)
        evidence.update(stats)
        evidence["task"] = task
        evidence["scope"] = "topic_stats"

    if "yearly_counts" in ops or "yoy_growth" in ops:
        yearly = db.yearly_counts(y0, y1)
        evidence["yearly"] = yearly
        if "yoy_growth" in ops:
            yoy = yoy_growth(yearly)
            evidence["yoy"] = yoy
            evidence["fastest_growth"] = fastest_growth(yoy)
        evidence["scope"] = task if task != "generic" else "journal"
        evidence["task"] = task

    if "top_keywords" in ops:
        evidence["keywords"] = db.top_keywords(20, y0, y1)
        evidence["scope"] = evidence.get("scope") or "journal"
        evidence["task"] = task

    if "papers_by_top_keywords" in ops:
        top_n = int(plan.get("top_n_directions") or 3)
        top_kws = evidence.get("keywords") or db.top_keywords(top_n, y0, y1)
        directions = []
        for r in top_kws[:top_n]:
            kw = r.get("keyword")
            papers = db.papers_by_keyword(str(kw), limit=6, start_year=y0, end_year=y1)
            directions.append(
                {"keyword": kw, "paper_count": r.get("paper_count"), "papers": papers}
            )
        evidence["directions"] = directions
        evidence["keywords"] = top_kws
        evidence["scope"] = "top_directions"
        evidence["task"] = task

    if "top_authors" in ops or task == "top_authors":
        top_n = _parse_top_n(plan, question, default=10 if task == "top_authors" else 15)
        evidence["authors"] = db.top_authors(top_n, y0, y1)
        evidence["task"] = "top_authors" if task == "top_authors" else task
        evidence["scope"] = "top_authors" if task == "top_authors" else (
            evidence.get("scope") or "top_teams"
        )
        evidence["top_n"] = top_n
        if task == "top_authors":
            return evidence

    if "top_institutions" in ops:
        top_n = _parse_top_n(plan, question, default=10 if task == "top_institutions" else 15)
        evidence["institutions"] = db.top_institutions(top_n, y0, y1)
        evidence["task"] = task
        evidence["scope"] = (
            "top_institutions" if task == "top_institutions" else (evidence.get("scope") or "top_teams")
        )
        evidence["top_n"] = top_n

    if "institution_authors" in ops or task == "institution_authors":
        inst = (
            plan.get("institution")
            or entities.get("institution")
            or ""
        )
        if not inst:
            m = re.search(
                r"([\u4e00-\u9fff]{2,20}(?:大学|学院|研究院|研究所|科学院))",
                question or "",
            )
            inst = m.group(1) if m else ""
        top_n = int(plan.get("top_n_authors") or plan.get("top_n") or 8)
        per = int(plan.get("papers_per_author") or 3)
        data = db.institution_authors_with_papers(
            str(inst),
            top_authors=top_n,
            papers_per_author=per,
            start_year=y0,
            end_year=y1,
        )
        evidence.update(data)
        evidence["task"] = "institution_authors"
        evidence["scope"] = "institution_authors"
        return evidence

    if "keywords_by_periods" in ops and y0 is not None and y1 is not None:
        evidence["periods"] = db.keywords_by_periods(_period_triples(int(y0), int(y1)), 8)
        evidence["task"] = task

    if "author_keywords_sample" in ops:
        evidence["author_keywords"] = db.author_keywords_sample(8, 6, y0, y1)
        evidence["task"] = task
        evidence["scope"] = evidence.get("scope") or "top_teams"

    if len(evidence) <= 5 and not evidence.get("yearly") and not evidence.get("authors"):
        if not ops:
            return {}
    return evidence


def execute_plan(
    plan: Dict[str, Any],
    question: str = "",
    entities: Dict[str, Any] | None = None,
    db: SQLiteRepo | None = None,
) -> Dict[str, Any]:
    """Execute canonical SQL operations without allowing early-return loss.

    Each operation is run against a single-operation legacy adapter.  The
    aggregate retains namespaced data for coverage/rendering and exposes a
    backward-compatible top-level view for existing templates.
    """
    from app.agents.operation_contracts import normalize_query_plan

    db = db or _db()
    entities = entities or {}
    canonical = normalize_query_plan(plan, question)
    sql_specs = [
        op for op in canonical.get("operations") or []
        if isinstance(op, dict) and op.get("source") == "sql"
        and op.get("type") != "submission_guidance"
    ]
    if not sql_specs:
        return _execute_single_plan(canonical, question, entities, db)

    operation_data: Dict[str, Dict[str, Any]] = {}
    aggregate: Dict[str, Any] = {
        "scope": canonical.get("main_task") or canonical.get("task") or "generic",
        "task": canonical.get("main_task") or canonical.get("task") or "generic",
        "start_year": canonical.get("year_start"),
        "end_year": canonical.get("year_end"),
        "source": "sqlite",
    }
    author_ids = list(canonical.get("author_ids") or [])
    for spec in sql_specs:
        op_type = str(spec.get("type") or "")
        run_type = "author_profile" if op_type == "author_topic_summary" else op_type
        subplan = dict(canonical)
        subplan["operations"] = []
        subplan["sql_ops"] = [run_type]
        subplan["task"] = run_type
        params = dict(spec.get("params") or {})
        for key, value in params.items():
            subplan[key] = value
        if op_type == "papers_for_authors" and not author_ids:
            for prior in operation_data.values():
                for row in prior.get("authors") or []:
                    aid = row.get("author_id")
                    if aid and aid not in author_ids:
                        author_ids.append(aid)
            subplan["author_ids"] = author_ids
        try:
            data = _execute_single_plan(subplan, question, entities, db)
        except Exception as exc:  # operation-level error, assessed downstream
            data = {"error": str(exc), "task": op_type, "scope": op_type, "source": "sqlite"}
        data = dict(data or {})
        data["operation"] = op_type
        operation_data[str(spec.get("id") or op_type)] = data
        for key, value in data.items():
            if key not in aggregate or aggregate.get(key) in (None, [], {}):
                aggregate[key] = value

    aggregate["scope"] = canonical.get("main_task") or canonical.get("task") or aggregate.get("scope")
    aggregate["task"] = canonical.get("main_task") or canonical.get("task") or aggregate.get("task")
    aggregate["operation_data"] = operation_data
    if len(operation_data) == 1:
        only = dict(next(iter(operation_data.values())))
        only["operation_data"] = operation_data
        return only
    return aggregate


def run_legacy(
    question: str,
    entities: Dict[str, Any] | None = None,
    db: SQLiteRepo | None = None,
) -> Dict[str, Any]:
    """Heuristic SQL path when no structured plan is available."""
    db = db or _db()
    entities = entities or {}
    author = entities.get("author_name") or extract_author_name(question)
    if author and not _looks_like_person_name(str(author)):
        author = None
    q = question or ""

    kw = _pick_keyword(q, entities)
    if (
        not author
        and kw
        and (
            _KEYWORD_AUTHOR_RE.search(q)
            or (re.search(r"作者|哪些人|谁", q) and re.search(r"关键词|主题词", q))
        )
    ):
        return db.authors_by_keyword(kw)

    if not author and _JOURNAL_OVERVIEW_RE.search(q):
        y0 = entities.get("year_start")
        y1 = entities.get("year_end")
        if y0 is None and y1 is None:
            y0, y1 = extract_year_window(question, default_last_n=20)
        return db.journal_overview(y0, y1)

    if author:
        profile = db.author_profile(author)
        if not profile.get("author"):
            # Do NOT fall back to journal-wide stats for a named-author miss —
            # that invites the synthesizer to invent a fake personal profile.
            return {
                **profile,
                "author_name": author,
                "task": "author_profile",
                "found": False,
                "papers": [],
                "total_papers": 0,
                "note": f"本刊库中未找到作者「{author}」",
            }
        evidence: Dict[str, Any] = {
            **profile,
            "author_name": author,
            "start_year": profile.get("year_min"),
            "end_year": profile.get("year_max"),
            "found": True,
        }
        if any(k in q for k in ("机构", "单位", "合作", "发文", "概况", "情况", "分布")):
            evidence["collaborator_institutions"] = db.collaborator_institutions(author)
            evidence["collaborators"] = db.author_collaborators(author, limit=15)
        return evidence

    y0 = entities.get("year_start")
    y1 = entities.get("year_end")
    if y0 is None and y1 is None:
        y0, y1 = extract_year_window(question, default_last_n=10)
    return {
        "scope": "journal",
        "start_year": y0,
        "end_year": y1,
        "yearly": db.yearly_counts(y0, y1),
        "keywords": db.top_keywords(20, y0, y1),
        "funds": db.top_funds(12),
        "source": "sqlite",
    }


def invoke(
    operation: str,
    *,
    question: str = "",
    entities: Dict[str, Any] | None = None,
    plan: Dict[str, Any] | None = None,
    journal_id: Optional[str] = None,
    **params: Any,
) -> CapabilityResult:
    """Standardized SQL capability entrypoint."""
    try:
        db = _db(journal_id or params.get("journal_id"))
        entities = entities or {}
        plan = plan or {}

        if operation == "execute_plan":
            data = execute_plan(plan, question, entities, db)
            if not data:
                data = run_legacy(question, entities, db)
            return ok_result("sql", operation, data)

        if operation == "yearly_counts":
            data = {
                "yearly": db.yearly_counts(params.get("year_start"), params.get("year_end")),
                "start_year": params.get("year_start"),
                "end_year": params.get("year_end"),
            }
            return ok_result("sql", operation, data)

        if operation == "journal_overview":
            return ok_result(
                "sql",
                operation,
                db.journal_overview(params.get("year_start"), params.get("year_end")),
            )

        if operation == "top_keywords":
            return ok_result(
                "sql",
                operation,
                {
                    "keywords": db.top_keywords(
                        params.get("limit", 20),
                        params.get("year_start"),
                        params.get("year_end"),
                    )
                },
            )

        if operation == "keyword_growth":
            return ok_result("sql", operation, db.keyword_growth(
                params.get("limit", 20), params.get("year_start"), params.get("year_end")
            ))

        if operation == "topic_period_compare":
            return ok_result("sql", operation, db.topic_period_compare(
                params.get("keywords") or [], params.get("year_start"), params.get("year_end"), params.get("limit", 20)
            ))

        if operation == "author_direction_evolution":
            return ok_result("sql", operation, db.author_direction_evolution(
                params.get("author_name"), params.get("author_id"), params.get("year_start"), params.get("year_end")
            ))

        if operation == "author_direction_diversity":
            return ok_result("sql", operation, db.author_direction_diversity(
                params.get("limit", 10), params.get("year_start"), params.get("year_end")
            ))

        if operation == "institution_stability":
            return ok_result("sql", operation, db.institution_stability(
                params.get("limit", 10), params.get("year_start"), params.get("year_end")
            ))

        if operation == "authors_by_keyword":
            return ok_result(
                "sql",
                operation,
                db.authors_by_keyword(params.get("keyword") or ""),
            )

        if operation == "author_profile":
            return ok_result("sql", operation, db.author_profile(params.get("name") or ""))

        if operation == "legacy":
            return ok_result("sql", operation, run_legacy(question, entities, db))

        return err_result("sql", operation, f"unknown sql operation: {operation}")
    except Exception as e:
        return err_result("sql", operation, str(e))
