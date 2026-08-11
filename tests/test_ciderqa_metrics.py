from __future__ import annotations

import pytest

from app.evaluation.ciderqa import CiderQAQuestion, CiderQASplitDataset
from app.evaluation.ciderqa_metrics import (
    CiderQACitationAssessment,
    CiderQAClaimAssessment,
    CiderQAInferenceResult,
    bootstrap_estimate,
    evaluate_ciderqa_results,
)


def _answerable() -> CiderQAQuestion:
    return CiderQAQuestion.model_validate(
        {
            "schema_version": 1,
            "id": "ciderqa-answerable",
            "family_id": "family-answerable",
            "split": "validation",
            "language": "fr",
            "task": "direct",
            "question": "Quels résultats sont observés ?",
            "answerable": True,
            "expected_answer": "La teneur diminue et la couleur reste stable.",
            "expected_claims": ["La teneur diminue.", "La couleur reste stable."],
            "reference_evidence": [
                {
                    "id": "evidence-one",
                    "notice_id": "notice-1",
                    "article_id": "article-1",
                    "fragment_id": "fragment-1",
                    "article_sha256": "a" * 64,
                    "kind": "body",
                    "page_start": 4,
                    "page_end": 4,
                    "excerpt": "La teneur diminue ; la couleur reste stable.",
                }
            ],
        }
    )


def _unanswerable() -> CiderQAQuestion:
    return CiderQAQuestion.model_validate(
        {
            "schema_version": 1,
            "id": "ciderqa-unanswerable",
            "family_id": "family-unanswerable",
            "split": "validation",
            "language": "en",
            "task": "abstention",
            "question": "What is not present in the corpus?",
            "answerable": False,
        }
    )


def test_ciderqa_metrics_separate_levels_factuality_citations_and_abstention() -> None:
    dataset = CiderQASplitDataset(
        schema_version=1,
        split="validation",
        questions=[_answerable(), _unanswerable()],
    )
    result = CiderQAInferenceResult(
        question_id="ciderqa-answerable",
        answered=True,
        insufficiency_score=0.1,
        ranked_notice_ids=["notice-1"],
        ranked_article_ids=["article-1"],
        ranked_fragment_ids=["fragment-1"],
        claims=[
            CiderQAClaimAssessment(
                text="La teneur diminue.",
                factually_correct=True,
                expected_claim_indexes=[0],
                citations=[
                    CiderQACitationAssessment(
                        evidence_id="evidence-one", entailed=True, page_exact=True
                    )
                ],
            ),
            CiderQAClaimAssessment(
                text="La couleur augmente.",
                factually_correct=False,
                expected_claim_indexes=[1],
                citations=[
                    CiderQACitationAssessment(
                        evidence_id="evidence-one", entailed=False, page_exact=True
                    )
                ],
            ),
        ],
    )
    abstention = CiderQAInferenceResult(
        question_id="ciderqa-unanswerable",
        answered=False,
        insufficiency_score=0.9,
    )

    report = evaluate_ciderqa_results(dataset, [result, abstention])

    assert report.notice_retrieval.recall_at_20.value == 1.0
    assert report.notice_retrieval.recall_at_10 is not None
    assert report.notice_retrieval.recall_at_10.value == 1.0
    assert report.notice_retrieval.recall_at_50 is not None
    assert report.notice_retrieval.recall_at_50.value == 1.0
    assert report.article_retrieval.mean_reciprocal_rank.value == 1.0
    assert report.fragment_retrieval.ndcg_at_20.value == 1.0
    assert report.fragment_retrieval.ndcg_at_10 is not None
    assert report.fragment_retrieval.ndcg_at_50 is not None
    assert report.exactness.value == 0.5
    assert report.completeness.value == 0.5
    assert report.citation_precision.value == 1.0
    assert report.citation_recall.value == 0.5
    assert report.entailment_rate.value == 0.5
    assert report.page_accuracy.value == 1.0
    assert report.abstention_sensitivity.value == 1.0
    assert report.abstention_specificity.value == 1.0
    assert report.false_refusal_rate.value == 0.0
    assert report.insufficiency_brier_score.value == pytest.approx(0.01)
    assert report.numeric_faithfulness is None
    assert report.numeric_assessment_coverage is None


def test_ciderqa_metrics_report_multi_k_retrieval_and_numeric_faithfulness() -> None:
    dataset = CiderQASplitDataset(
        schema_version=1,
        split="validation",
        questions=[_answerable(), _unanswerable()],
    )
    result = CiderQAInferenceResult(
        question_id="ciderqa-answerable",
        answered=True,
        insufficiency_score=0.1,
        ranked_notice_ids=[*[f"other-notice-{index}" for index in range(14)], "notice-1"],
        ranked_article_ids=[*[f"other-article-{index}" for index in range(14)], "article-1"],
        ranked_fragment_ids=[*[f"other-fragment-{index}" for index in range(14)], "fragment-1"],
        claims=[
            CiderQAClaimAssessment(
                text="La teneur diminue de 15 %.",
                factually_correct=True,
                expected_claim_indexes=[0],
                numeric_faithfulness="faithful",
                citations=[
                    CiderQACitationAssessment(
                        evidence_id="evidence-one", entailed=True, page_exact=True
                    )
                ],
            ),
            CiderQAClaimAssessment(
                text="La couleur augmente de 4 %.",
                factually_correct=False,
                expected_claim_indexes=[1],
                numeric_faithfulness="unfaithful",
                citations=[
                    CiderQACitationAssessment(
                        evidence_id="evidence-one", entailed=False, page_exact=True
                    )
                ],
            ),
        ],
    )
    abstention = CiderQAInferenceResult(
        question_id="ciderqa-unanswerable",
        answered=False,
        insufficiency_score=0.9,
    )

    report = evaluate_ciderqa_results(dataset, [result, abstention])

    assert report.article_retrieval.recall_at_10 is not None
    assert report.article_retrieval.recall_at_10.value == 0.0
    assert report.article_retrieval.recall_at_20.value == 1.0
    assert report.article_retrieval.recall_at_50 is not None
    assert report.article_retrieval.recall_at_50.value == 1.0
    assert report.numeric_faithfulness is not None
    assert report.numeric_faithfulness.value == 0.5
    assert report.numeric_assessment_coverage is not None
    assert report.numeric_assessment_coverage.value == 1.0


def test_bootstrap_interval_is_deterministic_and_contains_observed_mean() -> None:
    first = bootstrap_estimate([0.0, 0.5, 1.0], seed=42, samples=500)
    second = bootstrap_estimate([0.0, 0.5, 1.0], seed=42, samples=500)

    assert first == second
    assert first.ci_low <= first.value <= first.ci_high
