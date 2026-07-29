"""Freeze the common CiderQA ablation matrix before running inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.ciderqa_ablation import build_ablation_plan, write_signed_model


def _json_mapping(value: str, *, value_type: type[str] | type[int]) -> dict:
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not parsed:
        raise argparse.ArgumentTypeError("expected a non-empty JSON object")
    try:
        return {str(key): value_type(item) for key, item in parsed.items()}
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("JSON object contains an invalid value") from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--dataset-sha256", required=True)
    parser.add_argument(
        "--split", choices=("development", "validation", "final_test"), required=True
    )
    parser.add_argument("--mode", choices=("abstract_only", "full_text"), required=True)
    parser.add_argument("--corpus-sha256", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--model-versions-json", required=True)
    parser.add_argument("--seeds-json", default='{"bootstrap":1729}')
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = build_ablation_plan(
        dataset_version=args.dataset_version,
        dataset_sha256=args.dataset_sha256,
        split=args.split,
        mode=args.mode,
        corpus_sha256=args.corpus_sha256,
        code_revision=args.code_revision,
        model_versions=_json_mapping(args.model_versions_json, value_type=str),
        seeds=_json_mapping(args.seeds_json, value_type=int),
    )
    written = write_signed_model(plan, args.output)
    print(f"plan={written}")
    print(f"sha256={plan.plan_sha256}")
    for configuration in plan.configurations:
        parameters = {
            "ablation_plan_sha256": plan.plan_sha256,
            **configuration.signed_parameters,
        }
        print(f"{configuration.variant}={json.dumps(parameters, sort_keys=True)}")


if __name__ == "__main__":
    main()
