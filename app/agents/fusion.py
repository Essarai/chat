"""Result fusion: evidence merge + comprehensive answer generation."""

from __future__ import annotations

from typing import Any, Dict

from app.agents.generate import finalize_answer, generate_node, stream_generate
from app.agents.merge import merge_node
from app.agents.state import JournalState


def fuse_evidence_node(state: JournalState) -> Dict[str, Any]:
    """Format multi-source evidence for generation (stream prepare ends here)."""
    updates = merge_node(state) or {}
    updates["stage"] = "fused_evidence"
    return updates


def generate_answer_node(state: JournalState) -> Dict[str, Any]:
    """LLM / template synthesis from fused evidence."""
    result = generate_node(state) or {}
    result["stage"] = "fused"
    return result


def fusion_node(state: JournalState) -> Dict[str, Any]:
    """Full fusion: evidence formatting + final answer."""
    evidence_updates = fuse_evidence_node(state)
    merged_state: JournalState = {**state, **evidence_updates}
    answer_updates = generate_answer_node(merged_state)
    return {**evidence_updates, **answer_updates, "stage": "fused"}


__all__ = [
    "fusion_node",
    "fuse_evidence_node",
    "generate_answer_node",
    "stream_generate",
    "finalize_answer",
]
