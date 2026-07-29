"""Replay the six signed representative errors against a candidate report."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.ciderqa_regressions import (
    SignedCiderQARegressionPackage,
    replay_regression_package,
    write_regression_artifact,
)
from app.evaluation.ciderqa_report import SignedCiderQAReport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    package = SignedCiderQARegressionPackage.model_validate_json(
        args.package.read_text(encoding="utf-8")
    )
    candidate = SignedCiderQAReport.model_validate_json(args.candidate.read_text(encoding="utf-8"))
    report = replay_regression_package(package, candidate)
    written = write_regression_artifact(report, args.output)
    print(f"report={written}")
    print(f"sha256={report.report_sha256}")
    print(f"passed={str(report.passed).lower()}")
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
