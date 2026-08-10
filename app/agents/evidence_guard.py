"""Evidence constraints for final answers: numbers, papers, years, DOIs."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


def _year_window(state: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    plan = state.get("query_plan") or {}
    ents = state.get("entities") or {}
    sql = state.get("sql_evidence") or {}
    y0 = plan.get("year_start") or ents.get("year_start") or sql.get("start_year")
    y1 = plan.get("year_end") or ents.get("year_end") or sql.get("end_year")
    try:
        y0 = int(y0) if y0 is not None else None
    except (TypeError, ValueError):
        y0 = None
    try:
        y1 = int(y1) if y1 is not None else None
    except (TypeError, ValueError):
        y1 = None
    return y0, y1


def collect_allowed_numbers(state: Dict[str, Any]) -> Set[str]:
    """Collect numeric tokens that appear in SQL/KG structured evidence."""
    allowed: Set[str] = set()

    def add(n: Any) -> None:
        if n is None:
            return
        try:
            if isinstance(n, float):
                allowed.add(str(n))
                if n == int(n):
                    allowed.add(str(int(n)))
            else:
                allowed.add(str(int(n)))
        except (TypeError, ValueError):
            s = str(n).strip()
            if re.fullmatch(r"-?\d+(\.\d+)?", s):
                allowed.add(s)

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()
                if any(
                    x in lk
                    for x in (
                        "count",
                        "paper",
                        "year",
                        "delta",
                        "yoy",
                        "pct",
                        "total",
                        "co_paper",
                    )
                ):
                    add(v)
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for x in obj[:80]:
                walk(x, depth + 1)

    walk(state.get("sql_evidence") or {})
    walk(state.get("kg_evidence") or {})
    walk(state.get("operation_results") or [])
    # year window bounds always allowed
    y0, y1 = _year_window(state)
    add(y0)
    add(y1)
    return allowed


def collect_allowed_papers(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map doi_lower -> {doi, title, year} from SQL/RAG/KG evidence."""
    by_doi: Dict[str, Dict[str, Any]] = {}
    y0, y1 = _year_window(state)
    plan = state.get("query_plan") or {}
    sql = state.get("sql_evidence") or {}
    task = (plan.get("task") or sql.get("task") or sql.get("scope") or "").strip()
    sources = list(plan.get("sources") or state.get("intents") or [])
    sql_only_tasks = {
        "unsupported_citations",
        "hotspot_compare",
        "top_institutions",
        "top_authors",
        "institution_authors",
        "yearly_growth",
        "topic_coverage",
        "submission_fit",
        "topic_evolution",
        "author_profile",
        "coauthored_papers",
        "keyword_authors",
        "journal_overview",
        "top_teams",
        "authors_papers",
        "resultset_papers",
        "clarification",
    }
    author_scoped = task in {"author_profile", "author"} or sql.get("scope") == "author"
    exclude_rag = (
        task in sql_only_tasks
        or sources == ["sql"]
        or (len(sources) == 1 and sources[0] == "sql")
    )
    if task == "unsupported_citations" or sql.get("scope") == "unsupported":
        return {}

    def _year_ok(year: Any) -> bool:
        # Author profiles span full career — do not clip by journal window.
        if author_scoped:
            return True
        if y0 is None and y1 is None:
            return True
        if year is None:
            return False
        try:
            y = int(year)
        except (TypeError, ValueError):
            return False
        if y0 is not None and y < y0:
            return False
        if y1 is not None and y > y1:
            return False
        return True

    def add_paper(doi: Any, title: Any = None, year: Any = None) -> None:
        d = (str(doi or "")).strip()
        if not d:
            return
        if not _year_ok(year):
            return
        key = d.lower()
        prev = by_doi.get(key) or {}
        by_doi[key] = {
            "doi": d,
            "title": (title or prev.get("title") or "").strip(),
            "year": year if year is not None else prev.get("year"),
        }

    for p in sql.get("papers") or sql.get("recent_papers") or []:
        add_paper(p.get("doi"), p.get("title_zh") or p.get("title"), p.get("year"))
    for d in sql.get("directions") or []:
        for p in d.get("papers") or []:
            add_paper(p.get("doi"), p.get("title_zh") or p.get("title"), p.get("year"))
    for a in sql.get("authors") or []:
        if isinstance(a, dict):
            for p in a.get("papers") or []:
                add_paper(p.get("doi"), p.get("title_zh") or p.get("title"), p.get("year"))

    # Canonical multi-operation evidence may be namespaced instead of flattened.
    for result in state.get("operation_results") or []:
        data = result.get("data") or {}
        for p in data.get("papers") or data.get("recent_papers") or []:
            add_paper(p.get("doi"), p.get("title_zh") or p.get("title"), p.get("year"))
        for direction in data.get("directions") or []:
            for p in direction.get("papers") or []:
                add_paper(p.get("doi"), p.get("title_zh") or p.get("title"), p.get("year"))
        for author_row in data.get("authors") or []:
            if not isinstance(author_row, dict):
                continue
            for p in author_row.get("papers") or []:
                add_paper(p.get("doi"), p.get("title_zh") or p.get("title"), p.get("year"))
        for period in data.get("periods") or []:
            for p in period.get("papers") or []:
                add_paper(p.get("doi"), p.get("title_zh") or p.get("title"), p.get("year"))

    # Author / SQL-only tasks: do not admit RAG or unrelated KG keyword papers.
    if author_scoped or exclude_rag:
        return by_doi

    rag = state.get("rag_evidence") or {}
    for h in rag.get("hits") or []:
        add_paper(h.get("doi"), h.get("title"), h.get("year"))
    for c in rag.get("citations") or []:
        add_paper(c.get("doi"), c.get("title") or c.get("title_zh"), c.get("year"))

    kg = state.get("kg_evidence") or {}
    for p in kg.get("keyword_papers") or kg.get("papers") or []:
        add_paper(p.get("doi"), p.get("title") or p.get("title_zh"), p.get("year"))
    if kg.get("paper"):
        p = kg["paper"]
        add_paper(p.get("doi"), p.get("title") or p.get("title_zh"), p.get("year"))
    for n in kg.get("nodes") or []:
        if (n.get("group") or n.get("type")) == "paper":
            nid = str(n.get("id") or "")
            doi = nid.split("paper:", 1)[-1] if nid.startswith("paper:") else nid
            # KG graph nodes often lack year; only admit when year present & in window.
            add_paper(doi, n.get("title") or n.get("label"), n.get("year"))

    return by_doi


