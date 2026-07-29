"""Calibrate the contextual evidence threshold from expert-labelled CiderQA observations."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.ciderqa import load_ciderqa_manifest, load_split_for_purpose
from app.evaluation.contextual_relevance import (
    ContextualRelevanceCalibrationSet,
    calibrate_contextual_threshold,
    write_contextual_calibration,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate contextual relevance offline without any inference call."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    manifest = load_ciderqa_manifest(arguments.manifest)
    dataset = load_split_for_purpose(
        arguments.manifest,
        "development",
        purpose="development",
    )
    calibration_set = ContextualRelevanceCalibrationSet.model_validate_json(
        arguments.observations.read_text(encoding="utf-8")
    )
    if calibration_set.dataset_sha256 != manifest.development.sha256:
        raise ValueError("calibration observations do not match the frozen CiderQA development set")
    question_ids = {question.id for question in dataset.questions}
    unknown = {
        observation.question_id
        for observation in calibration_set.observations
        if observation.question_id not in question_ids
    }
    if unknown:
        raise ValueError("calibration observations contain unknown CiderQA question identifiers")
    report = calibrate_contextual_threshold(calibration_set)
    written = write_contextual_calibration(report, arguments.output)
    print(f"report={written}")
    print(f"threshold={report.threshold:.6f}")
    print(f"observations_sha256={report.observations_sha256}")
    print("network_calls=0")


if __name__ == "__main__":
    main()
