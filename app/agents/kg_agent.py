"""Backward-compatible re-export — use specialists.kg_agent."""

from app.agents.specialists.kg_agent import kg_agent_node, run_kg_agent

__all__ = ["run_kg_agent", "kg_agent_node"]
