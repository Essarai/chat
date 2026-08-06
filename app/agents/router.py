"""Deprecated: routing is folded into Query Planner Agent."""

from __future__ import annotations

from typing import Any, Dict

from app.agents.state import JournalState


def router_node(state: JournalState) -> Dict[str, Any]:
    """No-op shim — planner now owns intent/source selection."""
    return {}
