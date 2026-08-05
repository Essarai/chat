from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    minimax_api_key: str
    minimax_base_url: str
    minimax_chat_model: str
    minimax_embed_model: str

    chroma_host: str
    chroma_port: int
    chroma_token: str
    chroma_collection: str

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


@lru_cache()
def get_settings() -> Settings:
    default_db = str(ROOT / "data" / "journal.db")
    return Settings(
        minimax_api_key=_env("MINIMAX_API_KEY"),
        minimax_base_url=_env("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
        minimax_chat_model=_env("MINIMAX_CHAT_MODEL", "MiniMax-Text-01"),
        minimax_embed_model=_env("MINIMAX_EMBED_MODEL", "embo-01"),
        chroma_host=_env("CHROMA_HOST", "182.92.0.163"),
        chroma_port=_env_int("CHROMA_PORT", 8000),
        chroma_token=_env("CHROMA_TOKEN"),
        chroma_collection=_env("CHROMA_COLLECTION", "journal_papers"),
        sqlite_path=_env("SQLITE_PATH", default_db),
        neo4j_uri=_env("NEO4J_URI", "bolt://182.92.0.163:7687"),
        neo4j_user=_env("NEO4J_USER", "neo4j"),
        neo4j_password=_env("NEO4J_PASSWORD"),
        rag_top_k=_env_int("RAG_TOP_K", 5),
        chat_max_history=_env_int("CHAT_MAX_HISTORY", 10),
    )
