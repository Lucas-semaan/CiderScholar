"""Article metadata models."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

DOI_FULL_PATTERN = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)


class ArticleMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doi: str | None = None
    title: str
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    publication_year: int | None = Field(default=None, ge=1600, le=2200)
    language: str | None = None
    pdf_path: Path

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not DOI_FULL_PATTERN.fullmatch(normalized):
            raise ValueError("invalid DOI")
        return normalized.lower()
