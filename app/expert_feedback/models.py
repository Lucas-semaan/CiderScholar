"""Bounded proposal contracts. No submitted object can approve or activate itself."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.knowledge.contracts import ImmutableModel, ItemId, PositiveInt, Sha256, ShortText, unique
from app.knowledge.models import KnowledgeItem


class ChunkReference(ImmutableModel):
    kind: Literal["chunk"]
    corpus_id: ShortText
    article_id: ShortText
    chunk_id: PositiveInt
    page_start: PositiveInt
    page_end: PositiveInt
    content_sha256: Sha256

    @model_validator(mode="after")
    def ordered_pages(self) -> ChunkReference:
        if self.page_end < self.page_start:
            raise ValueError("page_end cannot precede page_start")
        return self


class BibliographicAbstractReference(ImmutableModel):
    kind: Literal["bibliographic_abstract"]
    corpus_id: ShortText
    record_id: ShortText
    content_sha256: Sha256


class ArticleAbstractReference(ImmutableModel):
    kind: Literal["article_abstract"]
    corpus_id: ShortText
    article_id: ShortText
    content_sha256: Sha256


EvidenceReference = Annotated[
    ChunkReference | BibliographicAbstractReference | ArticleAbstractReference,
    Field(discriminator="kind"),
]
Cause = Literal[
    "insufficient_trace",
    "source_changed",
    "corpus_gap",
    "index_gap",
    "knowledge_gap",
    "routing_error",
    "retrieval_error",
    "semantic_filter_error",
    "context_budget_error",
    "generation_method_error",
    "validator_bug",
    "expert_ambiguity",
    "runtime_failure",
]
CorrectionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)
]
CorrectionState = Literal[
    "submitted",
    "diagnosis_incomplete",
    "diagnosed",
    "needs_expert",
    "candidate_ready",
    "resolved",
    "rejected",
    "withdrawn",
]
CandidateState = Literal[
    "draft",
    "structurally_valid",
    "evaluating",
    "awaiting_review",
    "approved",
    "activated",
    "needs_expert",
    "evaluation_failed",
    "rejected",
    "superseded",
    "inconclusive",
]


class ExpertCorrectionCreate(ImmutableModel):
    schema_version: Literal[1] = 1
    client_request_id: UUID
    manifest_id: UUID | None = None
    claim_id: ShortText | None = None
    selected_text: str = Field(default="", max_length=2000)
    problem: CorrectionText
    proposed_correction: CorrectionText
    scope: Literal["this_answer", "reusable_method"]
    evidence_refs: Annotated[tuple[EvidenceReference, ...], Field(max_length=10)] = ()
    suggested_category: Cause | None = None

    @model_validator(mode="after")
    def identifiable_claim(self) -> ExpertCorrectionCreate:
        if self.claim_id is not None and self.manifest_id is None:
            raise ValueError("claim_id requires a manifest")
        unique(self.evidence_refs, "evidence references")
        return self


class Diagnosis(ImmutableModel):
    schema_version: Literal[1] = 1
    correction_id: UUID
    correction_revision: PositiveInt
    primary_cause: Cause
    contributing_causes: Annotated[tuple[Cause, ...], Field(max_length=12)] = ()
    observed_ids: Annotated[tuple[ShortText, ...], Field(max_length=100)] = ()
    verified_hashes: Annotated[tuple[Sha256, ...], Field(max_length=100)] = ()
    rationale: CorrectionText
    missing_information: Annotated[tuple[ShortText, ...], Field(max_length=20)] = ()
    confidence: Literal["supported", "uncertain"]
    target_item_ids: Annotated[tuple[ItemId, ...], Field(max_length=3)] = ()
    proposed_action: Literal[
        "knowledge_candidate",
        "engineering_issue",
        "acquisition_proposal",
        "expert_review",
        "no_change",
    ]

    @model_validator(mode="after")
    def compilable_only_with_supported_diagnosis(self) -> Diagnosis:
        unique(self.contributing_causes, "contributing causes")
        unique(self.target_item_ids, "target IDs")
        if self.proposed_action == "knowledge_candidate" and (
            self.confidence != "supported"
            or not self.target_item_ids
            or not self.observed_ids
            or not self.verified_hashes
            or self.missing_information
            or set(self.contributing_causes)
            & {"insufficient_trace", "source_changed", "expert_ambiguity"}
            or self.primary_cause
            not in {
                "knowledge_gap",
                "routing_error",
                "semantic_filter_error",
                "generation_method_error",
            }
        ):
            raise ValueError("knowledge candidate requires a supported, traceable method diagnosis")
        return self


class PatchOperation(ImmutableModel):
    operation: Literal["add_item", "replace_item", "retire_item"]
    item_id: ItemId
    expected_sha256: Sha256 | None = None
    item: KnowledgeItem | None = None
    justification: CorrectionText

    @model_validator(mode="after")
    def exact_operation_shape(self) -> PatchOperation:
        if (self.operation == "add_item") != (self.expected_sha256 is None):
            raise ValueError("existing item operations require an expected hash")
        if (self.operation == "retire_item") != (self.item is None):
            raise ValueError("add/replace require an item; retire forbids an item")
        if self.item is not None and (
            self.item.id != self.item_id
            or self.item.required
            or self.item.authority != "proposal"
            or self.item.kind not in {"taxonomy", "route", "method_policy"}
        ):
            raise ValueError("automatic patches may only propose optional lexical or method items")
        return self


class CandidatePatch(ImmutableModel):
    schema_version: Literal[1] = 1
    base_release_id: UUID
    diagnosis_sha256: Sha256
    operations: Annotated[tuple[PatchOperation, ...], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def distinct_operations(self) -> CandidatePatch:
        unique(tuple(operation.item_id for operation in self.operations), "patch targets")
        return self


class ExpertReviewCreate(ImmutableModel):
    client_request_id: UUID
    candidate_sha256: Sha256
    evaluation_sha256: Sha256
    decision: Literal["approve", "reject", "needs_changes"]
    reviewer_label: ShortText
    reason: CorrectionText


class ExpertActivationRequest(ImmutableModel):
    client_request_id: UUID
    review_id: UUID
    candidate_sha256: Sha256
    evaluation_sha256: Sha256
    expected_active_generation: int = Field(strict=True, ge=0)


class ImprovementBudget(ImmutableModel):
    """Explicit, independent budgets; defaults authorize no provider request."""

    diagnosis: int = Field(default=0, strict=True, ge=0, le=1000)
    compilation: int = Field(default=0, strict=True, ge=0, le=1000)
    review: int = Field(default=0, strict=True, ge=0, le=1000)
    evaluation: int = Field(default=0, strict=True, ge=0, le=10000)
