from __future__ import annotations

import json

import pytest

from app.deep_research.models import ContextualSummaryResult
from app.deep_research.retrieval import DeepResearchSearchSnapshot
from app.evaluation.ciderqa import CiderQAQuestion, CiderQASplitDataset
from app.evaluation.contextual_adjudication import (
    build_contextual_adjudication,
    finalize_contextual_adjudication,
)
from app.evaluation.contextual_relevance import calibrate_contextual_threshold


def _dataset() -> CiderQASplitDataset:
    return CiderQASplitDataset(
        schema_version=1,
        split="development",
        questions=[
            CiderQAQuestion(
                schema_version=1,
                id=f"ciderqa-adjudication-{index:02d}",
                family_id=f"family-adjudication-{index:02d}",
                split="development",
                language="fr",
                task="abstention",
                question=f"Question cidricole numéro {index} ?",
                answerable=False,
            )
            for index in range(10)
        ],
    )


def _snapshots(dataset: CiderQASplitDataset) -> list[DeepResearchSearchSnapshot]:
    snapshots: list[DeepResearchSearchSnapshot] = []
    for question_index, question in enumerate(dataset.questions):
        summaries = [
            ContextualSummaryResult(
                text_sha256=f"{question_index * 2 + summary_index + 1:064x}",
                article_id=f"article-{question_index}",
                chunk_id=summary_index + 1,
                scope="common",
                page_start=summary_index + 1,
                page_end=summary_index + 1,
                summary=f"Résumé à faire juger {question_index}-{summary_index}.",
                relevance_score=0.8 if summary_index == 0 else 0.2,
                # These are model decisions and must never pre-fill expert labels.
                relevant=summary_index == 0,
            )
            for summary_index in range(2)
        ]
        snapshots.append(
            DeepResearchSearchSnapshot(
                query=question.question,
                scopes=["common"],
                hits=[],
                contextual_summary_attempted=True,
                contextual_summaries=summaries,
            )
        )
    return snapshots


def test_build_adjudication_never_copies_model_or_ciderqa_labels() -> None:
    dataset = _dataset()

    adjudication = build_contextual_adjudication(
        dataset,
        dataset_sha256="a" * 64,
        snapshots=_snapshots(dataset),
    )

    assert len(adjudication.items) == 20
    assert all(item.expert_relevant is None for item in adjudication.items)
    assert {item.relevance_score for item in adjudication.items} == {0.2, 0.8}


def test_finalize_rejects_incomplete_expert_review() -> None:
    dataset = _dataset()
    adjudication = build_contextual_adjudication(
        dataset,
        dataset_sha256="b" * 64,
        snapshots=_snapshots(dataset),
    )

    with pytest.raises(ValueError, match="every contextual adjudication item"):
        finalize_contextual_adjudication(adjudication)


def test_finalize_strips_review_text_and_produces_calibratable_observations() -> None:
    dataset = _dataset()
    adjudication = build_contextual_adjudication(
        dataset,
        dataset_sha256="c" * 64,
        snapshots=_snapshots(dataset),
    )
    for item in adjudication.items:
        item.expert_relevant = item.relevance_score >= 0.5

    observations = finalize_contextual_adjudication(adjudication)
    serialized = observations.model_dump_json()
    report = calibrate_contextual_threshold(observations)

    assert len(observations.observations) == 20
    assert len({item.question_id for item in observations.observations}) == 10
    assert "question" not in json.loads(serialized)["observations"][0]
    assert "generated_summary" not in serialized
    assert report.threshold == 0.8
    assert report.f1 == 1.0


def test_build_adjudication_rejects_non_development_split() -> None:
    dataset = _dataset().model_copy(update={"split": "validation"})

    with pytest.raises(ValueError, match="only CiderQA development"):
        build_contextual_adjudication(
            dataset,
            dataset_sha256="d" * 64,
            snapshots=[],
        )
