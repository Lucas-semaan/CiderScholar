"""Build a signed CiderQA report from frozen labels and adjudicated local results."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.ciderqa import (
    CiderQAPurpose,
    CiderQASplit,
    load_ciderqa_manifest,
    load_split_for_purpose,
)
from app.evaluation.ciderqa_report import (
    CiderQAResultSet,
    CiderQARunContext,
    build_signed_ciderqa_report,
    write_ciderqa_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate precomputed CiderQA results without making any network call."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--split",
        choices=("development", "validation", "final_test"),
        required=True,
    )
    parser.add_argument(
        "--purpose",
        choices=("development", "validation", "final_test"),
        required=True,
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    split: CiderQASplit = args.split
    purpose: CiderQAPurpose = args.purpose
    manifest = load_ciderqa_manifest(args.manifest)
    dataset = load_split_for_purpose(args.manifest, split, purpose=purpose)
    result_set = CiderQAResultSet.model_validate_json(args.results.read_text(encoding="utf-8"))
    context = CiderQARunContext.model_validate_json(args.context.read_text(encoding="utf-8"))
    split_file = manifest.split_file(split)
    report = build_signed_ciderqa_report(
        dataset,
        result_set.results,
        context,
        dataset_version=manifest.dataset_version,
        dataset_sha256=split_file.sha256,
    )
    written = write_ciderqa_report(report, args.output)
    print(f"report={written}")
    print(f"sha256={report.report_sha256}")
    print("network_calls=0")


if __name__ == "__main__":
    main()
