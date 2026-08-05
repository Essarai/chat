from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agents.state import JournalState
from app.config import get_settings
from app.services.chroma_store import ChromaStore
from app.utils import doi_url


def citation_from_hit(h: Dict[str, Any]) -> Dict[str, Any]:
    doi = h.get("doi")
    return {
        "doi": doi,
        "url": h.get("url") or doi_url(doi),
        "title": h.get("title"),
        "year": h.get("year"),
        "distance": h.get("distance"),
    }


def run_rag_agent(question: str, top_k: Optional[int] = None) -> Dict[str, Any]:
    settings = get_settings()
    store = ChromaStore(settings)
    hits = store.search(question, top_k=top_k or settings.rag_top_k)
    return {
        "hits": hits,
        "citations": [citation_from_hit(h) for h in hits],
        "source": "chroma",
    }


def rag_agent_node(state: JournalState) -> Dict[str, Any]:
    intents = state.get("intents") or []
    if "rag" not in intents:
        return {}
    try:
        data = run_rag_agent(state.get("question") or "", state.get("top_k"))
        # keep dois for follow-ups
        entities = dict(state.get("entities") or {})
        entities["dois"] = [h.get("doi") for h in data.get("hits") or [] if h.get("doi")]
        return {"rag_evidence": data, "entities": entities}
    except Exception as e:
        errors = list(state.get("errors") or [])
        errors.append(f"rag_agent: {e}")
        return {"rag_evidence": {"error": str(e), "hits": [], "citations": []}, "errors": errors}
