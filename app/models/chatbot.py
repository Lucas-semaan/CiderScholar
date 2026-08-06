"""Typed chatbot results grounded in local or explicitly queried sources."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.corpora import CorpusScope


class ChatEvidencePassage(BaseModel):
    """One bounded, persistable unit supplied to the chat generation model."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=12000)
    evidence_kind: Literal["text", "figure"] = "text"
    chunk_id: int | None = Field(default=None, gt=0)
    section: str | None = Field(default=None, max_length=200)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    figure_analysis_id: str | None = Field(
        default=None,
        pattern=r"^figure-analysis-[0-9a-f]{24}$",
    )
    figure_label: str | None = Field(default=None, max_length=500)
    visual_model: str | None = Field(default=None, max_length=200)
    source_image_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_location(self) -> ChatEvidencePassage:
        pages = (self.page_start, self.page_end)
        if (pages[0] is None) != (pages[1] is None):
            raise ValueError("evidence pages must be both present or both absent")
        if pages[0] is not None and pages[1] is not None and pages[1] < pages[0]:
            raise ValueError("evidence page_end cannot precede page_start")
        if self.chunk_id is not None and self.page_start is None:
            raise ValueError("full-text chunk evidence requires page coordinates")
        figure_fields = (
            self.figure_analysis_id,
            self.figure_label,
            self.visual_model,
            self.source_image_sha256,
        )
        if self.evidence_kind == "figure":
            if self.page_start != self.page_end or any(value is None for value in figure_fields):
                raise ValueError("figure evidence requires complete single-page provenance")
        elif any(value is not None for value in figure_fields):
            raise ValueError("text evidence cannot carry figure provenance")
        return self


class ChatEvidenceRecord(BaseModel):
    """Article metadata plus the exact abstract or full-text passages used as evidence."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    origin: Literal["local_rag", "external_api"]
    evidence_level: Literal["abstract", "full_text"]
    scope: CorpusScope | None = None
    article_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    journal: str | None = None
    publication_year: int | None = None
    providers: list[str] = Field(default_factory=list)
    url: str | None = None
    score: float = 0.0
    matched_facets: list[str] = Field(default_factory=list, max_length=12)
    matrix_tier: Literal["exact", "near", "distant", "none"] = "none"
    evidence_grade: Literal["A", "B", "C", "D", "unassessed"] = "unassessed"
    passages: list[ChatEvidencePassage] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_evidence_level(self) -> ChatEvidenceRecord:
        has_chunks = any(passage.chunk_id is not None for passage in self.passages)
        if self.evidence_level == "full_text":
            if self.article_id is None or not has_chunks:
                raise ValueError("full-text evidence requires an article and persisted chunks")
        elif has_chunks:
            raise ValueError("abstract evidence cannot reference full-text chunks")
        return self


class ChatbotSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    origin: Literal["local_rag", "external_api"]
    evidence_level: Literal["abstract", "full_text"] = "abstract"
    scope: CorpusScope | None = None
    article_id: str | None = None
    chunk_ids: list[int] = Field(default_factory=list, max_length=8)
    page_ranges: list[str] = Field(default_factory=list, max_length=8)
    figure_refs: list[str] = Field(default_factory=list, max_length=8)
    title: str
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    journal: str | None = None
    publication_year: int | None = None
    providers: list[str]
    url: str | None = None
    snippet: str = Field(max_length=800)


class ChatbotFacetDraft(BaseModel):
    """One bounded, cited intermediate answer for a synthesis facet."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=4_000)
    answer_markdown: str = Field(min_length=1, max_length=30_000)
    cited_evidence_ids: list[str] = Field(default_factory=list, max_length=64)
    source_record_ids: list[str] = Field(default_factory=list, max_length=64)


class ChatbotEvaluationTrace(BaseModel):
    """Immutable identity of one prompt-profile evaluation cell."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    question_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
    profile: Literal["p0", "p1", "p2"]
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChatbotResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    retrieval_query: str
    answer_markdown: str
    sources: list[ChatbotSource]
    warnings: list[str]
    model: str
    local_result_count: int = Field(ge=0)
    external_result_count: int = Field(ge=0)
    external_enrichment_used: bool
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    generation_status: Literal[
        "generated",
        "extractive_fallback",
        "diagnostic_only",
    ] = "generated"
    diagnostic_code: str | None = Field(default=None, max_length=100)
    interaction_mode: Literal["research", "conversation"] = "research"
    reused_previous_sources: bool = False
    facet_drafts: list[ChatbotFacetDraft] = Field(default_factory=list, max_length=12)
    figure_analysis_requested: bool = False
    figure_analysis_count: int = Field(default=0, ge=0, le=10)
    figure_analysis_duration_seconds: float = Field(default=0.0, ge=0.0)
    figure_analysis_model: str | None = Field(default=None, max_length=200)
    evaluation: ChatbotEvaluationTrace | None = None
