"""Strip local review text from a completed contextual expert-adjudication file."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.contextual_adjudication import (
    ContextualAdjudicationSet,
    finalize_contextual_adjudication,
)
from app.evaluation.contextual_relevance import write_contextual_calibration_set


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create text-free contextual observations after every expert decision is set."
    )
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    adjudication = ContextualAdjudicationSet.model_validate_json(
        arguments.adjudication.read_text(encoding="utf-8")
    )
    calibration_set = finalize_contextual_adjudication(adjudication)
    written = write_contextual_calibration_set(calibration_set, arguments.output)
    print(f"observations={written}")
    print(f"count={len(calibration_set.observations)}")
    print("question_and_summary_text_written=0")
    print("network_calls=0")


if __name__ == "__main__":
    main()
