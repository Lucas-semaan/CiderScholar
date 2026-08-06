"""Application-only citation and bibliography rendering from scoped SQLite."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths, corpus_scope_label
from app.database.sqlite import Database
from app.deep_research.admission import ClaimAdmissionCheckpoint, ClaimAdmissionStage
from app.deep_research.claims import AtomicClaimCheckpoint
from app.jobs.contracts import DeepResearchPayload


class DeepResearchCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^claim-[0-9a-f]{20}$")
    evidence_kind: Literal["text", "figure"] = "text"
    scope: CorpusScope
    article_id: str = Field(min_length=1, max_length=200)
    article_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_id: int = Field(ge=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    figure_analysis_id: str | None = Field(
        default=None,
        pattern=r"^figure-analysis-[0-9a-f]{24}$",
    )
    figure_label: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_visual_citation(self) -> DeepResearchCitation:
        figure_fields = (self.figure_analysis_id, self.figure_label)
        if self.evidence_kind == "figure":
            if any(value is None for value in figure_fields) or self.page_start != self.page_end:
                raise ValueError("visual citation requires complete figure provenance")
        elif any(value is not None for value in figure_fields):
            raise ValueError("text citation cannot carry figure provenance")
        return self


class DeepResearchBibliographyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: CorpusScope
    article_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1)
    authors: list[str]
    journal: str | None = None
    publication_year: int | None = Field(default=None, ge=1600, le=2200)
    doi: str | None = None


class DeepResearchRenderedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    answer_markdown: str = Field(min_length=1)
    citations: list[DeepResearchCitation] = Field(min_length=1, max_length=80)
    bibliography: list[DeepResearchBibliographyEntry] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def bibliography_covers_citations(self) -> DeepResearchRenderedAnswer:
        cited = {(item.scope, item.article_id) for item in self.citations}
        listed = {(item.scope, item.article_id) for item in self.bibliography}
        if cited != listed:
            raise ValueError("rendered bibliography must exactly cover cited SQLite articles")
        return self


class SQLiteDeepResearchRenderer:
    def __init__(self, settings: Settings, checkpoint_root: Path) -> None:
        self.databases = {
            CorpusScope.COMMON: Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
        }
        self.checkpoint_root = checkpoint_root

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "rendered-answer.json"
        )

    def load(self, payload: DeepResearchPayload) -> DeepResearchRenderedAnswer:
        path = self._path(payload)
        if not path.is_file():
            raise RuntimeError("deep-research rendered-answer checkpoint is missing")
        return DeepResearchRenderedAnswer.model_validate_json(path.read_text(encoding="utf-8"))

    def render(
        self,
        payload: DeepResearchPayload,
        claims: AtomicClaimCheckpoint,
        admission: ClaimAdmissionCheckpoint,
    ) -> DeepResearchRenderedAnswer:
        path = self._path(payload)
        if path.is_file():
            return self.load(payload)
        admitted = ClaimAdmissionStage.admitted_claims(claims, admission)
        if not admitted:
            raise ValueError("an answer cannot be rendered without admitted claims")
        citations: list[DeepResearchCitation] = []
        bibliography: dict[
            tuple[CorpusScope, str],
            DeepResearchBibliographyEntry,
        ] = {}
        lines: list[str] = []
        for claim in admitted:
            claim_citations: list[DeepResearchCitation] = []
            for evidence in claim.evidence:
                if evidence.evidence_kind == "figure":
                    if evidence.figure_analysis_id is None:
                        raise RuntimeError("visual evidence has no persisted analysis")
                    row = self.databases[evidence.scope].figure_analysis_citation_source(
                        evidence.figure_analysis_id
                    )
                else:
                    row = self.databases[evidence.scope].deep_research_citation_source(
                        article_id=evidence.article_id,
                        chunk_id=evidence.chunk_id,
                    )
                if row is None:
                    raise RuntimeError("admitted claim source is missing from scoped SQLite")
                source_text = str(
                    row["observation_text"] if evidence.evidence_kind == "figure" else row["text"]
                )
                if (
                    hashlib.sha256(source_text.encode()).hexdigest() != evidence.source_text_sha256
                    or evidence.source_excerpt not in source_text
                    or str(row["article_id"]) != evidence.article_id
                ):
                    raise RuntimeError("admitted claim source changed in scoped SQLite")
                citation = DeepResearchCitation(
                    claim_id=claim.claim_id,
                    evidence_kind=evidence.evidence_kind,
                    scope=evidence.scope,
                    article_id=str(row["article_id"]),
                    article_sha256=str(row["article_sha256"]),
                    chunk_id=evidence.chunk_id,
                    page_start=(
                        int(row["page_number"])
                        if evidence.evidence_kind == "figure"
                        else int(row["page_start"])
                    ),
                    page_end=(
                        int(row["page_number"])
                        if evidence.evidence_kind == "figure"
                        else int(row["page_end"])
                    ),
                    figure_analysis_id=evidence.figure_analysis_id,
                    figure_label=evidence.figure_label,
                )
                claim_citations.append(citation)
                citations.append(citation)
                authors = json.loads(str(row["authors"]))
                if not isinstance(authors, list) or any(
                    not isinstance(author, str) for author in authors
                ):
                    raise RuntimeError("SQLite article authors are invalid")
                bibliography[(evidence.scope, str(row["article_id"]))] = (
                    DeepResearchBibliographyEntry(
                        scope=evidence.scope,
                        article_id=str(row["article_id"]),
                        title=str(row["title"]),
                        authors=authors,
                        journal=str(row["journal"]) if row["journal"] else None,
                        publication_year=(
                            int(row["publication_year"])
                            if row["publication_year"] is not None
                            else None
                        ),
                        doi=str(row["doi"]) if row["doi"] else None,
                    )
                )
            rendered_citations = " ".join(self._render_citation(item) for item in claim_citations)
            lines.append(f"- {claim.statement} {rendered_citations}")
        ordered_bibliography = sorted(
            bibliography.values(),
            key=lambda item: (
                item.authors[0].casefold() if item.authors else "",
                item.publication_year or 0,
                item.title.casefold(),
            ),
        )
        references = "\n".join(
            f"- {self._render_bibliography(item)}" for item in ordered_bibliography
        )
        rendered = DeepResearchRenderedAnswer(
            answer_markdown=(
                "## Résultats étayés\n\n" + "\n".join(lines) + "\n\n## Références\n\n" + references
            ),
            citations=citations,
            bibliography=ordered_bibliography,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(rendered.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return rendered

    @staticmethod
    def _render_citation(citation: DeepResearchCitation) -> str:
        pages = (
            f"p. {citation.page_start}"
            if citation.page_start == citation.page_end
            else f"pp. {citation.page_start}–{citation.page_end}"
        )
        if citation.evidence_kind == "figure":
            return (
                f"[{corpus_scope_label(citation.scope)} · {citation.article_id}, "
                f"{citation.figure_label}, {pages} · analyse visuelle locale validée]"
            )
        return f"[{corpus_scope_label(citation.scope)} · {citation.article_id}, {pages}]"

    @staticmethod
    def _render_bibliography(entry: DeepResearchBibliographyEntry) -> str:
        authors = ", ".join(entry.authors) if entry.authors else "Auteur non renseigné"
        year = f" ({entry.publication_year})." if entry.publication_year else "."
        journal = f" *{entry.journal}*." if entry.journal else ""
        doi = f" DOI : {entry.doi}." if entry.doi else ""
        return f"{authors}{year} {entry.title}.{journal}{doi}"
