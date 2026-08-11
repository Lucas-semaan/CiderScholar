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
from app.retrieval.index_manifest import (
    begin_index_generation,
    prepare_index_generation_mutation,
    resume_index_generation,
    verify_index_generation_snapshot,
    write_ready_index_generation_manifest,
)
from app.retrieval.index_reconciliation import reconcile_chunk_index
from app.retrieval.vector_search import QdrantLocalIndex


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--retry-failed", action="store_true", help="Retry chunks marked failed")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument(
        "--recreate",
        action="store_true",
        help="Delete only the configured Qdrant collection and re-embed every chunk",
    )
    operations.add_argument(
        "--reconcile",
        action="store_true",
        help=(
            "Reconcile SQLite chunks against Qdrant IDs/payloads and encode only "
            "missing or invalid points"
        ),
    )
    operations.add_argument(
        "--verify-generation",
        action="store_true",
        help="Verify the ready index sidecar against SQLite and every Qdrant point ID",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.verify_generation or args.reconcile) and args.retry_failed:
        parser.error("--retry-failed cannot be combined with --verify-generation or --reconcile")
    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
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
    index_manifest = None

    try:
        if args.verify_generation:
            index_manifest = verify_index_generation_snapshot(database, index)
            output = {
                "collection_name": index.collection_name,
                "generation_id": index_manifest.generation_id,
                "state": index_manifest.state,
                "indexed_article_count": index_manifest.indexed_article_count,
                "indexed_chunk_count": index_manifest.indexed_chunk_count,
                "qdrant_point_count": index_manifest.qdrant_point_count,
                "fully_indexed": index_manifest.fully_indexed,
                "verified": True,
                "corpus": CorpusScope.COMMON.value,
            }
            print(json.dumps(output, ensure_ascii=False))
            return 0
        status_counts = database.embedding_status_counts()
        reconciliation = None
        if args.reconcile:
            resumed_manifest = resume_index_generation(index)
            index_manifest = resumed_manifest or begin_index_generation(index)
            reconciliation = reconcile_chunk_index(database, index)
            status_counts = database.embedding_status_counts()
            requires_mutation = True
        else:
            requires_mutation = bool(status_counts.get("pending", 0)) or (
                args.retry_failed and bool(status_counts.get("failed", 0))
            )
        resumed_manifest = (
            None if args.recreate or args.reconcile else resume_index_generation(index)
        )
        if args.reconcile:
            pass
        elif args.recreate:
            index_manifest = begin_index_generation(index)
            deleted = index.delete_collection()
            reset_chunks = database.reset_all_embedding_statuses()
            recreated = True
            requires_mutation = True
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
        elif requires_mutation:
            index_manifest = prepare_index_generation_mutation(index)
        else:
            index_manifest = resumed_manifest

        backend = SentenceTransformerBackend(
            settings,
            require_model_manifest=index_manifest is not None,
        )
        report = EmbeddingBatchProcessor(settings, database, backend).run(
            index,
            retry_failed=args.retry_failed,
            stop_on_error=True,
            close_backend=True,
        )
        point_count = index.count()
        if (
            (recreated or index_manifest is not None)
            and report.error_type is None
            and report.chunks_failed == 0
            and index.collection_exists()
        ):
            index_manifest = write_ready_index_generation_manifest(
                database,
                index,
                generation_id=index_manifest.generation_id,
                created_at=index_manifest.created_at,
            )
        output = {
            **report.model_dump(mode="json"),
            "collection_name": index.collection_name,
            "qdrant_path": str(index.path),
            "qdrant_point_count": point_count,
            "recreated": recreated,
            "reset_chunks": reset_chunks,
            "reconciliation": reconciliation.model_dump() if reconciliation is not None else None,
            "index_manifest": (
                {
                    "generation_id": index_manifest.generation_id,
                    "state": index_manifest.state,
                    "fully_indexed": index_manifest.fully_indexed,
                }
                if index_manifest is not None
                else None
            ),
            "offline_mode": settings.app.offline_mode,
            "corpus": CorpusScope.COMMON.value,
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
