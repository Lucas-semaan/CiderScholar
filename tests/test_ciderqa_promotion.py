from __future__ import annotations

from app.evaluation.ciderqa_promotion import PromotionMetrics, assess_promotion


def _metrics(**changes: float) -> PromotionMetrics:
    values = {
        "article_recall_at_20": 0.94,
        "article_mrr": 0.82,
        "article_ndcg_at_20": 0.86,
        "exactness": 0.90,
        "completeness": 0.86,
        "citation_precision": 0.98,
        "citation_recall": 0.91,
        "entailment_rate": 0.94,
        "page_accuracy": 0.99,
        "abstention_sensitivity": 0.90,
        "abstention_specificity": 0.91,
    }
    values.update(changes)
    return PromotionMetrics.model_validate(values)


def test_promotion_requires_every_absolute_threshold_and_regression_budget() -> None:
    baseline = _metrics()

    accepted = assess_promotion(baseline, _metrics(exactness=0.895))
    absolute_failure = assess_promotion(baseline, _metrics(citation_precision=0.949))
    regression_failure = assess_promotion(baseline, _metrics(exactness=0.88))

    assert accepted.promoted is True
    assert absolute_failure.promoted is False
    assert any("absolute" in reason for reason in absolute_failure.failures)
    assert regression_failure.promoted is False
    assert any("regression floor" in reason for reason in regression_failure.failures)


def test_one_regression_cannot_be_offset_by_other_improvements() -> None:
    decision = assess_promotion(
        _metrics(),
        _metrics(article_recall_at_20=1.0, exactness=0.87),
    )

    assert decision.promoted is False
    assert any(reason.startswith("exactness") for reason in decision.failures)
