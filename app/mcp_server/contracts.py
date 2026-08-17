from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import DEFAULT_JOURNAL_ID


ToolStatus = Literal["complete", "partial", "ambiguous", "unsupported", "error"]
JournalId = Literal["ZDXBNXB", "ZDXBRWB"]
EntityType = Literal["author", "institution", "paper", "topic"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceRef(StrictModel):
    evidence_type: Literal["paper", "statistic", "entity", "data_scope"]
    source: str
    ref_id: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class ToolResponse(StrictModel):
    status: ToolStatus
    scope: Dict[str, Any] = Field(default_factory=dict)
    data: Dict[str, Any] = Field(default_factory=dict)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    missing_inputs: List[str] = Field(default_factory=list)
    unsupported_claims: List[str] = Field(default_factory=list)
    data_as_of: Optional[str] = None
    next_cursor: Optional[int] = None


class PublicationScope(StrictModel):
    journal_id: JournalId = DEFAULT_JOURNAL_ID
    year_start: Optional[int] = Field(default=None, ge=1900, le=2100)
    year_end: Optional[int] = Field(default=None, ge=1900, le=2100)
    topics: List[str] = Field(default_factory=list, max_length=12)
    author: Optional[str] = None
    institution: Optional[str] = None
    article_type: Optional[str] = None
    dois: List[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_years(self) -> "PublicationScope":
        if (
            self.year_start is not None
            and self.year_end is not None
            and self.year_start > self.year_end
        ):
            raise ValueError("year_start must be less than or equal to year_end")
        return self


class ResearchDescription(StrictModel):
    title: str = Field(default="", max_length=500)
    abstract: str = Field(default="", max_length=20000)
    keywords: List[str] = Field(default_factory=list, max_length=20)
    topics: List[str] = Field(default_factory=list, max_length=20)
    research_objects: List[str] = Field(default_factory=list, max_length=20)
    methods: List[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_content(self) -> "ResearchDescription":
        if not any(
            [
                self.title,
                self.abstract,
                self.keywords,
                self.topics,
                self.research_objects,
                self.methods,
            ]
        ):
            raise ValueError("at least one research description field is required")
        return self


class CompareSet(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    scope: PublicationScope
