"""Validate a frozen CiderQA manifest and report structural protocol quotas."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.ciderqa import validate_ciderqa_manifest
from app.evaluation.ciderqa_readiness import assess_ciderqa_readiness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    report = assess_ciderqa_readiness(validate_ciderqa_manifest(arguments.manifest))
    payload = report.model_dump_json(indent=2)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.structurally_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
