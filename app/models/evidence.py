"""Strict, page-traceable evidence extracted from selected SQLite chunks."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=2000)]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: ShortText
    source_excerpt: str = Field(min_length=1, max_length=6000)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    locator_kind: Literal["page", "structural"] = "page"
    section_path: str | None = None
    paragraph_start: int | None = Field(default=None, ge=0)
    paragraph_end: int | None = Field(default=None, ge=0)
    xml_id_start: str | None = None
    xml_id_end: str | None = None
    chunk_id: str = Field(pattern=r"^[1-9][0-9]*$")

    @model_validator(mode="after")
    def validate_pages(self) -> Finding:
        if self.locator_kind == "page":
            if self.page_start is None or self.page_end is None:
                raise ValueError("page findings require both page bounds")
            if any(
                value is not None
                for value in (
                    self.section_path,
                    self.paragraph_start,
                    self.paragraph_end,
                    self.xml_id_start,
                    self.xml_id_end,
                )
            ):
                raise ValueError("page findings cannot carry structural bounds")
        else:
            if self.page_start is not None or self.page_end is not None:
                raise ValueError("structural findings cannot carry page bounds")
            if not self.section_path or self.paragraph_start is None or self.paragraph_end is None:
                raise ValueError("structural findings require a section and paragraph bounds")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("finding page_end cannot precede page_start")
        if (
            self.paragraph_start is not None
            and self.paragraph_end is not None
            and self.paragraph_end < self.paragraph_start
        ):
            raise ValueError("finding paragraph_end cannot precede paragraph_start")
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
