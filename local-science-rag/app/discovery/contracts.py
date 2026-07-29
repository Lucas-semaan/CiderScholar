"""Strict hypothesis cards derived only from validated deep-research evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def content_hash(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class HypothesisPremise(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=10, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("premise evidence ids must be unique")
        return values


class DiscriminatingExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principle: str = Field(min_length=20, max_length=1500)
    discriminating_outcome: str = Field(min_length=20, max_length=1500)
    safety_review_required: Literal[True] = True
    executable_protocol: Literal[False] = False

    @field_validator("principle", "discriminating_outcome")
    @classmethod
    def prohibit_direct_protocol(cls, value: str) -> str:
        procedural = re.compile(
            r"(?:\b\d+(?:[.,]\d+)?\s*(?:ml|µl|ul|g|mg|°c|rpm|min(?:ute)?s?)\b|"
            r"\b(?:ajoutez|incubez|chauffez|mélangez|inoculez|centrifugez)\b)",
            re.IGNORECASE,
        )
        if procedural.search(value):
            raise ValueError("directly executable laboratory instructions are forbidden")
        return value


class HypothesisCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: UUID
    question: str = Field(min_length=10, max_length=4000)
    premises: list[HypothesisPremise] = Field(min_length=1, max_length=20)
    contradictions: list[str] = Field(min_length=1, max_length=20)
    uncertainties: list[str] = Field(min_length=1, max_length=20)
    explicit_gaps: list[str] = Field(min_length=1, max_length=20)
    testable_prediction: str = Field(min_length=20, max_length=2000)
    discriminating_experiment: DiscriminatingExperiment
    parent_hypothesis_id: UUID | None = None
    source_analysis_ids: list[UUID] = Field(default_factory=list, max_length=20)
    experimental_dataset_ids: list[UUID] = Field(default_factory=list, max_length=20)
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("hypothesis creation time must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def question_hash_matches(self) -> HypothesisCard:
        if hashlib.sha256(self.question.encode("utf-8")).hexdigest() != self.question_sha256:
            raise ValueError("hypothesis question hash does not match")
        return self


class HypothesisDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    premises: list[HypothesisPremise] = Field(min_length=1, max_length=20)
    contradictions: list[str] = Field(min_length=1, max_length=20)
    uncertainties: list[str] = Field(min_length=1, max_length=20)
    explicit_gaps: list[str] = Field(min_length=1, max_length=20)
    testable_prediction: str = Field(min_length=20, max_length=2000)
    discriminating_experiment: DiscriminatingExperiment


def build_hypothesis_card(
    *,
    question: str,
    draft: HypothesisDraft,
    validated_evidence_ids: set[str],
    corpus_sha256: str,
    model_sha256: str,
    prompt_sha256: str,
    hypothesis_id: UUID | None = None,
    parent_hypothesis_id: UUID | None = None,
    source_analysis_ids: list[UUID] | None = None,
    experimental_dataset_ids: list[UUID] | None = None,
    created_at: datetime | None = None,
) -> HypothesisCard:
    cleaned_question = " ".join(question.split())
    cited = {evidence_id for premise in draft.premises for evidence_id in premise.evidence_ids}
    unknown = cited.difference(validated_evidence_ids)
    if unknown:
        raise ValueError(f"hypothesis premise uses unvalidated evidence ids: {sorted(unknown)}")
    if not cited:
        raise ValueError("hypothesis requires validated evidence")
    return HypothesisCard(
        id=hypothesis_id or uuid4(),
        question=cleaned_question,
        **draft.model_dump(),
        parent_hypothesis_id=parent_hypothesis_id,
        source_analysis_ids=source_analysis_ids or [],
        experimental_dataset_ids=experimental_dataset_ids or [],
        question_sha256=hashlib.sha256(cleaned_question.encode("utf-8")).hexdigest(),
        corpus_sha256=corpus_sha256,
        evidence_sha256=content_hash(sorted(cited)),
        model_sha256=model_sha256,
        prompt_sha256=prompt_sha256,
        created_at=created_at or datetime.now(UTC),
    )


class HumanHypothesisReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["retain", "reject"]
    expert_reference: str = Field(min_length=3, max_length=200)
    comment: str | None = Field(default=None, max_length=2000)
    created_at: datetime


DISCOVERY_SCOPE = {
    "role": "assistance à la formulation et au classement d’hypothèses",
    "forbidden_claims": (
        "validation expérimentale",
        "recommandation autonome",
        "protocole de laboratoire directement exécutable",
    ),
    "required_human_gates": (
        "rétention d’une hypothèse",
        "exécution de code généré",
        "démarrage du cycle suivant",
    ),
}
