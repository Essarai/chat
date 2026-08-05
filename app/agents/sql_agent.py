from __future__ import annotations

import re
from typing import Any, Dict, Optional

from app.agents.state import JournalState
from app.agents.understand import extract_author_name, extract_year_window
from app.config import get_settings
from app.services.sqlite_repo import SQLiteRepo

_KEYWORD_AUTHOR_RE = re.compile(
    r"(关键词|主题词).{0,12}(包含|含有|有)|"
    r"(包含|含有).{0,12}(关键词|主题词)|"
    r"作者.{0,16}(关键词|主题词)|(关键词|主题词).{0,16}作者",
    re.I,
)


def _pick_keyword(question: str, entities: Dict[str, Any]) -> Optional[str]:
    kws = entities.get("keywords") or []
    for k in kws:
        s = str(k).strip().strip("“”\"'‘’")
        if s and len(s) <= 20:
            return s
    # quoted term in question
    m = re.search(r"[“\"‘']([^”\"’']+)[”\"’']", question or "")
    if m:
        return m.group(1).strip()
    return None


def run_sql_agent(question: str, entities: Dict[str, Any] | None = None) -> Dict[str, Any]:
    settings = get_settings()
    db = SQLiteRepo(settings)
    entities = entities or {}
    author = entities.get("author_name") or extract_author_name(question)
    q = question or ""

    # Keyword → authors (structured), before journal-wide trend fallback
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

    # Author questions must NEVER mix in journal-wide yearly/keyword/fund totals.
    if author:
        profile = db.author_profile(author)
        if not profile.get("author"):
            return {
                "scope": "author",
                "author_name": author,
                "error": f"未找到作者: {author}",
                "source": "sqlite",
            }
        evidence: Dict[str, Any] = {
            **profile,
            "author_name": author,
            "start_year": profile.get("year_min"),
            "end_year": profile.get("year_max"),
        }
        # Collaborator institution distribution when question asks about orgs / overall profile
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


def sql_agent_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    if "sql" not in intents:
        return {}
    try:
        data = run_sql_agent(state.get("question") or "", state.get("entities"))
        return {"sql_evidence": data}
    except Exception as e:
        errors = list(state.get("errors") or [])
        errors.append(f"sql_agent: {e}")
        return {"sql_evidence": {"error": str(e)}, "errors": errors}
