"""Post-inference CiderQA metrics with deterministic bootstrap intervals."""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.ciderqa import CiderQAQuestion, CiderQASplitDataset
from app.evaluation.metrics import RankingMetrics, ranking_metrics
from app.models.chatbot import ChatbotRetrievalTrace, ChatbotTiming

NUMERIC_TOKEN_PATTERN = re.compile(r"(?<!\w)[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)(?!\w)")
NumericFaithfulnessAssessment = Literal["not_assessed", "not_applicable", "faithful", "unfaithful"]


class CiderQACitationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, max_length=100)
    entailed: bool
    page_exact: bool


class CiderQAClaimAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    factually_correct: bool
    expected_claim_indexes: list[int] = Field(default_factory=list, max_length=20)
    citations: list[CiderQACitationAssessment] = Field(default_factory=list, max_length=20)
    numeric_faithfulness: NumericFaithfulnessAssessment = "not_assessed"


class CiderQAInferenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    answered: bool
    insufficiency_score: float = Field(ge=0.0, le=1.0)
    ranked_notice_ids: list[str] = Field(default_factory=list, max_length=100)
    ranked_article_ids: list[str] = Field(default_factory=list, max_length=100)
    ranked_fragment_ids: list[str] = Field(default_factory=list, max_length=200)
    claims: list[CiderQAClaimAssessment] = Field(default_factory=list, max_length=50)
    retrieval_traces: list[ChatbotRetrievalTrace] = Field(default_factory=list, max_length=40)
    timings: list[ChatbotTiming] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def answer_matches_claims(self) -> CiderQAInferenceResult:
        if self.answered and not self.claims:
            raise ValueError("an answered CiderQA result requires assessed claims")
        if not self.answered and self.claims:
            raise ValueError("an abstained CiderQA result cannot contain claims")
        return self


class MetricEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float = Field(ge=0.0, le=1.0)
    ci_low: float = Field(ge=0.0, le=1.0)
    ci_high: float = Field(ge=0.0, le=1.0)
    sample_count: int = Field(ge=1)


class RetrievalEstimates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recall_at_20: MetricEstimate
    mean_reciprocal_rank: MetricEstimate
    ndcg_at_20: MetricEstimate
    recall_at_10: MetricEstimate | None = None
    recall_at_50: MetricEstimate | None = None
    ndcg_at_10: MetricEstimate | None = None
    ndcg_at_50: MetricEstimate | None = None


class CiderQACaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    answerable: bool
    answered: bool
    notice: RankingMetrics | None
    article: RankingMetrics | None
    fragment: RankingMetrics | None
    notice_at_10: RankingMetrics | None = None
    notice_at_50: RankingMetrics | None = None
    article_at_10: RankingMetrics | None = None
    article_at_50: RankingMetrics | None = None
    fragment_at_10: RankingMetrics | None = None
    fragment_at_50: RankingMetrics | None = None
    exactness: float | None = Field(default=None, ge=0.0, le=1.0)
    completeness: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    entailment_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    page_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    numeric_faithfulness: float | None = Field(default=None, ge=0.0, le=1.0)
    numeric_assessment_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    abstention_correct: bool
    insufficiency_score: float = Field(ge=0.0, le=1.0)


class CiderQAMetricsReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count: int = Field(ge=1)
    notice_retrieval: RetrievalEstimates
    article_retrieval: RetrievalEstimates
    fragment_retrieval: RetrievalEstimates
    exactness: MetricEstimate
    completeness: MetricEstimate
    citation_precision: MetricEstimate
    citation_recall: MetricEstimate
    entailment_rate: MetricEstimate
    page_accuracy: MetricEstimate
    abstention_sensitivity: MetricEstimate
    abstention_specificity: MetricEstimate
    false_refusal_rate: MetricEstimate
    insufficiency_brier_score: MetricEstimate
    cases: list[CiderQACaseMetrics]
    numeric_faithfulness: MetricEstimate | None = None
    numeric_assessment_coverage: MetricEstimate | None = None


