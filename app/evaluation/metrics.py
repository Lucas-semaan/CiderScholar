"""Deterministic retrieval, concept, and citation traceability metrics."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field


class RankingMetrics(BaseModel):
    """Article-level metrics for one ranked result list."""

    model_config = ConfigDict(extra="forbid")

    precision_at_k: float = Field(ge=0.0, le=1.0)
    recall_at_k: float = Field(ge=0.0, le=1.0)
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
    ndcg_at_k: float = Field(ge=0.0, le=1.0)
    relevant_hits: int = Field(ge=0)
    expected_hits: int = Field(ge=0)


class TraceabilityMetrics(BaseModel):
    """Counts and rates for application-level evidence references."""

    model_config = ConfigDict(extra="forbid")

    evaluated_synthesis_count: int = Field(default=0, ge=0)
    total_citations: int = Field(default=0, ge=0)
    traceable_citations: int = Field(default=0, ge=0)
    total_assertions: int = Field(default=0, ge=0)
    unsupported_assertions: int = Field(default=0, ge=0)
    traceable_citation_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    unsupported_assertion_rate: float | None = Field(default=None, ge=0.0, le=1.0)


def _deduplicated(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def ranking_metrics(
    ranked_article_ids: Sequence[str],
    expected_article_ids: Sequence[str],
    acceptable_article_ids: Sequence[str] = (),
    *,
    k: int = 20,
) -> RankingMetrics:
    """Compute P@k, expected recall, MRR, and graded nDCG.

    Expected articles have relevance grade 2 and acceptable alternatives grade 1. Precision and
    reciprocal rank treat both grades as relevant; recall measures the mandatory expected set.
    Duplicate ranked identifiers are ignored after their first occurrence.
    """

    if k < 1:
        raise ValueError("k must be positive")
    expected = set(_deduplicated(expected_article_ids))
    if not expected:
        raise ValueError("at least one expected article is required")
    acceptable = set(_deduplicated(acceptable_article_ids)).difference(expected)
    relevant = expected.union(acceptable)
    ranked = _deduplicated(ranked_article_ids)[:k]

    relevant_hits = sum(article_id in relevant for article_id in ranked)
    expected_hits = sum(article_id in expected for article_id in ranked)
    reciprocal_rank = next(
        (1.0 / rank for rank, article_id in enumerate(ranked, start=1) if article_id in relevant),
        0.0,
    )

    def gain(article_id: str) -> int:
        if article_id in expected:
            return 2
        return 1 if article_id in acceptable else 0

    dcg = sum(
        (2 ** gain(article_id) - 1) / math.log2(rank + 1)
        for rank, article_id in enumerate(ranked, start=1)
        if gain(article_id)
    )
    ideal_grades = ([2] * len(expected) + [1] * len(acceptable))[:k]
    ideal_dcg = sum(
        (2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal_grades, start=1)
    )
    return RankingMetrics(
        precision_at_k=relevant_hits / k,
        recall_at_k=expected_hits / len(expected),
        mean_reciprocal_rank=reciprocal_rank,
        ndcg_at_k=dcg / ideal_dcg if ideal_dcg else 0.0,
        relevant_hits=relevant_hits,
        expected_hits=expected_hits,
    )


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    alphanumeric = "".join(
        character if character.isalnum() else " " for character in without_accents
    )
    return " ".join(alphanumeric.split())


def concept_recall(expected_concepts: Sequence[str], observed_texts: Iterable[str]) -> float:
    """Return the fraction of expected concepts present in retrieved article material."""

    concepts = _deduplicated([_normalized(value) for value in expected_concepts])
    if not concepts:
        return 1.0
    haystack = f" {_normalized(' '.join(observed_texts))} "
    found = sum(f" {concept} " in haystack for concept in concepts)
    return found / len(concepts)


def traceability_metrics(
    statement_evidence_ids: Iterable[Sequence[str]],
    allowed_evidence_ids: Iterable[str],
) -> TraceabilityMetrics:
    """Measure whether every citation and factual statement maps to allowed SQLite evidence."""

    allowed = set(allowed_evidence_ids)
    statements = [list(values) for values in statement_evidence_ids]
    total_citations = sum(len(values) for values in statements)
    traceable_citations = sum(
        evidence_id in allowed for values in statements for evidence_id in values
    )
    unsupported_assertions = sum(
        not values or any(evidence_id not in allowed for evidence_id in values)
        for values in statements
    )
    return TraceabilityMetrics(
        evaluated_synthesis_count=1,
        total_citations=total_citations,
        traceable_citations=traceable_citations,
        total_assertions=len(statements),
        unsupported_assertions=unsupported_assertions,
        traceable_citation_rate=(
            traceable_citations / total_citations if total_citations else None
        ),
        unsupported_assertion_rate=(
            unsupported_assertions / len(statements) if statements else None
        ),
    )


def combine_traceability(values: Sequence[TraceabilityMetrics]) -> TraceabilityMetrics:
    """Aggregate traceability counts without averaging per-query rates."""

    total_citations = sum(value.total_citations for value in values)
    traceable_citations = sum(value.traceable_citations for value in values)
    total_assertions = sum(value.total_assertions for value in values)
    unsupported_assertions = sum(value.unsupported_assertions for value in values)
    return TraceabilityMetrics(
        evaluated_synthesis_count=sum(value.evaluated_synthesis_count for value in values),
        total_citations=total_citations,
        traceable_citations=traceable_citations,
        total_assertions=total_assertions,
        unsupported_assertions=unsupported_assertions,
        traceable_citation_rate=(
            traceable_citations / total_citations if total_citations else None
        ),
        unsupported_assertion_rate=(
            unsupported_assertions / total_assertions if total_assertions else None
        ),
    )
