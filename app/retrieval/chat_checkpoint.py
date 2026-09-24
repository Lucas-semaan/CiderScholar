"""Durable, text-free checkpoints for resumable chat retrieval."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.config import Settings
from app.corpora import CorpusScope
from app.models.chatbot import (
    ChatbotRetrievalTrace,
    ChatbotTiming,
    ChatbotTraceCandidate,
    ChatEvidencePassage,
    ChatEvidenceRecord,
)
from app.retrieval.hypothesis_planning import HypothesisPlanningResult
from app.retrieval.rehydration import rehydrate_records


class ChatCheckpointPassage(BaseModel):
    """Persisted identity of one passage; SQLite remains authoritative for its text."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=300)
    chunk_id: int | None = Field(default=None, gt=0)
    context_role: Literal[
        "anchor",
        "result",
        "method_or_conditions",
        "discussion_or_limit",
        "supporting_context",
        "other",
    ] = "other"
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    locator_kind: Literal["page", "structural"] | None = None
    section_path: str | None = Field(default=None, max_length=2_000)
    paragraph_start: int | None = Field(default=None, ge=0)
    paragraph_end: int | None = Field(default=None, ge=0)
    xml_id_start: str | None = Field(default=None, max_length=255)
    xml_id_end: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_location(self) -> ChatCheckpointPassage:
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("checkpoint passage pages must be both present or absent")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("checkpoint passage end page cannot precede its start page")
        structural = (self.section_path, self.paragraph_start, self.paragraph_end)
        if any(value is not None for value in structural):
            if self.locator_kind != "structural" or not all(
                value is not None for value in structural
            ):
                raise ValueError("structural checkpoint passages require path and paragraph bounds")
            if (
                self.paragraph_start is not None
                and self.paragraph_end is not None
                and self.paragraph_end < self.paragraph_start
            ):
                raise ValueError("checkpoint structural paragraph bounds are invalid")
            if self.page_start is not None:
                raise ValueError("structural checkpoint passages cannot carry page coordinates")
        elif self.locator_kind == "structural":
            raise ValueError("structural checkpoint passages require path and paragraph bounds")
        if (
            self.chunk_id is not None
            and self.page_start is None
            and self.locator_kind != "structural"
        ):
            raise ValueError("checkpoint chunk references require a typed locator")
        return self


class ChatCheckpointEvidence(BaseModel):
    """Non-textual identity and ranking metadata for one retrieved SQLite record."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=300)
    evidence_level: Literal["abstract", "full_text"]
    scope: CorpusScope
    article_id: str | None = Field(default=None, max_length=300)
    providers: list[str] = Field(default_factory=list, max_length=20)
    score: float = 0.0
    matched_facets: list[str] = Field(default_factory=list, max_length=12)
    matrix_tier: Literal["exact", "near", "distant", "none"] = "none"
    evidence_grade: Literal["A", "B", "C", "D", "unassessed"] = "unassessed"
    passages: list[ChatCheckpointPassage] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_evidence_level(self) -> ChatCheckpointEvidence:
        has_chunks = any(passage.chunk_id is not None for passage in self.passages)
        if self.evidence_level == "full_text":
            if self.article_id is None or not has_chunks:
                raise ValueError("full-text checkpoints require an article and chunk identities")
        elif has_chunks:
            raise ValueError("abstract checkpoints cannot reference full-text chunks")
        return self

    @classmethod
    def from_record(cls, record: ChatEvidenceRecord) -> ChatCheckpointEvidence:
        if record.origin != "local_rag" or record.scope is None:
            raise ValueError("chat checkpoints accept only SQLite-authoritative evidence")
        return cls(
            record_id=record.record_id,
            evidence_level=record.evidence_level,
            scope=record.scope,
            article_id=record.article_id,
            providers=record.providers,
            score=record.score,
            matched_facets=record.matched_facets,
            matrix_tier=record.matrix_tier,
            evidence_grade=record.evidence_grade,
            passages=[
                ChatCheckpointPassage(
                    evidence_id=passage.evidence_id,
                    chunk_id=passage.chunk_id,
                    context_role=passage.context_role,
                    page_start=passage.page_start,
                    page_end=passage.page_end,
                    locator_kind=passage.locator_kind,
                    section_path=passage.section_path,
                    paragraph_start=passage.paragraph_start,
                    paragraph_end=passage.paragraph_end,
                    xml_id_start=passage.xml_id_start,
                    xml_id_end=passage.xml_id_end,
                )
                for passage in record.passages
            ],
        )

    def as_rehydration_record(self) -> ChatEvidenceRecord:
        return ChatEvidenceRecord(
            record_id=self.record_id,
            origin="local_rag",
            evidence_level=self.evidence_level,
            scope=self.scope,
            article_id=self.article_id,
            title="SQLite checkpoint reference",
            providers=self.providers,
            score=self.score,
            matched_facets=self.matched_facets,
            matrix_tier=self.matrix_tier,
            evidence_grade=self.evidence_grade,
            passages=[
                ChatEvidencePassage(
                    evidence_id=passage.evidence_id,
                    text="SQLite checkpoint reference",
                    chunk_id=passage.chunk_id,
                    context_role=passage.context_role,
                    page_start=passage.page_start,
                    page_end=passage.page_end,
                    locator_kind=passage.locator_kind,
                    section_path=passage.section_path,
                    paragraph_start=passage.paragraph_start,
                    paragraph_end=passage.paragraph_end,
                    xml_id_start=passage.xml_id_start,
                    xml_id_end=passage.xml_id_end,
                )
                for passage in self.passages
            ],
        )


class ChatRetrievalCheckpoint(BaseModel):
    """Completed retrieval boundary that can resume at semantic validation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    retrieval_query: str = Field(min_length=2, max_length=4_000)
    corpus_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    planning: HypothesisPlanningResult
    evidence: list[ChatCheckpointEvidence] = Field(min_length=1, max_length=48)
    external_result_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    timings: list[ChatbotTiming] = Field(default_factory=list, max_length=40)
    retrieval_traces: list[ChatbotRetrievalTrace] = Field(default_factory=list, max_length=40)
    retrieval_trace_candidates: list[ChatbotTraceCandidate] = Field(
        default_factory=list,
        max_length=300,
    )

    @classmethod
    def capture(
        cls,
        *,
        retrieval_query: str,
        corpus_fingerprint: str,
        planning: HypothesisPlanningResult,
        evidence: list[ChatEvidenceRecord],
        external_result_count: int,
        warnings: list[str],
        timings: list[ChatbotTiming],
        retrieval_traces: list[ChatbotRetrievalTrace],
        retrieval_trace_candidates: list[ChatbotTraceCandidate] | None = None,
    ) -> ChatRetrievalCheckpoint:
        return cls(
            retrieval_query=retrieval_query,
            corpus_fingerprint=corpus_fingerprint,
            planning=planning,
            evidence=[ChatCheckpointEvidence.from_record(record) for record in evidence],
            external_result_count=external_result_count,
            warnings=warnings,
            timings=timings,
            retrieval_traces=retrieval_traces,
            retrieval_trace_candidates=list(retrieval_trace_candidates or []),
        )

    def rehydrate(self, settings: Settings) -> list[ChatEvidenceRecord]:
        return rehydrate_records(
            settings,
            [record.as_rehydration_record() for record in self.evidence],
        )


