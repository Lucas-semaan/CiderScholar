"""Structural readiness audit for a real, expert-authored CiderQA dataset."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.evaluation.ciderqa import CiderQAQuestion, CiderQASplit, CiderQASplitDataset


class CiderQAReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    structurally_ready: bool
    expert_validation_required: Literal[True] = True
    question_count: int
    split_counts: dict[str, int]
    language_counts: dict[str, int]
    full_text_question_count: int
    unanswerable_question_count: int
    multi_source_question_count: int
    failures: list[str]


def _is_full_text(question: CiderQAQuestion) -> bool:
    return any(item.kind in {"body", "table", "figure"} for item in question.reference_evidence)


def _is_multi_source(question: CiderQAQuestion) -> bool:
    article_ids = {item.article_id for item in question.reference_evidence}
    return (
        question.task in {"comparison", "multi_article", "contradiction"} or len(article_ids) >= 2
    )


def assess_ciderqa_readiness(
    datasets: Mapping[CiderQASplit, CiderQASplitDataset],
) -> CiderQAReadinessReport:
    questions = [question for dataset in datasets.values() for question in dataset.questions]
    split_counts = {split: len(dataset.questions) for split, dataset in datasets.items()}
    language_counts = Counter(question.language for question in questions)
    full_text_count = sum(_is_full_text(question) for question in questions)
    unanswerable_count = sum(not question.answerable for question in questions)
    multi_source_count = sum(_is_multi_source(question) for question in questions)
    total = len(questions)
    failures: list[str] = []
    if set(datasets) != {"development", "validation", "final_test"}:
        failures.append("required_splits_missing")
    if total < 100:
        failures.append("question_count_below_100")
    if full_text_count < 25:
        failures.append("full_text_question_count_below_25")
    if unanswerable_count < 15:
        failures.append("unanswerable_question_count_below_15")
    if multi_source_count < 20:
        failures.append("multi_source_question_count_below_20")
    if total:
        french_share = language_counts["fr"] / total
        english_share = language_counts["en"] / total
        if not 0.45 <= french_share <= 0.55 or not 0.45 <= english_share <= 0.55:
            failures.append("language_balance_outside_45_55_percent")
    return CiderQAReadinessReport(
        structurally_ready=not failures,
        question_count=total,
        split_counts=split_counts,
        language_counts=dict(language_counts),
        full_text_question_count=full_text_count,
        unanswerable_question_count=unanswerable_count,
        multi_source_question_count=multi_source_count,
        failures=failures,
    )