def build_evidence_ledger(state: Dict[str, Any]) -> str:
    """Compact allowlist text injected into synthesizer prompt."""
    y0, y1 = _year_window(state)
    papers = collect_allowed_papers(state)
    sql = state.get("sql_evidence") or {}
    lines = [
        "[证据约束·仅可使用以下事实]",
        f"允许时间窗: {y0 or '不限'}–{y1 or '不限'}",
    ]
    # highlight key SQL numbers
    if sql.get("yoy") or sql.get("yearly"):
        rows = sql.get("yoy") or sql.get("yearly") or []
        lines.append(
            "SQL逐年发文: "
            + ", ".join(f"{r.get('year')}:{r.get('paper_count')}" for r in rows[:25])
        )
        fg = sql.get("fastest_growth")
        if fg:
            lines.append(
                f"SQL预计算最快增长年: {fg.get('year')} Δ{fg.get('delta')} 同比{fg.get('yoy_pct')}%"
            )
    if sql.get("keywords"):
        lines.append(
            "SQL热词: "
            + ", ".join(
                f"{r.get('keyword')}({r.get('paper_count')})"
                for r in (sql.get("keywords") or [])[:15]
            )
        )
    if sql.get("authors"):
        lines.append(
            "SQL作者: "
            + ", ".join(
                f"{a.get('name_zh')}({a.get('paper_count')})"
                for a in (sql.get("authors") or [])[:15]
                if isinstance(a, dict)
            )
        )
    if sql.get("institutions"):
        lines.append(
            "SQL机构: "
            + ", ".join(
                f"{i.get('institution')}({i.get('paper_count')})"
                for i in (sql.get("institutions") or [])[:15]
                if isinstance(i, dict)
            )
        )
    if sql.get("directions"):
        for d in (sql.get("directions") or [])[:5]:
            lines.append(f"方向「{d.get('keyword')}」({d.get('paper_count')}篇) 代表论文DOI:")
            for p in (d.get("papers") or [])[:6]:
                lines.append(
                    f"  - {p.get('title_zh')} | {p.get('year')} | {p.get('doi')}"
                )

    kg = state.get("kg_evidence") or {}
    if kg.get("authors") or kg.get("neighborhood_authors"):
        authors = kg.get("neighborhood_authors") or kg.get("authors") or []
        lines.append(
            "KG邻域作者: "
            + ", ".join(
                f"{a.get('name_zh') or a.get('name')}({a.get('paper_count') or a.get('co_papers')})"
                for a in authors[:12]
                if isinstance(a, dict)
            )
        )
    if kg.get("institutions") or kg.get("neighborhood_institutions"):
        insts = kg.get("neighborhood_institutions") or kg.get("institutions") or []
        lines.append(
            "KG邻域机构: "
            + ", ".join(
                f"{i.get('institution') or i.get('name')}({i.get('paper_count')})"
                for i in insts[:12]
                if isinstance(i, dict)
            )
        )
    if kg.get("related_keywords"):
        lines.append(
            "KG共现关键词: "
            + ", ".join(
                f"{k.get('label') or k.get('keyword')}({k.get('paper_count')})"
                for k in (kg.get("related_keywords") or [])[:10]
                if isinstance(k, dict)
            )
        )
    if kg.get("collaborators"):
        lines.append(
            "KG合作者: "
            + ", ".join(
                f"{c.get('name_zh')}({c.get('co_papers')})"
                for c in (kg.get("collaborators") or [])[:12]
                if isinstance(c, dict)
            )
        )

    if papers:
        lines.append(f"允许引用的论文DOI共 {len(papers)} 条（回答中的DOI必须在此集合）:")
        for i, p in enumerate(list(papers.values())[:20], 1):
            lines.append(
                f"  {i}. {p.get('title') or '（无题名）'} | {p.get('year')} | {p.get('doi')}"
            )
    else:
        lines.append("允许引用的论文DOI: （证据中无论文，禁止编造DOI/题名）")

    lines.append(
        "硬约束: ①每个统计数字须能在SQL/KG中找到对应；"
        "②每篇论文题名/DOI必须在允许列表；"
        "③论文年份须落在时间窗内（若窗口已知）；"
        "④禁止编造未出现的数字/DOI/论文。"
    )
    return "\n".join(lines)


