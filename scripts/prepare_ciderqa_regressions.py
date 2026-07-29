"""Freeze six expert-classified real baseline errors as regression gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.ciderqa_regressions import (
    RegressionClassification,
    build_regression_package,
    write_regression_artifact,
)
from app.evaluation.ciderqa_report import SignedCiderQAReport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--classifications", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    baseline = SignedCiderQAReport.model_validate_json(args.baseline.read_text(encoding="utf-8"))
    raw = json.loads(args.classifications.read_text(encoding="utf-8"))
    classifications = [RegressionClassification.model_validate(item) for item in raw]
    package = build_regression_package(baseline, classifications)
    written = write_regression_artifact(package, args.output)
    print(f"package={written}")
    print(f"sha256={package.package_sha256}")


if __name__ == "__main__":
    main()
