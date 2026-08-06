from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


CapabilityName = Literal["sql", "kg", "rag"]


class CapabilityResult(TypedDict, total=False):
    ok: bool
    capability: CapabilityName
    operation: str
    data: Dict[str, Any]
    citations: List[Dict[str, Any]]
    error: Optional[str]


def ok_result(
    capability: CapabilityName,
    operation: str,
    data: Dict[str, Any] | None = None,
    citations: List[Dict[str, Any]] | None = None,
) -> CapabilityResult:
    return {
        "ok": True,
        "capability": capability,
        "operation": operation,
        "data": data or {},
        "citations": citations or [],
        "error": None,
    }


def err_result(
    capability: CapabilityName,
    operation: str,
    error: str,
) -> CapabilityResult:
    return {
        "ok": False,
        "capability": capability,
        "operation": operation,
        "data": {},
        "citations": [],
        "error": error,
    }
