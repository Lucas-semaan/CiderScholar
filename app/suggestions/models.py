"""Strict domain contracts for scientific document suggestions."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.suggestions.validation import normalize_suggestion_doi, validate_reference_url


class SuggestionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DoiSuggestionSource(SuggestionModel):
    kind: Literal["doi"] = "doi"
    doi: str
    title: str | None = Field(default=None, max_length=500)
    abstract: str | None = Field(default=None, max_length=4000)

    @field_validator("doi")
    @classmethod
    def normalize_doi(cls, value: str) -> str:
        return normalize_suggestion_doi(value)


class UrlSuggestionSource(SuggestionModel):
    kind: Literal["url"] = "url"
    url: str
    title: str | None = Field(default=None, max_length=500)
    abstract: str | None = Field(default=None, max_length=4000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return validate_reference_url(value)


class PdfSuggestionSource(SuggestionModel):
    kind: Literal["pdf"] = "pdf"
    internal_filename: str = Field(pattern=r"^suggestion-[0-9a-f]{32}\.pdf$")
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str | None = Field(default=None, max_length=500)
    doi: str | None = None
    abstract: str | None = Field(default=None, max_length=4000)

    @field_validator("doi")
    @classmethod
    def normalize_optional_doi(cls, value: str | None) -> str | None:
        return normalize_suggestion_doi(value) if value else None


class ManualSuggestionSource(SuggestionModel):
    kind: Literal["manual"] = "manual"
    title: str = Field(min_length=3, max_length=500)
    reference: str = Field(min_length=3, max_length=2000)
    doi: str | None = None
    abstract: str | None = Field(default=None, max_length=4000)

    @field_validator("doi")
    @classmethod
    def normalize_optional_doi(cls, value: str | None) -> str | None:
        return normalize_suggestion_doi(value) if value else None


SuggestionSource = Annotated[
    DoiSuggestionSource | UrlSuggestionSource | PdfSuggestionSource | ManualSuggestionSource,
    Field(discriminator="kind"),
]


class SuggestionDraft(SuggestionModel):
    schema_version: Literal[1] = 1
    suggestion_id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    source: SuggestionSource
    scientific_comment: str | None = Field(default=None, max_length=1500)

    @field_validator("scientific_comment")
    @classmethod
    def clean_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("suggestion timestamp must be timezone-aware")
        return value


class SuggestionCandidateContext(SuggestionModel):
    title: str | None = Field(default=None, max_length=500)
    doi: str | None = None
    abstract: str | None = Field(default=None, max_length=4000)
    text_excerpt: str | None = Field(default=None, max_length=8000)


class SuggestionArgoDecision(SuggestionModel):
    relevant: bool
    reason: str = Field(min_length=3, max_length=800)
    theme: str | None = Field(default=None, max_length=120)
    uncertainty: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)


class SuggestionArtifact(SuggestionModel):
    filename: str = Field(pattern=r"^suggestion-[0-9a-f]{32}\.pdf$")
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SuggestionPackage(SuggestionModel):
    schema_version: Literal[1] = 1
    suggestion_id: UUID
    created_at: datetime
    source: SuggestionSource
    scientific_comment: str | None = Field(default=None, max_length=1500)
    candidate: SuggestionCandidateContext
    decision: SuggestionArgoDecision
    artifacts: list[SuggestionArtifact] = Field(default_factory=list, max_length=1)


class SuggestionReceipt(SuggestionModel):
    suggestion_id: UUID
    submitted_at: datetime
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreparedSuggestionPackage(SuggestionModel):
    directory: str
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest: SuggestionPackage


class SuggestionSubmissionResult(SuggestionModel):
    suggestion_id: UUID
    state: Literal["accepted", "not_retained", "retry"]
    message: str
    action: Literal["none", "settings", "retry"] = "none"
    decision: SuggestionArgoDecision | None = None
