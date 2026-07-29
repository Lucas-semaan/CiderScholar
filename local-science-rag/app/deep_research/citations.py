"""Bounded traversal of DOI relations observed in consulted local text."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.jobs.contracts import DeepResearchPayload
from app.updates.models import normalize_doi

MAX_CITATION_RELATIONS = 8
MAX_CITATION_DEPTH = 1

_DOI_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])10\.\d{4,9}/[-._;()/:A-Z0-9]+",
    flags=re.IGNORECASE,
)
_DOI_TRAILING_PUNCTUATION = ".,;:)]}>\"'"


class CitationSourceFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: CorpusScope
    article_id: str = Field(min_length=1, max_length=200)
    chunk_id: int = Field(ge=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    text: str = Field(min_length=1)


class ResolvedCitationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_doi: str = Field(min_length=6, max_length=200)
    target_scope: CorpusScope | None = None
    target_article_id: str | None = Field(default=None, max_length=200)
    access_status: Literal["consulted_local_text", "metadata_only", "unavailable"]
    consulted_chunk_id: int | None = Field(default=None, ge=1)
    consulted_page_start: int | None = Field(default=None, ge=1)
    consulted_page_end: int | None = Field(default=None, ge=1)
    consulted_text_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def keep_consultation_claims_truthful(self) -> ResolvedCitationTarget:
        consultation = (
            self.consulted_chunk_id,
            self.consulted_page_start,
            self.consulted_page_end,
            self.consulted_text_sha256,
        )
        if self.access_status == "consulted_local_text":
            if (
                self.target_scope is None
                or self.target_article_id is None
                or any(value is None for value in consultation)
            ):
                raise ValueError("consulted local citation target requires complete provenance")
        elif any(value is not None for value in consultation):
            raise ValueError("unconsulted citation target cannot carry consulted text provenance")
        if self.access_status == "unavailable" and (
            self.target_scope is not None or self.target_article_id is not None
        ):
            raise ValueError("unavailable citation target cannot claim a local article")
        return self


class CitationTraversalEntry(ResolvedCitationTarget):
    source_scope: CorpusScope
    source_article_id: str = Field(min_length=1, max_length=200)
    source_chunk_id: int = Field(ge=1)
    source_page_start: int = Field(ge=1)
    source_page_end: int = Field(ge=1)
    relation: Literal["references"] = "references"
    addition_reason: Literal["doi_explicitly_observed_in_consulted_fragment"] = (
        "doi_explicitly_observed_in_consulted_fragment"
    )
    depth: Literal[1] = 1


class CitationTraversalCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    max_depth: Literal[1] = MAX_CITATION_DEPTH
    max_relations: Literal[8] = MAX_CITATION_RELATIONS
    entries: list[CitationTraversalEntry] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_relations(self) -> CitationTraversalCheckpoint:
        identities = {
            (
                item.source_scope,
                item.source_article_id,
                item.source_chunk_id,
                item.target_doi,
            )
            for item in self.entries
        }
        if len(identities) != len(self.entries):
            raise ValueError("citation traversal relations cannot be duplicated")
        return self


class CitationTargetResolver(Protocol):
    def resolve(
        self,
        *,
        source_scope: CorpusScope,
        source_article_id: str,
        target_doi: str,
    ) -> ResolvedCitationTarget | None: ...


class SQLiteCitationTargetResolver:
    """Resolve DOI targets only from the two authoritative local databases."""

    def __init__(self, settings: Settings) -> None:
        self.databases = {
            scope: Database(corpus_paths(settings, scope).database_path)
            for scope in (CorpusScope.COMMON, CorpusScope.PRIVATE)
        }

    def resolve(
        self,
        *,
        source_scope: CorpusScope,
        source_article_id: str,
        target_doi: str,
    ) -> ResolvedCitationTarget | None:
        for scope in (source_scope, *(item for item in self.databases if item != source_scope)):
            row = self.databases[scope].article_with_first_chunk_by_doi(target_doi)
            if row is None:
                continue
            if scope == source_scope and str(row["article_id"]) == source_article_id:
                return None
            if row["chunk_id"] is None:
                return ResolvedCitationTarget(
                    target_doi=target_doi,
                    target_scope=scope,
                    target_article_id=str(row["article_id"]),
                    access_status="metadata_only",
                )
            text = str(row["text"])
            return ResolvedCitationTarget(
                target_doi=target_doi,
                target_scope=scope,
                target_article_id=str(row["article_id"]),
                access_status="consulted_local_text",
                consulted_chunk_id=int(row["chunk_id"]),
                consulted_page_start=int(row["page_start"]),
                consulted_page_end=int(row["page_end"]),
                consulted_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
        return ResolvedCitationTarget(
            target_doi=target_doi,
            access_status="unavailable",
        )


def extract_dois(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _DOI_PATTERN.findall(text):
        normalized = normalize_doi(match.rstrip(_DOI_TRAILING_PUNCTUATION))
        if normalized and normalized not in found:
            found.append(normalized)
    return tuple(found)


class CitationTraversalStage:
    def __init__(
        self,
        resolver: CitationTargetResolver,
        checkpoint_root: Path,
        *,
        max_relations: int = MAX_CITATION_RELATIONS,
    ) -> None:
        if not 1 <= max_relations <= MAX_CITATION_RELATIONS:
            raise ValueError("citation traversal limit must be between 1 and 8")
        self.resolver = resolver
        self.checkpoint_root = checkpoint_root
        self.max_relations = max_relations

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "citation-traversal.json"
        )

    def load(self, payload: DeepResearchPayload) -> CitationTraversalCheckpoint:
        path = self._path(payload)
        if not path.is_file():
            raise RuntimeError("deep-research citation traversal checkpoint is missing")
        return CitationTraversalCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))

    def traverse(
        self,
        payload: DeepResearchPayload,
        fragments: list[CitationSourceFragment],
    ) -> CitationTraversalCheckpoint:
        path = self._path(payload)
        if path.is_file():
            return self.load(payload)
        entries: list[CitationTraversalEntry] = []
        seen: set[tuple[CorpusScope, str, int, str]] = set()
        for fragment in fragments:
            for doi in extract_dois(fragment.text):
                identity = (
                    fragment.scope,
                    fragment.article_id,
                    fragment.chunk_id,
                    doi,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                resolved = self.resolver.resolve(
                    source_scope=fragment.scope,
                    source_article_id=fragment.article_id,
                    target_doi=doi,
                )
                if resolved is None:
                    continue
                entries.append(
                    CitationTraversalEntry(
                        **resolved.model_dump(),
                        source_scope=fragment.scope,
                        source_article_id=fragment.article_id,
                        source_chunk_id=fragment.chunk_id,
                        source_page_start=fragment.page_start,
                        source_page_end=fragment.page_end,
                    )
                )
                if len(entries) >= self.max_relations:
                    break
            if len(entries) >= self.max_relations:
                break
        checkpoint = CitationTraversalCheckpoint(entries=entries)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(checkpoint.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return checkpoint
