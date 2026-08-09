from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

JOURNAL_NXB = "ZDXBNXB"
JOURNAL_RWB = "ZDXBRWB"
DEFAULT_JOURNAL_ID = JOURNAL_NXB

JOURNAL_TITLES: Dict[str, str] = {
    JOURNAL_NXB: "浙江大学学报（农业与生命科学版）",
    JOURNAL_RWB: "浙江大学学报（人文社会科学版）",
}


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    journal_id: str
    journal_title: str

    minimax_api_key: str
    minimax_base_url: str
    minimax_chat_model: str
    minimax_embed_model: str

    chroma_target: str  # "cloud" | "http"
    chroma_host: str
    chroma_port: int
    chroma_token: str
    chroma_collection: str
    chroma_cloud_api_key: str
    chroma_cloud_tenant: str
    chroma_cloud_database: str

    sqlite_path: str

    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str

    rag_top_k: int
    chat_max_history: int

    @property
    def minimax_embed_url(self) -> str:
        return f"{self.minimax_base_url.rstrip('/')}/embeddings"

    @property
    def minimax_chat_url(self) -> str:
        return f"{self.minimax_base_url.rstrip('/')}/chat/completions"

    @property
    def chroma_uses_cloud(self) -> bool:
        return self.chroma_target == "cloud"


_current_journal: ContextVar[str] = ContextVar("journal_id", default=DEFAULT_JOURNAL_ID)
_current_settings: ContextVar[Optional[Settings]] = ContextVar(
    "corpus_settings", default=None
)


def normalize_journal_id(journal_id: Optional[str]) -> str:
    jid = (journal_id or DEFAULT_JOURNAL_ID).strip().upper()
    if jid not in JOURNAL_TITLES:
        return DEFAULT_JOURNAL_ID
    return jid


def set_current_journal(journal_id: Optional[str]) -> str:
    """Bind journal id + full Settings into the current context."""
    return bind_corpus(journal_id).journal_id


def bind_corpus(journal_id: Optional[str] = None) -> Settings:
    """Force the request/worker context onto a physically isolated corpus."""
    jid = normalize_journal_id(journal_id)
    settings = get_corpus_settings(jid)
    _current_journal.set(jid)
    _current_settings.set(settings)
    return settings


def get_current_journal() -> str:
    return normalize_journal_id(_current_journal.get())


@lru_cache()
def _base_settings() -> Settings:
    default_db = str(ROOT / "data" / "journal.db")
    target = (_env("CHROMA_TARGET", "cloud") or "cloud").strip().lower()
    if target not in {"cloud", "http"}:
        target = "cloud"
    return Settings(
        journal_id=JOURNAL_NXB,
        journal_title=JOURNAL_TITLES[JOURNAL_NXB],
        minimax_api_key=_env("MINIMAX_API_KEY"),
        minimax_base_url=_env("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
        minimax_chat_model=_env("MINIMAX_CHAT_MODEL", "MiniMax-Text-01"),
        minimax_embed_model=_env("MINIMAX_EMBED_MODEL", "embo-01"),
        chroma_target=target,
        chroma_host=_env("CHROMA_HOST", "182.92.0.163"),
        chroma_port=_env_int("CHROMA_PORT", 8000),
        chroma_token=_env("CHROMA_TOKEN"),
        chroma_collection=_env("CHROMA_COLLECTION", "journal_papers"),
        chroma_cloud_api_key=_env("CHROMA_CLOUD_API_KEY"),
        chroma_cloud_tenant=_env("CHROMA_CLOUD_TENANT"),
        chroma_cloud_database=_env("CHROMA_CLOUD_DATABASE", "journals"),
        sqlite_path=_env("SQLITE_PATH", default_db),
        neo4j_uri=_env("NEO4J_URI", "bolt://182.92.0.163:7687"),
        neo4j_user=_env("NEO4J_USER", "neo4j"),
        neo4j_password=_env("NEO4J_PASSWORD"),
        rag_top_k=_env_int("RAG_TOP_K", 5),
        chat_max_history=_env_int("CHAT_MAX_HISTORY", 10),
    )


@lru_cache()
def get_corpus_settings(journal_id: str = DEFAULT_JOURNAL_ID) -> Settings:
    """Return settings for a physically isolated corpus."""
    jid = normalize_journal_id(journal_id)
    base = _base_settings()
    if jid == JOURNAL_NXB:
        return replace(
            base,
            journal_id=JOURNAL_NXB,
            journal_title=JOURNAL_TITLES[JOURNAL_NXB],
        )

    default_rwb_db = str(ROOT / "data" / "journal_rwb.db")
    return replace(
        base,
        journal_id=JOURNAL_RWB,
        journal_title=JOURNAL_TITLES[JOURNAL_RWB],
        chroma_collection=_env("CHROMA_COLLECTION_RWB", "journal_papers_rwb"),
        sqlite_path=_env("SQLITE_PATH_RWB", default_rwb_db),
        neo4j_uri=_env("NEO4J_URI_RWB", base.neo4j_uri),
        neo4j_user=_env("NEO4J_USER_RWB", base.neo4j_user),
        neo4j_password=_env("NEO4J_PASSWORD_RWB", base.neo4j_password),
    )


def get_settings() -> Settings:
    """Request-scoped settings: prefer bound Settings, else journal id lookup."""
    bound = _current_settings.get()
    if bound is not None and bound.journal_id == get_current_journal():
        return bound
    return get_corpus_settings(get_current_journal())


def list_journals() -> list[dict]:
    return [
        {"journal_id": jid, "title": title}
        for jid, title in JOURNAL_TITLES.items()
    ]
