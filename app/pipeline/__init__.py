"""Unified production request pipeline.

The former Controller/LangGraph/ReAct stack remains in ``app.agents`` only as
deprecated compatibility code.  Production entry points use this package.
"""

from app.pipeline.compiler import PlanCompiler
from app.pipeline.context import ContextResolver
from app.pipeline.executor import DeterministicExecutor
from app.pipeline.production import UnifiedProductionPipeline
from app.pipeline.semantic import SemanticRouter
from app.pipeline.validator import PlanValidator

__all__ = [
    "ContextResolver",
    "SemanticRouter",
    "PlanCompiler",
    "PlanValidator",
    "DeterministicExecutor",
    "UnifiedProductionPipeline",
]
