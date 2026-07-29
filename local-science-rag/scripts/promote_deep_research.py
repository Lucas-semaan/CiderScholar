"""Create the runtime activation bundle only after every promotion check passes."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.deep_research.promotion import build_activation_bundle, write_activation_bundle
from app.evaluation.ciderqa_ablation import SignedCiderQAAblationReport
from app.evaluation.ciderqa_report import SignedCiderQAReport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--ablation", type=Path, required=True)
    parser.add_argument("--memory-profile", choices=("8gb", "16gb"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    baseline = SignedCiderQAReport.model_validate_json(args.baseline.read_text(encoding="utf-8"))
    candidate = SignedCiderQAReport.model_validate_json(args.candidate.read_text(encoding="utf-8"))
    ablation = SignedCiderQAAblationReport.model_validate_json(
        args.ablation.read_text(encoding="utf-8")
    )
    bundle = build_activation_bundle(
        baseline,
        candidate,
        ablation,
        memory_profile=args.memory_profile,
    )
    written = write_activation_bundle(bundle, args.output)
    print(f"activation={written}")
    print(f"sha256={bundle.bundle_sha256}")


if __name__ == "__main__":
    main()
