from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.capabilities.schemas import CapabilityResult, err_result, ok_result
from app.config import bind_corpus, get_corpus_settings, get_settings
from app.services.chroma_store import ChromaStore
from app.utils import doi_url

_CHROMA_CACHE: Dict[str, ChromaStore] = {}

# 办刊通告 / 评奖 / 引证报告等非研究文献，易污染「趋势/方向」类语义检索
_EDITORIAL_TITLE_RE = re.compile(
    r"(本刊.*(奖|蝉联|入围|位居|排名)|"
    r"中国科技论文在线优秀期刊|"
    r"中国科技期刊引证报告|"
    r"世界学术期刊学术影响力|"
    r"WAJCI|影响因子|"
    r"在线优先出版|"
    r"编委会|征稿启事|更正声明|"
    r"优秀期刊.*一等奖)",
    re.I,
)


def _store(journal_id: Optional[str] = None) -> ChromaStore:
    settings = bind_corpus(journal_id) if journal_id else get_settings()
    jid = settings.journal_id
    store = _CHROMA_CACHE.get(jid)
    if store is None:
        store = ChromaStore(get_corpus_settings(jid))
        _CHROMA_CACHE[jid] = store
    return store


def citation_from_hit(h: Dict[str, Any]) -> Dict[str, Any]:
    doi = h.get("doi")
    return {
        "doi": doi,
        "url": h.get("url") or doi_url(doi),
        "title": h.get("title"),
        "year": h.get("year"),
        "distance": h.get("distance"),
    }


def _as_year(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _year_where(
    year_start: Optional[int], year_end: Optional[int]
) -> Optional[Dict[str, Any]]:
    y0, y1 = _as_year(year_start), _as_year(year_end)
    if y0 is None and y1 is None:
        return None
    clauses: List[Dict[str, Any]] = []
    if y0 is not None:
        clauses.append({"year": {"$gte": y0}})
    if y1 is not None:
        clauses.append({"year": {"$lte": y1}})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _in_year_window(
    year: Any, year_start: Optional[int], year_end: Optional[int]
) -> bool:
    y0, y1 = _as_year(year_start), _as_year(year_end)
    if y0 is None and y1 is None:
        return True
    y = _as_year(year)
    if y is None:
        # Unknown year: drop when a window is required.
        return False
    if y0 is not None and y < y0:
        return False
    if y1 is not None and y > y1:
        return False
    return True


def _filter_hits_by_year(
    hits: List[Dict[str, Any]],
    year_start: Optional[int],
    year_end: Optional[int],
) -> Tuple[List[Dict[str, Any]], int]:
    if year_start is None and year_end is None:
        return hits, 0
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for h in hits or []:
        if _in_year_window(h.get("year"), year_start, year_end):
            kept.append(h)
        else:
            dropped += 1
    return kept, dropped


def _is_editorial_noise(hit: Dict[str, Any]) -> bool:
    title = str(hit.get("title") or hit.get("title_zh") or "")
    text = str(hit.get("document") or hit.get("text") or hit.get("snippet") or "")
    return bool(_EDITORIAL_TITLE_RE.search(title) or _EDITORIAL_TITLE_RE.search(text[:400]))


def _filter_editorial_hits(
    hits: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for h in hits or []:
        if _is_editorial_noise(h):
            dropped += 1
            continue
        kept.append(h)
    return kept, dropped


def _merge_hits(hit_lists: List[List[Dict[str, Any]]], top_k: int) -> List[Dict[str, Any]]:
    seen = set()
    merged: List[Dict[str, Any]] = []
    for hits in hit_lists:
        for h in hits or []:
            doi = h.get("doi") or h.get("title")
            if not doi or doi in seen:
                continue
            seen.add(doi)
            merged.append(h)
            if len(merged) >= top_k:
                return merged
    return merged


def semantic_search(
    question: str = "",
    queries: Optional[List[str]] = None,
    top_k: Optional[int] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    journal_id: Optional[str] = None,
) -> Dict[str, Any]:
    settings = bind_corpus(journal_id) if journal_id else get_settings()
    store = _store(settings.journal_id)
    k = top_k or settings.rag_top_k
    qlist = [q.strip() for q in (queries or []) if q and str(q).strip()]
    if not qlist:
        qlist = [question] if question else []
    if not qlist:
        return {
            "hits": [],
            "citations": [],
            "queries": [],
            "source": "chroma",
            "year_start": year_start,
            "year_end": year_end,
        }

    where = _year_where(year_start, year_end)
    # Over-fetch when windowing so post-filter still fills top_k.
    fetch_k = max(k * 3, k + 8) if where else k
    per_query = max(3, (fetch_k + len(qlist) - 1) // len(qlist))
    lists: List[List[Dict[str, Any]]] = []
    where_error = None
    for q in qlist[:8]:
        try:
            lists.append(store.search(q, top_k=per_query, where=where))
        except Exception as e:
            where_error = str(e)
            try:
                lists.append(store.search(q, top_k=per_query, where=None))
            except Exception:
                lists.append([])
    hits = _merge_hits(lists, fetch_k)
    if not hits and question and question not in qlist:
        try:
            hits = store.search(question, top_k=fetch_k, where=where)
        except Exception as e:
            where_error = where_error or str(e)
            hits = store.search(question, top_k=fetch_k, where=None)

    hits, dropped = _filter_hits_by_year(hits, year_start, year_end)
    before_editorial = list(hits)
    hits, editorial_dropped = _filter_editorial_hits(hits)
    # If over-filtered to empty, keep year-filtered set (rare edge).
    if not hits and editorial_dropped:
        hits = before_editorial
        editorial_dropped = 0
    hits = hits[:k]
    return {
        "hits": hits,
        "citations": [citation_from_hit(h) for h in hits],
        "queries": qlist,
        "source": "chroma",
        "year_start": year_start,
        "year_end": year_end,
        "year_filtered": bool(where),
        "dropped_out_of_window": dropped,
        "dropped_editorial": editorial_dropped,
        "where_error": where_error,
    }


def invoke(
    operation: str,
    *,
    question: str = "",
    queries: Optional[List[str]] = None,
    top_k: Optional[int] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    journal_id: Optional[str] = None,
    **params: Any,
) -> CapabilityResult:
    try:
        if operation in {"semantic_search", "execute_plan", "search"}:
            data = semantic_search(
                question,
                queries=queries,
                top_k=top_k,
                year_start=year_start
                if year_start is not None
                else params.get("year_start"),
                year_end=year_end if year_end is not None else params.get("year_end"),
                journal_id=journal_id or params.get("journal_id"),
            )
            return ok_result(
                "rag",
                "semantic_search",
                data,
                citations=data.get("citations") or [],
            )
        return err_result("rag", operation, f"unknown rag operation: {operation}")
    except Exception as e:
        return err_result("rag", operation, str(e))
