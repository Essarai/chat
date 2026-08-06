from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.agents.understand import extract_author_name
from app.capabilities.schemas import CapabilityResult, err_result, ok_result
from app.config import get_settings
from app.services.neo4j_repo import Neo4jRepo
from app.services.sqlite_repo import SQLiteRepo
from app.utils import doi_url


def _neo4j() -> Neo4jRepo:
    return Neo4jRepo(get_settings())


def _db() -> SQLiteRepo:
    return SQLiteRepo(get_settings())


def _normalize_keyword_network(data: Dict[str, Any], keyword: str) -> Dict[str, Any]:
    """Flatten Neo4j graph payload into analyst-friendly neighborhood lists."""
    out = dict(data or {})
    out["query_keyword"] = keyword
    out["source"] = out.get("source") or "neo4j"
    authors: List[Dict[str, Any]] = []
    institutions: List[Dict[str, Any]] = []
    related: List[Dict[str, Any]] = []
    papers: List[Dict[str, Any]] = []
    for n in out.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        group = n.get("group") or n.get("type") or ""
        label = n.get("label") or ""
        value = n.get("value")
        nid = str(n.get("id") or "")
        if group == "author":
            aid = nid.split("author:", 1)[-1] if nid.startswith("author:") else nid
            authors.append(
                {
                    "author_id": aid,
                    "name_zh": label,
                    "paper_count": value,
                }
            )
        elif group == "institution":
            institutions.append(
                {
                    "institution_id": nid,
                    "name": label,
                    "institution": label,
                    "paper_count": value,
                }
            )
        elif group == "keyword" and label and label != keyword:
            related.append({"label": label, "keyword": label, "paper_count": value})
        elif group == "paper":
            doi = nid.split("paper:", 1)[-1] if nid.startswith("paper:") else nid
            papers.append(
                {
                    "doi": doi,
                    "title": n.get("title") or label,
                    "title_zh": n.get("title") or label,
                }
            )
    authors.sort(key=lambda x: int(x.get("paper_count") or 0), reverse=True)
    institutions.sort(key=lambda x: int(x.get("paper_count") or 0), reverse=True)
    related.sort(key=lambda x: int(x.get("paper_count") or 0), reverse=True)
    out["authors"] = authors
    out["neighborhood_authors"] = authors
    out["institutions"] = institutions
    out["neighborhood_institutions"] = institutions
    out["related_keywords"] = related
    out["papers"] = papers
    out["neighborhood"] = {
        "keyword": keyword,
        "author_count": len(authors),
        "institution_count": len(institutions),
        "related_keyword_count": len(related),
        "paper_count": len(papers),
        "edge_count": len(out.get("edges") or []),
    }
    return out


def keyword_ego(keyword: str) -> Dict[str, Any]:
    neo4j = _neo4j()
    db = _db()
    neo_err = None
    try:
        if hasattr(neo4j, "keyword_network"):
            data = neo4j.keyword_network(keyword, limit=20)
            if data.get("nodes") or data.get("keyword"):
                return _normalize_keyword_network(data, keyword)
    except Exception as e:
        neo_err = str(e)

    authors = db.authors_by_keyword(keyword, author_limit=12, papers_per_author=3)
    inst = db.institutions_by_keyword(keyword, limit=12)
    return {
        "query_keyword": keyword,
        "source": "sqlite",
        "neo4j_error": neo_err,
        "authors": authors.get("authors") or [],
        "neighborhood_authors": authors.get("authors") or [],
        "institutions": inst.get("institutions") or [],
        "neighborhood_institutions": inst.get("institutions") or [],
        "related_keywords": [],
        "papers": [],
        "total_papers": authors.get("total_papers"),
        "keyword": {"label": keyword},
        "neighborhood": {
            "keyword": keyword,
            "author_count": len(authors.get("authors") or []),
            "institution_count": len(inst.get("institutions") or []),
            "fallback": "sqlite",
        },
    }


def author_ego(name: str) -> Dict[str, Any]:
    neo4j = _neo4j()
    db = _db()
    try:
        data = neo4j.author_collaborators(name=name)
        data["query_name"] = name
        data["source"] = data.get("source") or "neo4j"
        if data.get("author"):
            return data
    except Exception as e:
        data = db.author_collaborators(name)
        data["query_name"] = name
        data["neo4j_error"] = str(e)
        return data
    data = db.author_collaborators(name)
    data["query_name"] = name
    return data


def paper_neighborhood(doi: str) -> Dict[str, Any]:
    neo4j = _neo4j()
    db = _db()
    try:
        paper = neo4j.paper_neighborhood(doi)
        return {"paper": paper, "source": "neo4j"}
    except Exception as e:
        paper = db.get_paper(doi) or {}
        if paper.get("doi"):
            paper["url"] = doi_url(paper.get("doi"))
        return {"paper": paper, "source": "sqlite", "neo4j_error": str(e)}


def execute_plan(
    plan: Dict[str, Any],
    question: str = "",
    entities: Dict[str, Any] | None = None,
    last_dois: Optional[List[str]] = None,
) -> Dict[str, Any]:
    entities = entities or {}
    ops = list(plan.get("kg_ops") or [])
    kws = [
        str(k).strip()
        for k in (plan.get("keywords") or entities.get("keywords") or [])
        if str(k).strip()
    ]
    name = (
        plan.get("author_name")
        or entities.get("author_name")
        or extract_author_name(question)
    )

    # Prefer explicit ops; do not let task=keyword_collab override author_ego.
    if "author_ego" in ops and name:
        return author_ego(name)

    if "keyword_ego" in ops or (
        plan.get("task") == "keyword_collab" and kws and "author_ego" not in ops
    ):
        kw = kws[0] if kws else ""
        if not kw:
            kw = re.sub(r"(相关|研究|论文|文献|有哪些|什么|？|\?)", "", question).strip()
        if kw:
            return keyword_ego(kw)

    if "author_ego" in ops or name:
        if name:
            return author_ego(name)

    if last_dois:
        return paper_neighborhood(last_dois[0])

    kw = kws[0] if kws else ""
    if not kw:
        kw = re.sub(r"(相关|研究|论文|文献|有哪些|什么|？|\?)", "", question).strip()
    if kw:
        try:
            return {
                "keyword_papers": _neo4j().keyword_related_papers(kw),
                "source": "neo4j",
                "query_keyword": kw,
            }
        except Exception as e:
            return {
                "keyword_papers": [],
                "source": "sqlite",
                "neo4j_error": str(e),
                "query_keyword": kw,
            }
    return {}


def invoke(
    operation: str,
    *,
    question: str = "",
    entities: Dict[str, Any] | None = None,
    plan: Dict[str, Any] | None = None,
    last_dois: Optional[List[str]] = None,
    **params: Any,
) -> CapabilityResult:
    try:
        if operation == "execute_plan":
            return ok_result(
                "kg",
                operation,
                execute_plan(plan or {}, question, entities, last_dois),
            )
        if operation == "keyword_ego":
            return ok_result("kg", operation, keyword_ego(params.get("keyword") or ""))
        if operation == "author_ego":
            return ok_result("kg", operation, author_ego(params.get("name") or ""))
        if operation == "paper_neighborhood":
            return ok_result(
                "kg", operation, paper_neighborhood(params.get("doi") or "")
            )
        return err_result("kg", operation, f"unknown kg operation: {operation}")
    except Exception as e:
        return err_result("kg", operation, str(e))
