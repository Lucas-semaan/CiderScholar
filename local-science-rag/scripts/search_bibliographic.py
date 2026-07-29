"""Search configured official bibliographic APIs sequentially."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import load_settings
from app.updates.service import BibliographicDiscoveryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Scientific metadata query")
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--limit", type=int, help="Results requested from each source")
    parser.add_argument("--json", action="store_true", help="Print structured JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings(args.config)
    try:
        report = BibliographicDiscoveryService(settings).search(
            args.query, limit_per_source=args.limit
        )
    except Exception as exc:
        print(
            f"Recherche bibliographique impossible: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        for record in report.records:
            print(
                f"[{record.source}] {record.title} | "
                f"{record.publication_year or '?'} | DOI {record.doi or 'absent'}"
            )
        for error in report.errors:
            print(
                f"[{error.source}] {error.error_type}: {error.message}",
                file=sys.stderr,
            )
    return 0 if report.successful_sources else 2


if __name__ == "__main__":
    raise SystemExit(main())
