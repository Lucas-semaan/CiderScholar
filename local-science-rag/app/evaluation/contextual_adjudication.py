"""Local expert-adjudication package for contextual CiderQA calibration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.corpora import CorpusScope
from app.deep_research.retrieval import DeepResearchSearchSnapshot
from app.evaluation.ciderqa import CiderQASplitDataset
from app.evaluation.contextual_relevance import (
    ContextualRelevanceCalibrationSet,
    ContextualRelevanceObservation,
)


class ContextualAdjudicationItem(BaseModel):
    """Local review material; question and generated summary are removed at finalization."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^ciderqa-[a-z0-9][a-z0-9-]{2,79}$")
    question: str = Field(min_length=1, max_length=2_000)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: CorpusScope
    article_id: str = Field(min_length=1, max_length=200)
    chunk_id: int = Field(ge=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    generated_summary: str = Field(max_length=1_200)
    relevance_score: float = Field(ge=0.0, le=1.0)
    expert_relevant: bool | None = None


class ContextualAdjudicationSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    split: Literal["development"] = "development"
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[ContextualAdjudicationItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_items(self) -> ContextualAdjudicationSet:
        identities = {(item.question_id, item.text_sha256) for item in self.items}
        if len(identities) != len(self.items):
            raise ValueError("contextual adjudication items cannot be duplicated")
        return self


def _normalized_question(value: str) -> str:
    return " ".join(value.casefold().split())


def build_contextual_adjudication(
    dataset: CiderQASplitDataset,
    *,
    dataset_sha256: str,
    snapshots: list[DeepResearchSearchSnapshot],
) -> ContextualAdjudicationSet:
    """Match snapshots by question text without consulting any CiderQA answer label."""

    if dataset.split != "development":
        raise ValueError("contextual threshold calibration uses only CiderQA development")
    by_question: dict[str, tuple[str, str]] = {}
    for question in dataset.questions:
        normalized = _normalized_question(question.question)
        if normalized in by_question:
            raise ValueError("CiderQA development questions are ambiguous after normalization")
        by_question[normalized] = (question.id, question.question)
    items: dict[tuple[str, str], ContextualAdjudicationItem] = {}
    for snapshot in snapshots:
        matched = by_question.get(_normalized_question(snapshot.query))
        if matched is None:
            continue
        question_id, question_text = matched
        for summary in snapshot.contextual_summaries:
            item = ContextualAdjudicationItem(
                question_id=question_id,
                question=question_text,
                text_sha256=summary.text_sha256,
                scope=summary.scope,
                article_id=summary.article_id,
                chunk_id=summary.chunk_id,
                page_start=summary.page_start,
                page_end=summary.page_end,
                generated_summary=summary.summary,
                relevance_score=summary.relevance_score,
            )
            identity = (question_id, summary.text_sha256)
            existing = items.get(identity)
            if existing is not None and existing != item:
                raise ValueError("one adjudication identity has conflicting snapshot results")
            items[identity] = item
    if not items:
        raise ValueError("no contextual snapshot matches CiderQA development")
    ordered = sorted(
        items.values(),
        key=lambda item: (
            item.question_id,
            item.scope.value,
            item.article_id,
            item.chunk_id,
        ),
    )
    return ContextualAdjudicationSet(
        dataset_sha256=dataset_sha256,
        items=ordered,
    )


def finalize_contextual_adjudication(
    adjudication: ContextualAdjudicationSet,
) -> ContextualRelevanceCalibrationSet:
    """Strip local review text and return only scores, hashes and expert labels."""

    if any(item.expert_relevant is None for item in adjudication.items):
        raise ValueError("every contextual adjudication item requires an expert decision")
    return ContextualRelevanceCalibrationSet(
        dataset_sha256=adjudication.dataset_sha256,
        observations=[
            ContextualRelevanceObservation(
                question_id=item.question_id,
                text_sha256=item.text_sha256,
                relevance_score=item.relevance_score,
                expert_relevant=bool(item.expert_relevant),
            )
            for item in adjudication.items
        ],
    )


def write_contextual_adjudication(
    adjudication: ContextualAdjudicationSet,
    destination: str | Path,
) -> Path:
    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(adjudication.model_dump_json(indent=2) + "\n")
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return path