class FigureAnalysisCheckpointItem(BaseModel):
    """Text-free identity of one completed visual candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    element_id: str = Field(min_length=1, max_length=500)
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_name: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(min_length=1, max_length=200)
    analysis_id: str = Field(pattern=r"^figure-analysis-[0-9a-f]{24}$")
    admitted: bool


class FigureAnalysisCheckpoint(BaseModel):
    """Bounded, replayable visual progress without image or observation text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[FigureAnalysisCheckpointItem] = Field(default_factory=list, max_length=10)

    @classmethod
    def empty(cls, request_fingerprint: str) -> FigureAnalysisCheckpoint:
        return cls(request_fingerprint=request_fingerprint)

    def with_item(self, item: FigureAnalysisCheckpointItem) -> FigureAnalysisCheckpoint:
        kept = [
            existing
            for existing in self.items
            if (
                existing.element_id,
                existing.image_sha256,
                existing.analysis_contract_sha256,
                existing.model_name,
                existing.model_revision,
            )
            != (
                item.element_id,
                item.image_sha256,
                item.analysis_contract_sha256,
                item.model_name,
                item.model_revision,
            )
        ]
        return self.model_copy(update={"items": [*kept, item][-10:]})


class FigureAnalysisCheckpointStore:
    """Atomically persist one visual checkpoint under a chat message identity."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, user_message_id: UUID) -> Path:
        return self.root / str(user_message_id) / "figure_analysis.json"

    def load(
        self,
        user_message_id: UUID,
        *,
        request_fingerprint: str,
    ) -> FigureAnalysisCheckpoint | None:
        path = self._path(user_message_id)
        if not path.is_file():
            return None
        try:
            checkpoint = FigureAnalysisCheckpoint.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError):
            return None
        return checkpoint if checkpoint.request_fingerprint == request_fingerprint else None

    def save(
        self,
        user_message_id: UUID,
        *,
        checkpoint: FigureAnalysisCheckpoint,
    ) -> None:
        path = self._path(user_message_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            checkpoint.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


class _CheckpointEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint: ChatRetrievalCheckpoint


class ChatRetrievalCheckpointStore:
    """Atomically persist checkpoints under one durable user-message identity."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def request_fingerprint(request: dict[str, Any]) -> str:
        encoded = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(b"ciderscholar-chat-checkpoint-v1\0" + encoded).hexdigest()

    def _path(self, user_message_id: UUID) -> Path:
        return self.root / str(user_message_id) / "retrieval.json"

    def load(
        self,
        user_message_id: UUID,
        *,
        request_fingerprint: str,
    ) -> ChatRetrievalCheckpoint | None:
        path = self._path(user_message_id)
        if not path.is_file():
            return None
        try:
            envelope = _CheckpointEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError):
            return None
        if envelope.request_fingerprint != request_fingerprint:
            return None
        return envelope.checkpoint

    def save(
        self,
        user_message_id: UUID,
        *,
        request_fingerprint: str,
        checkpoint: ChatRetrievalCheckpoint,
    ) -> None:
        path = self._path(user_message_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        envelope = _CheckpointEnvelope(
            request_fingerprint=request_fingerprint,
            checkpoint=checkpoint,
        )
        temporary.write_text(
            json.dumps(envelope.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
