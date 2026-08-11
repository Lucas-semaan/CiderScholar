"""User-visible, evidence-safe budgets for scientific chat answer effort."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AnswerEffort(StrEnum):
    """Closed public control for answer effort and bounded retrieval breadth."""

    CONCISE = "concise"
    BALANCED = "balanced"
    DEEP = "deep"


@dataclass(frozen=True, slots=True)
class AnswerEffortBudget:
    """One coherent budget; scientific validation is intentionally not optional."""

    max_query_variants: int
    abstract_result_limit: int
    article_count: int
    passages_per_article: int
    candidate_chunks_per_article: int
    context_radius: int
    evidence_record_limit: int
    max_evidence_items: int
    max_evidence_characters: int
    follow_up_query_limit: int
    follow_up_incomplete_axes: bool
    mono_max_statements: int
    mono_max_output_tokens: int
    facet_max_statements: int
    facet_max_output_tokens: int
    final_max_statements: int
    final_max_output_tokens: int


_BUDGETS: dict[AnswerEffort, AnswerEffortBudget] = {
    AnswerEffort.CONCISE: AnswerEffortBudget(
        max_query_variants=4,
        abstract_result_limit=12,
        article_count=6,
        passages_per_article=3,
        candidate_chunks_per_article=50,
        context_radius=1,
        evidence_record_limit=12,
        max_evidence_items=12,
        max_evidence_characters=24_000,
        follow_up_query_limit=4,
        follow_up_incomplete_axes=False,
        mono_max_statements=4,
        mono_max_output_tokens=3_072,
        facet_max_statements=2,
        facet_max_output_tokens=3_072,
        final_max_statements=8,
        final_max_output_tokens=4_096,
    ),
    AnswerEffort.BALANCED: AnswerEffortBudget(
        max_query_variants=8,
        abstract_result_limit=15,
        article_count=8,
        passages_per_article=4,
        candidate_chunks_per_article=75,
        context_radius=2,
        evidence_record_limit=16,
        max_evidence_items=20,
        max_evidence_characters=36_000,
        follow_up_query_limit=8,
        follow_up_incomplete_axes=True,
        mono_max_statements=8,
        mono_max_output_tokens=4_096,
        facet_max_statements=4,
        facet_max_output_tokens=4_096,
        final_max_statements=16,
        final_max_output_tokens=6_144,
    ),
    AnswerEffort.DEEP: AnswerEffortBudget(
        max_query_variants=8,
        abstract_result_limit=20,
        article_count=10,
        passages_per_article=6,
        candidate_chunks_per_article=100,
        context_radius=3,
        evidence_record_limit=20,
        max_evidence_items=20,
        max_evidence_characters=42_000,
        follow_up_query_limit=8,
        follow_up_incomplete_axes=True,
        mono_max_statements=12,
        mono_max_output_tokens=6_144,
        facet_max_statements=6,
        facet_max_output_tokens=4_096,
        final_max_statements=16,
        final_max_output_tokens=8_192,
    ),
}


def answer_effort_budget(effort: AnswerEffort | str) -> AnswerEffortBudget:
    """Return a validated immutable budget for a persisted public value."""

    return _BUDGETS[AnswerEffort(effort)]


def migrate_legacy_answer_effort(values: object) -> object:
    """Translate the retired answer-intensity field without weakening strict models."""

    if not isinstance(values, dict) or "answer_intensity" not in values:
        return values
    if "answer_effort" in values:
        raise ValueError("answer_effort and answer_intensity cannot be supplied together")
    return {key: value for key, value in values.items() if key != "answer_intensity"} | {
        "answer_effort": values["answer_intensity"]
    }
