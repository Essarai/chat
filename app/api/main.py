from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import (
    DEFAULT_JOURNAL_ID,
    bind_corpus,
    get_corpus_settings,
    get_settings,
    list_journals,
    set_current_journal,
)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(
    title="AI 期刊知识助手",
    description="智能检索 / 趋势分析 / 知识图谱查询 / RAG 问答",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_bots: Dict[str, Any] = {}


def get_bot(journal_id: Optional[str] = None):
    """Per-corpus orchestrator; also sets request-scoped journal context."""
    # Lazy import: chromadb/langgraph are heavy and would block Railway healthchecks.
    from app.services.orchestrator import ChatOrchestrator

    settings = bind_corpus(journal_id)
    jid = settings.journal_id
    bot = _bots.get(jid)
    if bot is None:
        bot = ChatOrchestrator(settings)
        _bots[jid] = bot
    else:
        # Ensure this request context is bound even when bot is cached
        bind_corpus(jid)
    return bot


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    journal_id: str = DEFAULT_JOURNAL_ID


class AskRequest(BaseModel):
    question: str
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    reset: bool = False
    journal_id: str = DEFAULT_JOURNAL_ID


@app.get("/health")
def health() -> Dict[str, Any]:
    """Liveness probe for Railway — must not call Chroma/Neo4j (may be unreachable)."""
    settings = get_corpus_settings(DEFAULT_JOURNAL_ID)
    vis_js = WEB_DIR / "vendor" / "vis-network.min.js"
    vis_css = WEB_DIR / "vendor" / "vis-network.min.css"
    return {
        "ok": True,
        "chat_model": settings.minimax_chat_model,
        "embed_model": settings.minimax_embed_model,
        "chroma_target": settings.chroma_target,
        "collection": settings.chroma_collection,
        "sqlite_path": settings.sqlite_path,
        "journals": list_journals(),
        "frontend": {
            "asset_version": "20260808a",
            "vis_network_js": vis_js.exists(),
            "vis_network_css": vis_css.exists(),
            "vis_network_js_bytes": vis_js.stat().st_size if vis_js.exists() else 0,
        },
    }


@app.get("/journals")
def journals() -> Dict[str, Any]:
    return {"journals": list_journals(), "default": DEFAULT_JOURNAL_ID}


@app.get("/health/ready")
def health_ready(
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    """Optional readiness check against remote dependencies."""
    jid = set_current_journal(journal_id)
    settings = get_settings()
    status: Dict[str, Any] = {
        "ok": True,
        "journal_id": jid,
        "chat_model": settings.minimax_chat_model,
        "chroma_target": settings.chroma_target,
        "collection": settings.chroma_collection,
        "sqlite_path": settings.sqlite_path,
    }
    try:
        from app.services.chroma_store import ChromaStore

        status["chroma_count"] = ChromaStore(settings).count()
    except Exception as e:
        status["ok"] = False
        status["chroma_error"] = str(e)
    return status


@app.post("/search")
def search(req: SearchRequest) -> Dict[str, Any]:
    try:
        hits = get_bot(req.journal_id).search(req.query, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"query": req.query, "journal_id": set_current_journal(req.journal_id), "hits": hits}


@app.post("/ask")
def ask(req: AskRequest) -> Dict[str, Any]:
    bot = get_bot(req.journal_id)
    if req.reset:
        bot.reset()
    try:
        result = bot.ask(req.question, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "question": req.question,
        "journal_id": set_current_journal(req.journal_id),
        "intent": result.intent,
        "intents": result.intents,
        "route_reason": result.route_reason,
        "answer": result.answer,
        "citations": result.citations,
        "evidence": result.evidence,
    }


@app.post("/ask/stream")
def ask_stream(req: AskRequest) -> StreamingResponse:
    bot = get_bot(req.journal_id)
    if req.reset:
        bot.reset()

    def event_gen():
        # Re-bind corpus for this generator thread/context (StreamingResponse
        # may run outside the request thread that called get_bot).
        bind_corpus(req.journal_id)
        try:
            for ev in bot.ask_stream(req.question, top_k=req.top_k):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/trends/yearly")
def trends_yearly(
    start_year: Optional[int] = Query(default=None),
    end_year: Optional[int] = Query(default=None),
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        rows = get_bot(journal_id).db.yearly_counts(start_year, end_year)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"yearly": rows, "journal_id": set_current_journal(journal_id)}


@app.get("/trends/keywords")
def trends_keywords(
    limit: int = Query(default=20, ge=1, le=100),
    start_year: Optional[int] = Query(default=None),
    end_year: Optional[int] = Query(default=None),
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        rows = get_bot(journal_id).db.top_keywords(limit, start_year, end_year)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"keywords": rows, "journal_id": set_current_journal(journal_id)}


@app.get("/graph/paper/{doi:path}")
def graph_paper(
    doi: str,
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        data = get_bot(journal_id).neo4j.paper_neighborhood(doi)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data:
        raise HTTPException(status_code=404, detail="paper not found")
    return data


@app.get("/graph/author/{author_id}")
def graph_author(
    author_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        data = get_bot(journal_id).neo4j.author_collaborators(
            author_id=author_id, limit=limit
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("author"):
        raise HTTPException(status_code=404, detail="author not found")
    return data


@app.get("/graph/author-by-name")
def graph_author_by_name(
    name: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        data = get_bot(journal_id).neo4j.author_collaborators(name=name, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("author"):
        raise HTTPException(status_code=404, detail="author not found")
    return data


@app.get("/graph/network/author")
def graph_network_author(
    name: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        data = get_bot(journal_id).neo4j.author_network(name=name, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("author"):
        raise HTTPException(status_code=404, detail="author not found")
    return data


@app.get("/graph/network/paper")
def graph_network_paper(
    doi: str = Query(...),
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        data = get_bot(journal_id).neo4j.paper_network(doi=doi)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("paper"):
        raise HTTPException(status_code=404, detail="paper not found")
    return data


@app.get("/graph/network/keyword")
def graph_network_keyword(
    keyword: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        data = get_bot(journal_id).neo4j.keyword_network(keyword=keyword, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("keyword"):
        raise HTTPException(status_code=404, detail="keyword not found")
    return data


@app.get("/graph/network/institution")
def graph_network_institution(
    name: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    journal_id: str = Query(default=DEFAULT_JOURNAL_ID),
) -> Dict[str, Any]:
    try:
        data = get_bot(journal_id).neo4j.institution_network(name=name, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("institution"):
        raise HTTPException(status_code=404, detail="institution not found")
    return data


@app.get("/")
def index() -> FileResponse:
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="frontend not found")
    return FileResponse(
        index_path,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
