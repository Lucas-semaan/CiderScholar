"""Strict hierarchical synthesis models with application-rendered citations."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.corpora import CorpusScope

ShortText = Annotated[str, Field(min_length=1, max_length=2000)]
DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/\S+", re.IGNORECASE)
MODEL_CITATION_PATTERN = re.compile(r"\[[^\]\n]+,\s*p{1,2}\.\s*[^\]\n]+\]", re.IGNORECASE)


class CitedStatement(BaseModel):
    """One factual statement supported by one or more persisted evidence rows."""

    model_config = ConfigDict(extra="forbid")

    statement: ShortText
    evidence_ids: list[str] = Field(min_length=1, max_length=8)

    @field_validator("statement")
    @classmethod
    def forbid_model_generated_references(cls, value: str) -> str:
        if DOI_PATTERN.search(value):
            raise ValueError("model-generated DOI is forbidden")
        if MODEL_CITATION_PATTERN.search(value):
            raise ValueError("citation text must be rendered by the application")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def deduplicate_evidence_ids(cls, value: list[str]) -> list[str]:
        if any(not evidence_id.strip() for evidence_id in value):
            raise ValueError("evidence identifiers cannot be empty")
        if len(set(value)) != len(value):
            raise ValueError("evidence identifiers cannot be duplicated")
        return value


class ThemeAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme_id: str = Field(pattern=r"^theme-[1-9][0-9]*$")
    label: str = Field(min_length=1, max_length=200)
    article_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("article_ids")
    @classmethod
    def unique_articles(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("a theme cannot repeat an article")
        return value


class ThemePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    themes: list[ThemeAssignment] = Field(min_length=1, max_length=10)


class ThemeSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme_id: str = Field(pattern=r"^theme-[1-9][0-9]*$")
    label: str = Field(min_length=1, max_length=200)
    article_ids: list[str] = Field(min_length=1, max_length=20)
    summary: list[CitedStatement] = Field(min_length=1, max_length=20)
    convergent_results: list[CitedStatement] = Field(max_length=20)
    contradictory_results: list[CitedStatement] = Field(max_length=20)
    quantitative_results: list[CitedStatement] = Field(max_length=20)
    missing_information: list[ShortText] = Field(max_length=20)


class FinalSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direct_answer: list[CitedStatement] = Field(min_length=1, max_length=20)
    consensus: list[CitedStatement] = Field(max_length=20)
    convergent_results: list[CitedStatement] = Field(max_length=20)
    contradictory_results: list[CitedStatement] = Field(max_length=20)
    quantitative_results: list[CitedStatement] = Field(max_length=20)
    missing_information: list[ShortText] = Field(max_length=20)


class BibliographyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(min_length=1)
    scope: CorpusScope = CorpusScope.COMMON
    title: str = Field(min_length=1)
    authors: list[str]
    journal: str | None
    publication_year: int | None
    doi: str | None


class SynthesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    question: str
    themes: list[ThemeSynthesis]
    final: FinalSynthesis
    bibliography: list[BibliographyEntry]
    answer_markdown: str
    cited_evidence_ids: list[str]
