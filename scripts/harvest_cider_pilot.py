"""Run the bounded weekly cider metadata/abstract harvest and local index."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import load_settings
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.updates.cleanup import archive_and_purge_rejected_records
from app.updates.harvest import (
    BibliographicHarvestStore,
    CiderAbstractBackfiller,
    CiderPilotHarvester,
    HarvestNotDue,
)
from app.updates.vector_index import (
    BibliographicHybridSearchService,
    BibliographicVectorIndex,
    index_bibliographic_abstracts,
)

EVALUATION_QUERIES = [
    "microorganismes levures bactéries fermentation malolactique du cidre",
    "rôle des polyphénols dans la couleur et l'oxydation du cidre",
    "distillation maturation arômes calvados eau-de-vie de pomme",
    "azote protéines peptides fermentation du cidre",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if the weekly interval has not elapsed",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip batched OpenAlex DOI enrichment for accepted records",
    )
    parser.add_argument(
        "--backfill-limit",
        type=int,
        default=100,
        help="Maximum accepted DOI records to enrich in one batch (default: 100)",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Store records without updating the local E5/Qdrant index",
    )
    parser.add_argument(
        "--no-evaluate",
        action="store_true",
        help="Skip the four local hybrid retrieval checks",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete JSON report instead of a compact summary",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings(args.config)
    database = Database(settings.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    harvest_payload: dict[str, Any] | None = None
    skipped_reason: str | None = None
    try:
        harvest = CiderPilotHarvester(settings, database).run(force=args.force)
        harvest_payload = harvest.model_dump(mode="json")
    except HarvestNotDue as exc:
        skipped_reason = str(exc)

    backfill_payload: dict[str, Any] | None = None
    if not args.no_backfill:
        backfill = CiderAbstractBackfiller(settings, database).run(
            limit=args.backfill_limit,
        )
        backfill_payload = backfill.model_dump(mode="json")

    index_payload: dict[str, Any] | None = None
    evaluations: list[dict[str, Any]] = []
    normalized_record_count = store.normalize_existing_text()
    reclassified_hit_count = store.reclassify_existing()
    abstractless_rejected_count = store.reject_abstractless_records()
    cleanup = archive_and_purge_rejected_records(settings, database)
    pending_index = bool(store.pending_abstracts(limit=1))
    if not args.no_index and pending_index:
        backend = SentenceTransformerBackend(settings)
        try:
            index_report = index_bibliographic_abstracts(
                settings,
                store,
                backend,
                close_backend=False,
            )
            index_payload = index_report.model_dump(mode="json")
            if not args.no_evaluate:
                service = BibliographicHybridSearchService(
                    settings,
                    store,
                    backend,
                    BibliographicVectorIndex(settings),
                )
                try:
                    for query in EVALUATION_QUERIES:
                        response = service.search(query, limit=5)
                        evaluations.append(
                            {
                                "query": query,
                                "duration_seconds": response.duration_seconds,
                                "results": [
                                    {
                                        "rank": result.rank,
                                        "title": result.title,
                                        "doi": result.doi,
                                        "year": result.publication_year,
                                        "sources": result.sources,
                                        "lexical_rank": result.lexical_rank,
                                        "vector_rank": result.vector_rank,
                                    }
                                    for result in response.results
                                ],
                            }
                        )
                finally:
                    service.close()
            else:
                backend.close()
        except Exception:
            backend.close()
            raise

    statistics = store.statistics()
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "harvest": harvest_payload,
        "backfill": backfill_payload,
        "skipped_reason": skipped_reason,
        "index": index_payload,
        "statistics": statistics,
        "normalized_record_count": normalized_record_count,
        "reclassified_hit_count": reclassified_hit_count,
        "abstractless_rejected_count": abstractless_rejected_count,
        "rejected_cleanup": cleanup.model_dump(mode="json"),
        "evaluations": evaluations,
    }
    report_path = _write_report(settings.paths.exports_dir, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if harvest_payload:
            print(
                "harvest="
                f"{harvest_payload['state']} "
                f"raw={harvest_payload['raw_record_count']} "
                f"unique={harvest_payload['unique_record_count']} "
                f"abstracts={harvest_payload['abstract_record_count']}"
            )
        elif skipped_reason:
            print(f"harvest=skipped reason={skipped_reason}")
        if backfill_payload:
            print(
                "backfill="
                f"{backfill_payload['state']} "
                f"candidates={backfill_payload['candidates']} "
                f"matched={backfill_payload['matched_records']} "
                f"abstracts_added={backfill_payload['abstracts_added']}"
            )
        if index_payload:
            print(
                "index="
                f"indexed={index_payload['records_indexed']} "
                f"failed={index_payload['records_failed']}"
            )
        print(
            f"corpus=records:{statistics['records']} "
            f"abstracts:{statistics['abstracts']} "
            f"indexed:{statistics['indexed']}"
        )
        print(f"evaluation_queries={len(evaluations)}")
        print(f"report={report_path}")
    return 0


def _write_report(exports_dir: Path, report: dict[str, Any]) -> Path:
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = exports_dir / f"cider-harvest-pilot-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
