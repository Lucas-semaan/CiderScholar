"""Strict, page-traceable evidence extracted from selected SQLite chunks."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=2000)]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: ShortText
    source_excerpt: str = Field(min_length=1, max_length=6000)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    chunk_id: str = Field(pattern=r"^[1-9][0-9]*$")

    @model_validator(mode="after")
    def validate_pages(self) -> Finding:
        if self.page_end < self.page_start:
            raise ValueError("finding page_end cannot precede page_start")
        return self


class ArticleEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(min_length=1)
    relevance_score: float = Field(ge=0.0, le=1.0)
    question_addressed: ShortText
    findings: list[Finding] = Field(max_length=20)
    topics: list[ShortText] = Field(max_length=20)
    contradictions: list[ShortText] = Field(max_length=20)
    missing_information: list[ShortText] = Field(max_length=20)
