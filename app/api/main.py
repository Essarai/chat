from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import get_settings

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(
    title="AI 期刊知识助手",
    description="智能检索 / 趋势分析 / 知识图谱查询 / RAG 问答",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache()
def get_bot():
    # Lazy import: chromadb/langgraph are heavy and would block Railway healthchecks.
    from app.services.orchestrator import ChatOrchestrator

    return ChatOrchestrator(get_settings())


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = Field(default=None, ge=1, le=20)


class AskRequest(BaseModel):
    question: str
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    reset: bool = False


@app.get("/health")
def health() -> Dict[str, Any]:
    """Liveness probe for Railway — must not call Chroma/Neo4j (may be unreachable)."""
    settings = get_settings()
    return {
        "ok": True,
        "chat_model": settings.minimax_chat_model,
        "embed_model": settings.minimax_embed_model,
        "collection": settings.chroma_collection,
        "sqlite_path": settings.sqlite_path,
    }


@app.get("/health/ready")
def health_ready() -> Dict[str, Any]:
    """Optional readiness check against remote dependencies."""
    settings = get_settings()
    status: Dict[str, Any] = {
        "ok": True,
        "chat_model": settings.minimax_chat_model,
        "collection": settings.chroma_collection,
    }
    try:
        status["chroma_count"] = get_bot().chroma.count()
    except Exception as e:
        status["ok"] = False
        status["chroma_error"] = str(e)
    return status


@app.post("/search")
def search(req: SearchRequest) -> Dict[str, Any]:
    try:
        hits = get_bot().search(req.query, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"query": req.query, "hits": hits}


@app.post("/ask")
def ask(req: AskRequest) -> Dict[str, Any]:
    bot = get_bot()
    if req.reset:
        bot.reset()
    try:
        result = bot.ask(req.question, top_k=req.top_k)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "question": req.question,
        "intent": result.intent,
        "intents": result.intents,
        "route_reason": result.route_reason,
        "answer": result.answer,
        "citations": result.citations,
        "evidence": result.evidence,
    }


@app.post("/ask/stream")
def ask_stream(req: AskRequest) -> StreamingResponse:
    bot = get_bot()
    if req.reset:
        bot.reset()

    def event_gen():
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
) -> Dict[str, Any]:
    try:
        rows = get_bot().db.yearly_counts(start_year, end_year)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"yearly": rows}


@app.get("/trends/keywords")
def trends_keywords(
    limit: int = Query(default=20, ge=1, le=100),
    start_year: Optional[int] = Query(default=None),
    end_year: Optional[int] = Query(default=None),
) -> Dict[str, Any]:
    try:
        rows = get_bot().db.top_keywords(limit, start_year, end_year)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"keywords": rows}


@app.get("/graph/paper/{doi:path}")
def graph_paper(doi: str) -> Dict[str, Any]:
    try:
        data = get_bot().neo4j.paper_neighborhood(doi)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data:
        raise HTTPException(status_code=404, detail="paper not found")
    return data


@app.get("/graph/author/{author_id}")
def graph_author(
    author_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    try:
        data = get_bot().neo4j.author_collaborators(author_id=author_id, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("author"):
        raise HTTPException(status_code=404, detail="author not found")
    return data


@app.get("/graph/author-by-name")
def graph_author_by_name(
    name: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    try:
        data = get_bot().neo4j.author_collaborators(name=name, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("author"):
        raise HTTPException(status_code=404, detail="author not found")
    return data


@app.get("/graph/network/author")
def graph_network_author(
    name: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    try:
        data = get_bot().neo4j.author_network(name=name, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("author"):
        raise HTTPException(status_code=404, detail="author not found")
    return data


@app.get("/graph/network/paper")
def graph_network_paper(doi: str = Query(...)) -> Dict[str, Any]:
    try:
        data = get_bot().neo4j.paper_network(doi=doi)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("paper"):
        raise HTTPException(status_code=404, detail="paper not found")
    return data


@app.get("/graph/network/keyword")
def graph_network_keyword(
    keyword: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    try:
        data = get_bot().neo4j.keyword_network(keyword=keyword, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not data.get("keyword"):
        raise HTTPException(status_code=404, detail="keyword not found")
    return data


@app.get("/graph/network/institution")
def graph_network_institution(
    name: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    try:
        data = get_bot().neo4j.institution_network(name=name, limit=limit)
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
    return FileResponse(index_path)


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
