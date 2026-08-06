"""Backward-compatible re-export — use specialists.rag_agent."""

from app.agents.specialists.rag_agent import rag_agent_node, run_rag_agent
from app.capabilities.rag_capability import citation_from_hit

__all__ = ["run_rag_agent", "rag_agent_node", "citation_from_hit"]
