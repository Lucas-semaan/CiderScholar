"""Precommitted CiderQA scientific promotion gate."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.ciderqa_report import SignedCiderQAReport, verify_ciderqa_report


class PromotionMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_recall_at_20: float = Field(ge=0.0, le=1.0)
    article_mrr: float = Field(ge=0.0, le=1.0)
    article_ndcg_at_20: float = Field(ge=0.0, le=1.0)
    exactness: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)
    citation_precision: float = Field(ge=0.0, le=1.0)
    citation_recall: float = Field(ge=0.0, le=1.0)
    entailment_rate: float = Field(ge=0.0, le=1.0)
    page_accuracy: float = Field(ge=0.0, le=1.0)
    abstention_sensitivity: float = Field(ge=0.0, le=1.0)
    abstention_specificity: float = Field(ge=0.0, le=1.0)


class PromotionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promoted: bool
    failures: list[str]


ABSOLUTE_THRESHOLDS = PromotionMetrics(
    article_recall_at_20=0.90,
    article_mrr=0.75,
    article_ndcg_at_20=0.80,
    exactness=0.85,
    completeness=0.80,
    citation_precision=0.95,
    citation_recall=0.85,
    entailment_rate=0.85,
    page_accuracy=0.95,
    abstention_sensitivity=0.85,
    abstention_specificity=0.85,
)

REGRESSION_BUDGETS = PromotionMetrics(
    article_recall_at_20=0.02,
    article_mrr=0.02,
    article_ndcg_at_20=0.02,
    exactness=0.01,
    completeness=0.02,
    citation_precision=0.005,
    citation_recall=0.01,
    entailment_rate=0.01,
    page_accuracy=0.005,
    abstention_sensitivity=0.02,
    abstention_specificity=0.02,
)


def metrics_from_report(report: SignedCiderQAReport) -> PromotionMetrics:
    metrics = report.metrics
    return PromotionMetrics(
        article_recall_at_20=metrics.article_retrieval.recall_at_20.value,
        article_mrr=metrics.article_retrieval.mean_reciprocal_rank.value,
        article_ndcg_at_20=metrics.article_retrieval.ndcg_at_20.value,
        exactness=metrics.exactness.value,
        completeness=metrics.completeness.value,
        citation_precision=metrics.citation_precision.value,
        citation_recall=metrics.citation_recall.value,
        entailment_rate=metrics.entailment_rate.value,
        page_accuracy=metrics.page_accuracy.value,
        abstention_sensitivity=metrics.abstention_sensitivity.value,
        abstention_specificity=metrics.abstention_specificity.value,
    )


def assess_promotion(
    baseline: PromotionMetrics,
    candidate: PromotionMetrics,
) -> PromotionDecision:
    failures: list[str] = []
    baseline_values = baseline.model_dump()
    candidate_values = candidate.model_dump()
    threshold_values = ABSOLUTE_THRESHOLDS.model_dump()
    budget_values = REGRESSION_BUDGETS.model_dump()
    for name, candidate_value in candidate_values.items():
        threshold = threshold_values[name]
        if candidate_value < threshold:
            failures.append(f"{name}: {candidate_value:.4f} < absolute {threshold:.4f}")
        allowed_minimum = baseline_values[name] - budget_values[name]
        if candidate_value < allowed_minimum:
            failures.append(
                f"{name}: {candidate_value:.4f} < regression floor {allowed_minimum:.4f}"
            )
    return PromotionDecision(promoted=not failures, failures=failures)


def compare_signed_reports(
    baseline: SignedCiderQAReport,
    candidate: SignedCiderQAReport,
) -> PromotionDecision:
    failures: list[str] = []
    if not verify_ciderqa_report(baseline) or not verify_ciderqa_report(candidate):
        failures.append("baseline and candidate reports must have valid signatures")
    if baseline.dataset_sha256 != candidate.dataset_sha256:
        failures.append("baseline and candidate dataset hashes differ")
    if baseline.context.split != candidate.context.split:
        failures.append("baseline and candidate splits differ")
    if baseline.context.mode != candidate.context.mode:
        failures.append("baseline and candidate modes differ")
    if candidate.context.argo_requests_used > candidate.context.argo_request_limit:
        failures.append("candidate exceeds its ARGO request budget")
    if failures:
        return PromotionDecision(promoted=False, failures=failures)
    return assess_promotion(metrics_from_report(baseline), metrics_from_report(candidate))