def bootstrap_estimate(
    values: Sequence[float], *, seed: int = 1729, samples: int = 2000
) -> MetricEstimate:
    if not values:
        raise ValueError("a metric estimate requires at least one observation")
    if samples < 100:
        raise ValueError("bootstrap samples must be at least 100")
    observed = [float(value) for value in values]
    mean = sum(observed) / len(observed)
    if len(observed) == 1:
        return MetricEstimate(value=mean, ci_low=mean, ci_high=mean, sample_count=len(observed))
    generator = random.Random(seed)
    bootstrapped = sorted(
        sum(generator.choice(observed) for _ in observed) / len(observed) for _ in range(samples)
    )
    low_index = int(0.025 * (samples - 1))
    high_index = int(0.975 * (samples - 1))
    return MetricEstimate(
        value=mean,
        ci_low=bootstrapped[low_index],
        ci_high=bootstrapped[high_index],
        sample_count=len(observed),
    )


def _case_metrics(
    question: CiderQAQuestion,
    result: CiderQAInferenceResult,
) -> CiderQACaseMetrics:
    if question.id != result.question_id:
        raise ValueError("CiderQA inference result does not match its question")
    abstention_correct = result.answered == question.answerable
    if not question.answerable:
        return CiderQACaseMetrics(
            question_id=question.id,
            answerable=False,
            answered=result.answered,
            notice=None,
            article=None,
            fragment=None,
            abstention_correct=abstention_correct,
            insufficiency_score=result.insufficiency_score,
        )

    expected_notices = list(
        dict.fromkeys(evidence.notice_id for evidence in question.reference_evidence)
    )
    expected_articles = list(
        dict.fromkeys(evidence.article_id for evidence in question.reference_evidence)
    )
    expected_fragments = list(
        dict.fromkeys(evidence.fragment_id for evidence in question.reference_evidence)
    )
    allowed_evidence = {evidence.id for evidence in question.reference_evidence}
    expected_indexes = set(range(len(question.expected_claims)))
    supported_indexes: set[int] = set()
    cited_indexes: set[int] = set()
    citations = [citation for claim in result.claims for citation in claim.citations]
    for claim in result.claims:
        invalid = set(claim.expected_claim_indexes).difference(expected_indexes)
        if invalid:
            raise ValueError("claim assessment refers to an unknown expected claim")
        if claim.factually_correct:
            supported_indexes.update(claim.expected_claim_indexes)
        if claim.factually_correct and any(
            citation.evidence_id in allowed_evidence and citation.entailed
            for citation in claim.citations
        ):
            cited_indexes.update(claim.expected_claim_indexes)
    traceable = sum(citation.evidence_id in allowed_evidence for citation in citations)
    entailed = sum(
        citation.evidence_id in allowed_evidence and citation.entailed for citation in citations
    )
    exact_pages = sum(
        citation.evidence_id in allowed_evidence and citation.page_exact for citation in citations
    )
    citation_count = len(citations)
    claim_count = len(result.claims)
    numeric_candidates = [
        claim
        for claim in result.claims
        if NUMERIC_TOKEN_PATTERN.search(claim.text)
        or claim.numeric_faithfulness in {"faithful", "unfaithful"}
    ]
    assessed_numeric_claims = [
        claim
        for claim in numeric_candidates
        if claim.numeric_faithfulness in {"faithful", "unfaithful"}
    ]
    return CiderQACaseMetrics(
        question_id=question.id,
        answerable=True,
        answered=result.answered,
        notice=ranking_metrics(result.ranked_notice_ids, expected_notices, k=20),
        article=ranking_metrics(result.ranked_article_ids, expected_articles, k=20),
        fragment=ranking_metrics(result.ranked_fragment_ids, expected_fragments, k=20),
        notice_at_10=ranking_metrics(result.ranked_notice_ids, expected_notices, k=10),
        notice_at_50=ranking_metrics(result.ranked_notice_ids, expected_notices, k=50),
        article_at_10=ranking_metrics(result.ranked_article_ids, expected_articles, k=10),
        article_at_50=ranking_metrics(result.ranked_article_ids, expected_articles, k=50),
        fragment_at_10=ranking_metrics(result.ranked_fragment_ids, expected_fragments, k=10),
        fragment_at_50=ranking_metrics(result.ranked_fragment_ids, expected_fragments, k=50),
        exactness=(
            sum(claim.factually_correct for claim in result.claims) / claim_count
            if claim_count
            else 0.0
        ),
        completeness=len(supported_indexes) / len(expected_indexes),
        citation_precision=traceable / citation_count if citation_count else 0.0,
        citation_recall=len(cited_indexes) / len(expected_indexes),
        entailment_rate=entailed / citation_count if citation_count else 0.0,
        page_accuracy=exact_pages / citation_count if citation_count else 0.0,
        numeric_faithfulness=(
            sum(claim.numeric_faithfulness == "faithful" for claim in assessed_numeric_claims)
            / len(assessed_numeric_claims)
            if assessed_numeric_claims
            else None
        ),
        numeric_assessment_coverage=(
            len(assessed_numeric_claims) / len(numeric_candidates) if numeric_candidates else None
        ),
        abstention_correct=abstention_correct,
        insufficiency_score=result.insufficiency_score,
    )


