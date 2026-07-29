from __future__ import annotations

from app.evaluation.contextual_relevance import (
    ContextualRelevanceCalibrationSet,
    ContextualRelevanceObservation,
    calibrate_contextual_threshold,
)


def _calibration_set() -> ContextualRelevanceCalibrationSet:
    observations = []
    for index in range(10):
        question_id = f"ciderqa-calibration-{index}"
        observations.extend(
            (
                ContextualRelevanceObservation(
                    question_id=question_id,
                    text_sha256=f"{index * 2 + 1:064x}",
                    relevance_score=0.80 + index / 1_000,
                    expert_relevant=True,
                ),
                ContextualRelevanceObservation(
                    question_id=question_id,
                    text_sha256=f"{index * 2 + 2:064x}",
                    relevance_score=0.20 + index / 1_000,
                    expert_relevant=False,
                ),
            )
        )
    return ContextualRelevanceCalibrationSet(
        dataset_sha256="a" * 64,
        observations=observations,
    )


def test_contextual_threshold_calibration_is_deterministic_and_separates_labels() -> None:
    calibration_set = _calibration_set()

    first = calibrate_contextual_threshold(calibration_set)
    second = calibrate_contextual_threshold(calibration_set)

    assert first == second
    assert first.threshold == 0.8
    assert first.precision == first.recall == first.f1 == first.specificity == 1.0
    assert first.observation_count == 20
    assert first.question_count == 10
    assert len(first.observations_sha256) == 64


def test_contextual_threshold_tie_prefers_stricter_more_precise_gate() -> None:
    calibration_set = _calibration_set()
    observations = [
        item.model_copy(update={"relevance_score": 0.5})
        if item.question_id == "ciderqa-calibration-0"
        else item
        for item in calibration_set.observations
    ]

    report = calibrate_contextual_threshold(
        calibration_set.model_copy(update={"observations": observations})
    )

    assert 0.5 <= report.threshold <= 1.0
