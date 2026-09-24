"""Closed, content-free manifests for expert-memory run traceability."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.chat_effort import AnswerEffort
from app.knowledge.contracts import Sha256


class TraceModel(BaseModel):
    """Strict nested model used by every persisted trace fragment."""

    model_config = ConfigDict(extra="forbid", frozen=True)


TraceCode = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,100}$")]
TraceId = Annotated[str, Field(min_length=1, max_length=300)]
Count = Annotated[int, Field(ge=0)]


class TraceIdentity(TraceModel):
    item_id: TraceId
    revision: int = Field(ge=1)
    content_sha256: Sha256


class TraceCandidate(TraceModel):
    identity: TraceIdentity
    stage: Literal["retrieval", "fusion", "semantic_filter", "final_context"]
    rank: int = Field(ge=0)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    decision: Literal["retained", "rejected", "omitted"]
    reason: TraceCode | None = None


class TraceBudget(TraceModel):
    stage: Literal["routing", "planning", "semantic_filter", "generation"]
    limit_characters: int = Field(ge=0)
    used_characters: int = Field(ge=0)
    fallback: bool = False


class TraceRoutingDecision(TraceModel):
    route_id: TraceId
    reason: Literal[
        "selected",
        "no_match",
        "excluded",
        "gateway_uncertain",
        "unreviewed",
        "budget_exceeded",
    ]


class TraceEvidence(TraceModel):
    evidence_id: TraceId
    source_kind: Literal["chunk", "bibliographic_abstract", "article_abstract"]
    source_id: TraceId
    text_sha256: Sha256
    presented_text_sha256: Sha256
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section_path: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def coherent_locator(self) -> TraceEvidence:
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("evidence page bounds must be both present or absent")
        if self.page_start is not None and self.page_end is not None:
            if self.page_end < self.page_start:
                raise ValueError("evidence page_end cannot precede page_start")
            if self.section_path is not None:
                raise ValueError("page evidence cannot carry a structural section path")
        elif self.source_kind == "chunk" and not self.section_path:
            raise ValueError("chunk evidence requires a page or structural locator")
        return self


class TraceClaimLink(TraceModel):
    claim_id: TraceId
    evidence_ids: tuple[TraceId, ...] = Field(min_length=1, max_length=8)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("claim evidence IDs must be unique")
        return values


class TraceOutput(TraceModel):
    state: Literal["running", "succeeded", "failed", "cancelled", "incomplete"]
    response_sha256: Sha256 | None = None
    validation_codes: tuple[TraceCode, ...] = ()
    claim_links: tuple[TraceClaimLink, ...] = Field(default_factory=tuple, max_length=80)


class TraceCost(TraceModel):
    retrieval_requests: Count = 0
    llm_requests: Count = 0
    prompt_tokens: Count = 0
    completion_tokens: Count = 0
    duration_milliseconds: Count = 0
    cache_hits: Count = 0
    cache_misses: Count = 0


class ExpertRunManifest(TraceModel):
    """Bounded identity of one attempt, suitable for durable checkpointing."""

    schema_version: Literal[1] = 1
    run_id: UUID
    job_id: UUID
    attempt: int = Field(ge=0, le=3)
    mode: Literal["off", "shadow", "active"] = "off"
    release_id: UUID | None = None
    release_sha256: Sha256 | None = None
    recipe_version: str | None = Field(default=None, max_length=80)
    recipe_sha256: Sha256 | None = None
    code_revision: str = Field(min_length=1, max_length=200)
    question_sha256: Sha256
    user_context_sha256: Sha256
    answer_effort: AnswerEffort
    interaction_mode: Literal["research", "conversation"]
    configuration_sha256: Sha256
    corpus_fingerprint_before: Sha256 | None = None
    corpus_fingerprint_after: Sha256 | None = None
    sql_schema_version: int = Field(ge=0)
    index_fingerprints: tuple[Sha256, ...] = Field(default_factory=tuple, max_length=20)
    model_versions: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    prompt_template_hashes: tuple[Sha256, ...] = Field(default_factory=tuple, max_length=20)
    routing_items: tuple[TraceIdentity, ...] = Field(default_factory=tuple, max_length=20)
    routing_decisions: tuple[TraceRoutingDecision, ...] = Field(
        default_factory=tuple, max_length=40
    )
    candidates: tuple[TraceCandidate, ...] = Field(default_factory=tuple, max_length=300)
    budgets: tuple[TraceBudget, ...] = Field(default_factory=tuple, max_length=8)
    evidence: tuple[TraceEvidence, ...] = Field(default_factory=tuple, max_length=80)
    output: TraceOutput
    cost: TraceCost
    state: Literal["running", "succeeded", "failed", "cancelled", "incomplete"] = "running"
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def coherent_manifest(self) -> ExpertRunManifest:
        if (self.release_id is None) != (self.release_sha256 is None):
            raise ValueError("release identity must include both ID and hash")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("manifest evidence IDs must be unique")
        known_evidence = set(evidence_ids)
        for link in self.output.claim_links:
            if not set(link.evidence_ids) <= known_evidence:
                raise ValueError("claim link references an unknown evidence ID")
        if self.updated_at < self.created_at:
            raise ValueError("manifest updated_at cannot precede created_at")
        return self

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    def manifest_sha256(self) -> str:
        payload = json.dumps(
            self.canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(payload).hexdigest()