def _retrieval_estimates(cases: Sequence[CiderQACaseMetrics], field: str) -> RetrievalEstimates:
    rankings = [getattr(case, field) for case in cases]
    present = [value for value in rankings if value is not None]
    rankings_at_10 = [getattr(case, f"{field}_at_10") for case in cases]
    rankings_at_50 = [getattr(case, f"{field}_at_50") for case in cases]
    present_at_10 = [value for value in rankings_at_10 if value is not None]
    present_at_50 = [value for value in rankings_at_50 if value is not None]
    return RetrievalEstimates(
        recall_at_20=bootstrap_estimate([value.recall_at_k for value in present]),
        mean_reciprocal_rank=bootstrap_estimate([value.mean_reciprocal_rank for value in present]),
        ndcg_at_20=bootstrap_estimate([value.ndcg_at_k for value in present]),
        recall_at_10=bootstrap_estimate([value.recall_at_k for value in present_at_10]),
        recall_at_50=bootstrap_estimate([value.recall_at_k for value in present_at_50]),
        ndcg_at_10=bootstrap_estimate([value.ndcg_at_k for value in present_at_10]),
        ndcg_at_50=bootstrap_estimate([value.ndcg_at_k for value in present_at_50]),
    )


def _present_values(cases: Sequence[CiderQACaseMetrics], field: str) -> list[float]:
    return [float(value) for case in cases if (value := getattr(case, field)) is not None]


def _optional_estimate(cases: Sequence[CiderQACaseMetrics], field: str) -> MetricEstimate | None:
    values = _present_values(cases, field)
    return bootstrap_estimate(values) if values else None


def evaluate_ciderqa_results(
    dataset: CiderQASplitDataset,
    results: Sequence[CiderQAInferenceResult],
) -> CiderQAMetricsReport:
    by_id = {result.question_id: result for result in results}
    expected_ids = {question.id for question in dataset.questions}
    if set(by_id) != expected_ids or len(by_id) != len(results):
        raise ValueError("CiderQA results must match every question exactly once")
    cases = [_case_metrics(question, by_id[question.id]) for question in dataset.questions]
    answerable = [case for case in cases if case.answerable]
    unanswerable = [case for case in cases if not case.answerable]
    if not answerable or not unanswerable:
        raise ValueError("CiderQA metrics require answerable and unanswerable strata")
    specificity_values = [float(case.answered) for case in answerable]
    sensitivity_values = [float(not case.answered) for case in unanswerable]
    brier_values = [(case.insufficiency_score - float(not case.answerable)) ** 2 for case in cases]
    return CiderQAMetricsReport(
        case_count=len(cases),
        notice_retrieval=_retrieval_estimates(cases, "notice"),
        article_retrieval=_retrieval_estimates(cases, "article"),
        fragment_retrieval=_retrieval_estimates(cases, "fragment"),
        exactness=bootstrap_estimate(_present_values(cases, "exactness")),
        completeness=bootstrap_estimate(_present_values(cases, "completeness")),
        citation_precision=bootstrap_estimate(_present_values(cases, "citation_precision")),
        citation_recall=bootstrap_estimate(_present_values(cases, "citation_recall")),
        entailment_rate=bootstrap_estimate(_present_values(cases, "entailment_rate")),
        page_accuracy=bootstrap_estimate(_present_values(cases, "page_accuracy")),
        abstention_sensitivity=bootstrap_estimate(sensitivity_values),
        abstention_specificity=bootstrap_estimate(specificity_values),
        false_refusal_rate=bootstrap_estimate([1.0 - value for value in specificity_values]),
        insufficiency_brier_score=bootstrap_estimate(brier_values),
        cases=cases,
        numeric_faithfulness=_optional_estimate(cases, "numeric_faithfulness"),
        numeric_assessment_coverage=_optional_estimate(cases, "numeric_assessment_coverage"),
    )
