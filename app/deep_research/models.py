"""Shared strict models for durable deep-research artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.corpora import CorpusScope


class ContextualSummaryResult(BaseModel):
    """Persistable ARGO summary linked to one local fragment without raw text."""

    model_config = ConfigDict(extra="forbid")

    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    article_id: str
    chunk_id: int = Field(ge=1)
    scope: CorpusScope
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    summary: str = Field(max_length=1_200)
    relevance_score: float = Field(ge=0.0, le=1.0)
    relevant: bool


class ContextualEvidenceGate(BaseModel):
    """Only contextual summaries admitted to downstream evidence stages."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    threshold: float = Field(ge=0.0, le=1.0)
    source_summary_count: int = Field(ge=0, le=12)
    rejected_summary_count: int = Field(ge=0, le=12)
    accepted: list[ContextualSummaryResult] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def enforce_filtered_only(self) -> ContextualEvidenceGate:
        if len(self.accepted) + self.rejected_summary_count != self.source_summary_count:
            raise ValueError("contextual evidence counts are inconsistent")
        if any(
            not summary.relevant or summary.relevance_score < self.threshold
            for summary in self.accepted
        ):
            raise ValueError("rejected contextual summary cannot enter evidence")
        identities = {
            (
                summary.scope,
                summary.article_id,
                summary.chunk_id,
                summary.text_sha256,
            )
            for summary in self.accepted
        }
        if len(identities) != len(self.accepted):
            raise ValueError("contextual evidence cannot contain duplicates")
        return self
