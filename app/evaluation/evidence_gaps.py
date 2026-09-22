"""Evaluation-only contracts for bounded, traceable evidence gaps."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GapKind = Literal["missing_population", "missing_intervention", "missing_outcome", "contradiction"]


class EvidenceGap(BaseModel):
    """A bounded verification need, never a conversational axis or acquisition request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: GapKind
    query: str = Field(min_length=2, max_length=500)
    reason: str = Field(min_length=2, max_length=1_000)
    history: tuple[str, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def query_is_not_already_tried(self) -> EvidenceGap:
        normalized = " ".join(self.query.casefold().split())
        if normalized in {" ".join(item.casefold().split()) for item in self.history}:
            raise ValueError("evidence gap query is already present in its history")
        return self


class EvidenceGapPlan(BaseModel):
    """One evaluation-only follow-up wave, capped before any SQLite retrieval begins."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gaps: tuple[EvidenceGap, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_queries(self) -> EvidenceGapPlan:
        queries = [" ".join(gap.query.casefold().split()) for gap in self.gaps]
        if len(queries) != len(set(queries)):
            raise ValueError("evidence gap queries must be unique")
        return self
