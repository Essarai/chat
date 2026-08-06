from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.agents.understand import (
    _looks_like_person_name,
    extract_author_name,
    extract_year_window,
)
from app.capabilities.schemas import CapabilityResult, err_result, ok_result
from app.config import get_settings
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


def _db() -> SQLiteRepo:
    return SQLiteRepo(get_settings())


def _pick_keyword(question: str, entities: Dict[str, Any]) -> Optional[str]:
    for k in entities.get("keywords") or []:
        s = str(k).strip().strip("“”\"'‘’")
        if s and len(s) <= 20:
            return s
    m = re.search(r"[“\"‘']([^”\"’']+)[”\"’']", question or "")
    return m.group(1).strip() if m else None


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


def execute_plan(
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

    if "author_profile" in ops and author:
        profile = db.author_profile(author)
        if profile.get("author"):
            evidence = {**profile, "author_name": author, "task": task}
            q = question or ""
            # Pair coauthor questions should not expand to full collaborator dumps.
            if entities.get("author_name_b") and re.search(r"合作|合著|共著", q):
                return evidence
            if any(k in q for k in ("机构", "单位", "合作", "发文", "概况", "情况", "分布")):
                evidence["collaborator_institutions"] = db.collaborator_institutions(author)
                evidence["collaborators"] = db.author_collaborators(author, limit=15)
            return evidence

    if "authors_by_keyword" in ops:
        kw = (kws[0] if kws else None) or _pick_keyword(question, entities) or ""
        data = db.authors_by_keyword(kw)
        evidence.update(data)
        evidence["task"] = task
        evidence["scope"] = data.get("scope") or task

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

    if "top_authors" in ops:
        evidence["authors"] = db.top_authors(15, y0, y1)
        evidence["task"] = task
        evidence["scope"] = evidence.get("scope") or "top_teams"

    if "top_institutions" in ops:
        evidence["institutions"] = db.top_institutions(15, y0, y1)
        evidence["task"] = task
        evidence["scope"] = evidence.get("scope") or "top_teams"

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
            y0 = entities.get("year_start")
            y1 = entities.get("year_end")
            if y0 is None and y1 is None:
                y0, y1 = extract_year_window(question, default_last_n=10)
            if _JOURNAL_OVERVIEW_RE.search(q):
                return db.journal_overview(y0, y1)
            return {
                "scope": "journal",
                "start_year": y0,
                "end_year": y1,
                "yearly": db.yearly_counts(y0, y1),
                "keywords": db.top_keywords(20, y0, y1),
                "funds": db.top_funds(12),
                "authors": db.top_authors(12, y0, y1),
                "institutions": db.top_institutions(12, y0, y1),
                "note": f"未识别到有效作者「{author}」，已改用全刊统计",
                "source": "sqlite",
            }
        evidence: Dict[str, Any] = {
            **profile,
            "author_name": author,
            "start_year": profile.get("year_min"),
            "end_year": profile.get("year_max"),
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
    **params: Any,
) -> CapabilityResult:
    """Standardized SQL capability entrypoint."""
    try:
        db = _db()
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
