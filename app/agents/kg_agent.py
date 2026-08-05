from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.agents.state import JournalState
from app.agents.understand import extract_author_name
from app.config import get_settings
from app.services.neo4j_repo import Neo4jRepo
from app.services.sqlite_repo import SQLiteRepo
from app.utils import doi_url


def run_kg_agent(
    question: str,
    entities: Dict[str, Any] | None = None,
    last_dois: Optional[List[str]] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    neo4j = Neo4jRepo(settings)
    db = SQLiteRepo(settings)
    entities = entities or {}
    name = entities.get("author_name") or extract_author_name(question)

    if name:
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

    if last_dois:
        doi = last_dois[0]
        try:
            paper = neo4j.paper_neighborhood(doi)
            return {"paper": paper, "source": "neo4j"}
        except Exception as e:
            paper = db.get_paper(doi) or {}
            if paper.get("doi"):
                paper["url"] = doi_url(paper.get("doi"))
            return {"paper": paper, "source": "sqlite", "neo4j_error": str(e)}

    kw = ""
    kws = entities.get("keywords") or []
    if kws:
        kw = kws[0]
    else:
        kw = re.sub(r"(相关|研究|论文|文献|有哪些|什么|？|\?)", "", question).strip()
    if kw:
        try:
            return {
                "keyword_papers": neo4j.keyword_related_papers(kw),
                "source": "neo4j",
            }
        except Exception as e:
            return {"keyword_papers": [], "source": "sqlite", "neo4j_error": str(e)}
    return {}


def kg_agent_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    if "kg" not in intents:
        return {}
    try:
        last_dois = (state.get("entities") or {}).get("dois") or []
        data = run_kg_agent(state.get("question") or "", state.get("entities"), last_dois)
        return {"kg_evidence": data}
    except Exception as e:
        errors = list(state.get("errors") or [])
        errors.append(f"kg_agent: {e}")
        return {"kg_evidence": {"error": str(e)}, "errors": errors}
