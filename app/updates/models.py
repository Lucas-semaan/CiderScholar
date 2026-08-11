"""Validated metadata returned by opt-in official bibliographic APIs."""

from __future__ import annotations

import html
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def normalize_doi(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = DOI_PATTERN.search(value.strip())
    return match.group(0).rstrip(".,;)").lower() if match else None


def clean_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    without_tags = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return " ".join(without_tags.split()) or None


def integer_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class BibliographicRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    journal: str | None = None
    work_type: str | None = Field(default=None, max_length=100)
    publisher: str | None = Field(default=None, max_length=500)
    publication_year: int | None = Field(default=None, ge=1600, le=2200)
    doi: str | None = None
    citation_count: int | None = Field(default=None, ge=0)
    url: str | None = None
    relevance_score: float | None = None

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_doi(value)
        if normalized is None or normalized != value.lower():
            raise ValueError("bibliographic DOI is invalid or not normalized")
        return normalized


class BibliographicSourceError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    error_type: str
    message: str


class BibliographicSearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    queried_sources: list[str]
    successful_sources: list[str]
    records: list[BibliographicRecord]
    errors: list[BibliographicSourceError]
    duration_seconds: float = Field(ge=0.0)
