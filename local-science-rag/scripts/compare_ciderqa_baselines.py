"""Compare signed abstract-only and full-text CiderQA baselines."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.ciderqa_baselines import (
    build_baseline_comparison,
    write_baseline_comparison,
)
from app.evaluation.ciderqa_report import SignedCiderQAReport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abstract-only", type=Path, required=True)
    parser.add_argument("--full-text", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    abstract = SignedCiderQAReport.model_validate_json(
        args.abstract_only.read_text(encoding="utf-8")
    )
    full_text = SignedCiderQAReport.model_validate_json(args.full_text.read_text(encoding="utf-8"))
    report = build_baseline_comparison(abstract, full_text)
    written = write_baseline_comparison(report, args.output)
    print(f"report={written}")
    print(f"sha256={report.report_sha256}")


if __name__ == "__main__":
    main()
