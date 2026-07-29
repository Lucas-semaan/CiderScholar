"""Verify the versioned demonstration questions against one local common corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.demo_sources import verify_demo_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/demo_questions.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    report = verify_demo_sources(arguments.database.resolve(), arguments.manifest.resolve())
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
