"""Compare the six signed CiderQA ablation runs against one baseline."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.ciderqa_ablation import (
    ABLATION_VARIANTS,
    CiderQAAblationPlan,
    build_ablation_report,
    write_signed_model,
)
from app.evaluation.ciderqa_report import SignedCiderQAReport


def _report_argument(value: str) -> tuple[str, Path]:
    variant, separator, raw_path = value.partition("=")
    if not separator or variant not in ABLATION_VARIANTS or not raw_path:
        choices = ", ".join(ABLATION_VARIANTS)
        raise argparse.ArgumentTypeError(f"expected VARIANT=PATH with VARIANT in: {choices}")
    return variant, Path(raw_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--report",
        type=_report_argument,
        action="append",
        required=True,
        help="Repeat exactly once for baseline and each isolated stage.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = CiderQAAblationPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
    paths = dict(args.report)
    if len(paths) != len(args.report):
        raise SystemExit("each ablation variant must be provided exactly once")
    reports = {
        variant: SignedCiderQAReport.model_validate_json(path.read_text(encoding="utf-8"))
        for variant, path in paths.items()
    }
    report = build_ablation_report(plan, reports)
    written = write_signed_model(report, args.output)
    print(f"report={written}")
    print(f"sha256={report.report_sha256}")
    print(f"baseline={report.baseline_report_sha256}")


if __name__ == "__main__":
    main()
