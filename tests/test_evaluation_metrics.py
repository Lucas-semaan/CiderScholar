from __future__ import annotations

import math

import pytest

from app.evaluation.metrics import (
    combine_traceability,
    concept_recall,
    ranking_metrics,
    traceability_metrics,
)


def test_ranking_metrics_use_graded_relevance_and_expected_recall() -> None:
    metrics = ranking_metrics(
        ["acceptable", "expected-1", "irrelevant", "expected-2"],
        ["expected-1", "expected-2"],
        ["acceptable"],
        k=4,
    )

    dcg = 1.0 + 3.0 / math.log2(3) + 3.0 / math.log2(5)
    ideal = 3.0 + 3.0 / math.log2(3) + 1.0 / math.log2(4)
    assert metrics.precision_at_k == 0.75
    assert metrics.recall_at_k == 1.0
    assert metrics.mean_reciprocal_rank == 1.0
    assert metrics.ndcg_at_k == pytest.approx(dcg / ideal)
    assert metrics.relevant_hits == 3
    assert metrics.expected_hits == 2


def test_ranking_metrics_deduplicate_results_before_cutoff() -> None:
    metrics = ranking_metrics(["a", "a", "b"], ["a", "b"], k=2)

    assert metrics.precision_at_k == 1.0
    assert metrics.recall_at_k == 1.0
    assert metrics.ndcg_at_k == 1.0


def test_ranking_metrics_reject_invalid_depth_or_empty_ground_truth() -> None:
    with pytest.raises(ValueError, match="positive"):
        ranking_metrics(["a"], ["a"], k=0)
    with pytest.raises(ValueError, match="expected article"):
        ranking_metrics(["a"], [], k=20)


def test_concept_recall_is_accent_insensitive_and_phrase_aware() -> None:
    value = concept_recall(
        ["polyphénols", "température élevée"],
        ["La stabilite des polyphenols est mesurée pendant le stockage."],
    )

    assert value == 0.5
    assert concept_recall([], []) == 1.0


def test_traceability_counts_missing_and_unknown_evidence() -> None:
    first = traceability_metrics(
        [["evidence-1"], ["evidence-2", "invented"], []],
        ["evidence-1", "evidence-2"],
    )
    second = traceability_metrics([["evidence-3"]], ["evidence-3"])
    combined = combine_traceability([first, second])

    assert first.total_citations == 3
    assert first.traceable_citations == 2
    assert first.total_assertions == 3
    assert first.unsupported_assertions == 2
    assert first.traceable_citation_rate == pytest.approx(2 / 3)
    assert first.unsupported_assertion_rate == pytest.approx(2 / 3)
    assert combined.evaluated_synthesis_count == 2
    assert combined.traceable_citation_rate == 0.75
    assert combined.unsupported_assertion_rate == 0.5


def test_empty_traceability_aggregate_reports_not_evaluated() -> None:
    metrics = combine_traceability([])

    assert metrics.evaluated_synthesis_count == 0
    assert metrics.traceable_citation_rate is None
    assert metrics.unsupported_assertion_rate is None
