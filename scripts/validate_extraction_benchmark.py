"""Read-only validation of an extraction benchmark manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from app.evaluation.extraction_benchmark import load_extraction_benchmark_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        manifest = load_extraction_benchmark_manifest(arguments.manifest)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"invalid extraction benchmark manifest: {exc}")
        return 2
    print(
        f"valid extraction benchmark manifest: {manifest.benchmark_version} "
        f"({manifest.state}, {len(manifest.entries)} entries)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
