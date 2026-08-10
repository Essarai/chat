from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import (
    DEFAULT_JOURNAL_ID,
    JOURNAL_TITLES,
    bind_corpus,
    get_corpus_settings,
    get_settings,
    list_journals,
    set_current_journal,
)
from app.services.conversation_store import build_turn_record, conversation_store

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(
    title="AI 期刊知识助手",
    description="智能检索 / 趋势分析 / 知识图谱查询 / RAG 问答",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")
        if origin.strip()
    ],
    allow_credentials=os.getenv("ALLOWED_ORIGINS", "*").strip() != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("journal_agent")
_orchestrators: Dict[str, Any] = {}
_rate_events: Dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()
_access_token = os.getenv("API_ACCESS_TOKEN", "").strip()
_requests_per_minute = max(0, int(os.getenv("REQUESTS_PER_MINUTE", "0") or 0))


@app.middleware("http")
async def optional_access_controls(request: Request, call_next):
    """Opt-in public-deployment controls; local defaults remain unrestricted."""
    if request.url.path not in {"/", "/health"} and not request.url.path.startswith("/static/"):
        if _access_token:
            supplied = request.headers.get("authorization", "")
            if supplied != f"Bearer {_access_token}":
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if _requests_per_minute:
            key = request.client.host if request.client else "unknown"
            now = time.monotonic()
            with _rate_lock:
                events = _rate_events[key]
                while events and now - events[0] >= 60:
                    events.popleft()
                if len(events) >= _requests_per_minute:
                    return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
                events.append(now)
    response = await call_next(request)
    if request.url.path in {"/static/app.js", "/static/index.html"}:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _validated_journal_id(journal_id: Optional[str]) -> str:
    jid = (journal_id or DEFAULT_JOURNAL_ID).strip().upper()
    if jid not in JOURNAL_TITLES:
        raise HTTPException(status_code=400, detail=f"unsupported journal_id: {journal_id}")
    return jid


def get_bot(journal_id: Optional[str] = None):
    """Cached per-corpus resources; conversation state lives elsewhere."""
    # Lazy import: chromadb/langgraph are heavy and would block Railway healthchecks.
    from app.services.orchestrator import ChatOrchestrator

    settings = bind_corpus(_validated_journal_id(journal_id))
    jid = settings.journal_id
    bot = _orchestrators.get(jid)
    if bot is None:
        bot = ChatOrchestrator(settings)
        _orchestrators[jid] = bot
    else:
        # Ensure this request context is bound even when bot is cached
        bind_corpus(jid)
    return bot


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    journal_id: str = DEFAULT_JOURNAL_ID


class HistoryItem(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    reset: bool = False
    journal_id: str = DEFAULT_JOURNAL_ID
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    # Client-owned prior turns (user/assistant). When set, replaces server history.
    history: Optional[List[HistoryItem]] = None


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
            "asset_version": "20260810-history-1",
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
    jid = _validated_journal_id(journal_id)
    set_current_journal(jid)
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


def _conversation_inputs(req: AskRequest) -> Dict[str, Any]:
    jid = _validated_journal_id(req.journal_id)
    cid = (req.conversation_id or conversation_store.new_id()).strip()
    state = conversation_store.reset(cid, jid) if req.reset else conversation_store.get(cid, jid)
    client_history = [
        {"role": item.role, "content": item.content}
        for item in (req.history or [])
        if item.role in {"user", "assistant"} and item.content.strip()
    ]
    stored_history = state.history()
    # An edit/resend changes the visible branch. Drop structured references
    # instead of attaching them to a different textual history.
    if stored_history and client_history and stored_history != client_history:
        state = conversation_store.reconcile_history(cid, jid, client_history)
        stored_history = state.history()
    history = stored_history or client_history
    previous_turn = state.turns[-1].to_dict() if state.turns else None
    last_dois = []
    if previous_turn:
        last_dois = [
            item.get("doi")
            for item in (previous_turn.get("result_set") or {}).get("items") or []
            if item.get("doi")
        ]
    return {
        "journal_id": jid,
        "conversation_id": cid,
        "history": history,
        "previous_turn": previous_turn,
        "last_dois": last_dois,
    }


@app.post("/ask")
def ask(req: AskRequest) -> Dict[str, Any]:
    context = _conversation_inputs(req)
    bot = get_bot(context["journal_id"])
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    try:
        result = bot.ask(
            req.question,
            top_k=req.top_k,
            history=context["history"],
            last_dois=context["last_dois"],
            previous_turn=context["previous_turn"],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    turn = build_turn_record(req.question, result)
    conversation_store.append(context["conversation_id"], context["journal_id"], turn)
    logger.info(
        "ask request_id=%s conversation_id=%s journal_id=%s intent=%s plan=%s elapsed_ms=%d truncated=%s",
        request_id,
        context["conversation_id"],
        context["journal_id"],
        json.dumps(result.evidence.get("turn_intent") or {}, ensure_ascii=False),
        json.dumps(result.evidence.get("query_plan") or {}, ensure_ascii=False),
        int((time.monotonic() - started) * 1000),
        result.truncated,
    )
    return {
        "request_id": request_id,
        "conversation_id": context["conversation_id"],
        "turn_id": turn.turn_id,
        "question": req.question,
        "journal_id": context["journal_id"],
        "intent": result.intent,
        "intents": result.intents,
        "route_reason": result.route_reason,
        "answer": result.answer,
        "citations": result.citations,
        "evidence": result.evidence,
        "truncated": result.truncated,
        "shown_count": result.shown_count,
        "total_count": result.total_count,
        "has_more": result.has_more,
    }


@app.post("/ask/stream")
def ask_stream(req: AskRequest) -> StreamingResponse:
    context = _conversation_inputs(req)
    bot = get_bot(context["journal_id"])
    request_id = str(uuid.uuid4())
    started = time.monotonic()

    def event_gen():
        # Re-bind corpus for this generator thread/context (StreamingResponse
        # may run outside the request thread that called get_bot).
        bind_corpus(context["journal_id"])
        try:
            for ev in bot.ask_stream(
                req.question,
                top_k=req.top_k,
                history=context["history"],
                last_dois=context["last_dois"],
                previous_turn=context["previous_turn"],
            ):
                if ev.get("type") == "done":
                    result_like = SimpleNamespace(
                        answer=ev.get("answer") or "",
                        citations=ev.get("citations") or [],
                        evidence=ev.get("evidence") or {},
                    )
                    turn = build_turn_record(req.question, result_like)
                    conversation_store.append(
                        context["conversation_id"], context["journal_id"], turn
                    )
                    ev.update(
                        {
                            "request_id": request_id,
                            "conversation_id": context["conversation_id"],
                            "turn_id": turn.turn_id,
                        }
                    )
                    logger.info(
                        "ask_stream request_id=%s conversation_id=%s journal_id=%s intent=%s plan=%s elapsed_ms=%d truncated=%s",
                        request_id,
                        context["conversation_id"],
                        context["journal_id"],
                        json.dumps(turn.intent or {}, ensure_ascii=False),
                        json.dumps(turn.query_plan or {}, ensure_ascii=False),
                        int((time.monotonic() - started) * 1000),
                        bool(ev.get("truncated")),
                    )
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
