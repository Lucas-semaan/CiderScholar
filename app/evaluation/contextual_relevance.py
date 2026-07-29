"""Offline CiderQA calibration for the contextual-summary relevance gate."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContextualRelevanceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^ciderqa-[a-z0-9][a-z0-9-]{2,79}$")
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relevance_score: float = Field(ge=0.0, le=1.0)
    expert_relevant: bool


class ContextualRelevanceCalibrationSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    split: Literal["development"] = "development"
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observations: list[ContextualRelevanceObservation] = Field(min_length=20)

    @model_validator(mode="after")
    def validate_labels(self) -> ContextualRelevanceCalibrationSet:
        identities = {
            (observation.question_id, observation.text_sha256) for observation in self.observations
        }
        if len(identities) != len(self.observations):
            raise ValueError("contextual calibration observations cannot be duplicated")
        if len({observation.question_id for observation in self.observations}) < 10:
            raise ValueError("contextual calibration requires at least ten CiderQA questions")
        if {observation.expert_relevant for observation in self.observations} != {
            False,
            True,
        }:
            raise ValueError("contextual calibration requires relevant and rejected labels")
        return self


class ContextualThresholdCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    split: Literal["development"] = "development"
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observations_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_count: int = Field(ge=20)
    question_count: int = Field(ge=10)
    threshold: float = Field(ge=0.0, le=1.0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)
    specificity: float = Field(ge=0.0, le=1.0)


def _digest(calibration_set: ContextualRelevanceCalibrationSet) -> str:
    payload = json.dumps(
        calibration_set.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _metrics(
    observations: list[ContextualRelevanceObservation],
    threshold: float,
) -> tuple[float, float, float, float]:
    predictions = [
        (observation.relevance_score >= threshold, observation.expert_relevant)
        for observation in observations
    ]
    true_positive = sum(predicted and expected for predicted, expected in predictions)
    false_positive = sum(predicted and not expected for predicted, expected in predictions)
    false_negative = sum(not predicted and expected for predicted, expected in predictions)
    true_negative = sum(not predicted and not expected for predicted, expected in predictions)
    predicted_positive = true_positive + false_positive
    precision = true_positive / predicted_positive if predicted_positive else 0.0
    recall = true_positive / (true_positive + false_negative)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    specificity = true_negative / (true_negative + false_positive)
    return precision, recall, f1, specificity


def calibrate_contextual_threshold(
    calibration_set: ContextualRelevanceCalibrationSet,
) -> ContextualThresholdCalibration:
    """Choose the F1-optimal threshold, preferring precision then stricter ties."""

    observations = calibration_set.observations
    candidates = sorted({0.0, 1.0, *(item.relevance_score for item in observations)})
    scored = [(threshold, *_metrics(observations, threshold)) for threshold in candidates]
    threshold, precision, recall, f1, specificity = max(
        scored,
        key=lambda item: (item[3], item[1], item[4], item[0]),
    )
    return ContextualThresholdCalibration(
        dataset_sha256=calibration_set.dataset_sha256,
        observations_sha256=_digest(calibration_set),
        observation_count=len(observations),
        question_count=len({item.question_id for item in observations}),
        threshold=threshold,
        precision=precision,
        recall=recall,
        f1=f1,
        specificity=specificity,
    )


def write_contextual_calibration(
    report: ContextualThresholdCalibration,
    destination: str | Path,
) -> Path:
    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(report.model_dump_json(indent=2) + "\n")
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return path


def write_contextual_calibration_set(
    calibration_set: ContextualRelevanceCalibrationSet,
    destination: str | Path,
) -> Path:
    """Atomically write the text-free expert observations consumed by calibration."""

    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(calibration_set.model_dump_json(indent=2) + "\n")
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return path
