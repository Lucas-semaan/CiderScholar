"""Build or explicitly rebuild the embedded Qdrant chunk index."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import (
    EmbeddingBatchProcessor,
    SentenceTransformerBackend,
)
from app.retrieval.vector_search import QdrantLocalIndex


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--scope",
        choices=("configured", CorpusScope.COMMON.value, CorpusScope.PRIVATE.value),
        default="configured",
        help="Corpus whose chunk collection must be indexed",
    )
    parser.add_argument("--retry-failed", action="store_true", help="Retry chunks marked failed")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete only the configured Qdrant collection and re-embed every chunk",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    if args.scope != "configured":
        settings = settings_for_corpus(settings, CorpusScope(args.scope))
    settings.paths.create()
    logging.basicConfig(
        level=getattr(logging, settings.app.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database = Database(settings.paths.database_path)
    database.initialize()
    index = QdrantLocalIndex(settings)
    recreated = False
    reset_chunks = 0

    try:
        status_counts = database.embedding_status_counts()
        if args.recreate:
            deleted = index.delete_collection()
            reset_chunks = database.reset_all_embedding_statuses()
            recreated = True
            print(
                f"Collection supprimée={deleted}; fragments remis en attente={reset_chunks}. "
                "Les textes SQLite et PDF sont conservés."
            )
        elif not index.collection_exists() and status_counts.get("indexed", 0):
            print(
                "SQLite contient des fragments marqués indexed mais la collection Qdrant "
                "n'existe pas. Relancez avec --recreate.",
                file=sys.stderr,
            )
            return 2

        backend = SentenceTransformerBackend(settings)
        report = EmbeddingBatchProcessor(settings, database, backend).run(
            index,
            retry_failed=args.retry_failed,
            stop_on_error=True,
            close_backend=True,
        )
        point_count = index.count()
        output = {
            **report.model_dump(mode="json"),
            "collection_name": index.collection_name,
            "qdrant_path": str(index.path),
            "qdrant_point_count": point_count,
            "recreated": recreated,
            "reset_chunks": reset_chunks,
            "offline_mode": settings.app.offline_mode,
            "scope": args.scope,
        }
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_path = settings.paths.exports_dir / f"vector-index-{timestamp}.json"
        report_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"collection={index.collection_name} points={point_count} "
            f"indexés={report.chunks_indexed} échecs={report.chunks_failed}"
        )
        print(f"Rapport : {report_path}")
        return 1 if report.error_type or report.chunks_failed else 0
    finally:
        index.close()


if __name__ == "__main__":
    raise SystemExit(main())