_DOI_RE = re.compile(
    r"(?:DOI\s*[:：]\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.I,
)
_YEAR_IN_PARENS_RE = re.compile(r"[（(]\s*(20\d{2})\s*年?\s*[)）]")


def _normalize_doi(doi: str) -> str:
    """Strip trailing punctuation / markdown link closers from a DOI capture."""
    d = (doi or "").strip()
    # Markdown links often yield `10.xxxx/yyy)` — keep balanced inner ().
    while d and d[-1] in ").,;]}>\"'":
        if d.endswith(")") and d.count("(") >= d.count(")"):
            break
        d = d[:-1]
    return d.lower()


def enforce_evidence_constraints(answer: str, state: Dict[str, Any]) -> str:
    """
    Post-filter answer:
    - drop / mark paper lines whose DOI is not in evidence
    - drop years clearly outside window when attached to paper lines
    Does not invent replacements; prefers removal of unsupported claims.
    """
    if not answer:
        return answer
    papers = collect_allowed_papers(state)
    allowed_dois = set(papers.keys())
    y0, y1 = _year_window(state)
    lines = answer.split("\n")
    out: List[str] = []
    removed = 0

    for line in lines:
        raw_dois = [m.group(1) for m in _DOI_RE.finditer(line)]
        dois = [_normalize_doi(d) for d in raw_dois]
        dois = [d for d in dois if d]
        if dois:
            bad = [d for d in dois if d not in allowed_dois]
            # Keep line if any captured DOI is allowed (URL paren artifacts normalize away).
            if bad and allowed_dois and not any(d in allowed_dois for d in dois):
                if re.match(r"^\s*\d+[\.、)]\s*", line) or "查看全文" in line:
                    removed += 1
                    continue
                for d in bad:
                    line = re.sub(re.escape(d), "（证据中无此DOI，已省略）", line, flags=re.I)
            # year check on citation-like lines
            if y0 and y1 and (re.match(r"^\s*\d+[\.、)]\s*", line) or dois):
                ym = _YEAR_IN_PARENS_RE.search(line)
                if ym:
                    y = int(ym.group(1))
                    if y < y0 or y > y1:
                        d0 = next((d for d in dois if d in allowed_dois), dois[0])
                        py = papers.get(d0, {}).get("year")
                        if py is not None:
                            try:
                                if int(py) < y0 or int(py) > y1:
                                    removed += 1
                                    continue
                            except (TypeError, ValueError):
                                pass
                        else:
                            line = _YEAR_IN_PARENS_RE.sub(
                                f"（年份超出检索区间{y0}-{y1}，已标注）", line, count=1
                            )
        out.append(line)

    text = "\n".join(out)
    if removed:
        text = text.rstrip() + (
            f"\n\n> 注：已省略 {removed} 条证据中不存在的论文引用。"
        )
    return text


def constraint_notes_for_prompt(state: Dict[str, Any]) -> str:
    return build_evidence_ledger(state)
