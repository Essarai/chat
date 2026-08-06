"""Standardized business capability interfaces (SQL / KG / RAG)."""

from app.capabilities import kg_capability, rag_capability, sql_capability
from app.capabilities.schemas import CapabilityResult, err_result, ok_result

__all__ = [
    "sql_capability",
    "kg_capability",
    "rag_capability",
    "CapabilityResult",
    "ok_result",
    "err_result",
]
