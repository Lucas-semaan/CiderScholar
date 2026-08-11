"""Rebuild, resume, or verify the common-corpus abstract-only Qdrant index."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import (
    index_bibliographic_abstracts,
    verify_bibliographic_abstract_index,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument(
        "--recreate",
        action="store_true",
        help="Delete only bibliographic_abstracts and re-embed eligible abstract-only records",
    )
    operations.add_argument(
        "--verify",
        action="store_true",
        help="Verify exact SQLite/Qdrant abstract-only IDs and routing payloads without E5",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry only records marked failed, in addition to pending records",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verify and args.retry_failed:
        raise SystemExit("--retry-failed cannot be combined with --verify")
    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)

    if args.verify:
        report = verify_bibliographic_abstract_index(settings, store)
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
        return 0

    report = index_bibliographic_abstracts(
        settings,
        store,
        SentenceTransformerBackend(settings),
        recreate=args.recreate,
        retry_failed=args.retry_failed,
        raise_on_error=False,
    )
    payload = {
        **report.model_dump(mode="json"),
        "collection_name": settings.harvest.vector_collection_name,
        "corpus": CorpusScope.COMMON.value,
        "recreated": args.recreate,
        "retry_failed": args.retry_failed,
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = settings.paths.exports_dir / f"bibliographic-abstract-index-{timestamp}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**payload, "report_path": str(report_path)}, ensure_ascii=False))
    return 1 if report.error_type is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
