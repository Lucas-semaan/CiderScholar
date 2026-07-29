"""Combine passing physical 8 GB and 16 GB reports into the DRS-026 evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.deep_research_profiles import (
    SignedProfileTrialReport,
    build_dual_profile_report,
    write_profile_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eight-gb", type=Path, required=True)
    parser.add_argument("--sixteen-gb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    eight = SignedProfileTrialReport.model_validate_json(args.eight_gb.read_text(encoding="utf-8"))
    sixteen = SignedProfileTrialReport.model_validate_json(
        args.sixteen_gb.read_text(encoding="utf-8")
    )
    report = build_dual_profile_report(eight, sixteen)
    written = write_profile_report(report, args.output)
    print(f"report={written}")
    print(f"sha256={report.report_sha256}")


if __name__ == "__main__":
    main()
