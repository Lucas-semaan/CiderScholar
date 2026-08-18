"""Run a large, resumable cider harvest, quarantine purge, and local indexing pass."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.updates.cleanup import archive_and_purge_rejected_records
from app.updates.harvest import BibliographicHarvestStore, CiderBulkHarvester
from app.updates.harvest_queries import (
    CIDER_EXPANDED_QUERY_WAVES,
    CIDER_MATERIAL_QUERY_WAVES,
    CIDER_MICROBIOLOGY_QUERY_WAVES,
    CIDER_SPECIALIZED_QUERY_WAVES,
)
from app.updates.vector_index import index_bibliographic_abstracts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--target", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-runs", type=int, default=20)
    parser.add_argument("--profile", default="cider_design_bulk")
    parser.add_argument(
        "--start-page",
        type=int,
        default=0,
        help="Start pagination at this zero-based page for a new profile",
    )
    parser.add_argument(
        "--query-set",
        choices=("focused", "expanded", "specialized", "materials", "microbiology"),
        default="focused",
        help="Focused legacy, broad expansion, or specialized domain query waves",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=("crossref", "europe_pmc", "openalex", "clarivate", "elsevier"),
        help="Optional provider subset; defaults to every configured provider",
    )
    parser.add_argument(
        "--defer-maintenance",
        action="store_true",
        help=(
            "Defer global normalization, reclassification, rejected-record cleanup, "
            "and vector maintenance until the final campaign pass"
        ),
    )
    parser.add_argument("--no-index", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    # This command is itself an explicit, operator-initiated harvest.  The
    # application-wide scheduler may remain disabled without preventing a
    # deliberate local campaign.
    settings.harvest.enabled = True
    database = Database(settings.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)

    cleanup_before = None
    abstractless_rejected_before = 0
    if args.defer_maintenance:
        print("maintenance_before=deferred", flush=True)
    else:
        abstractless_rejected_before = store.reject_abstractless_records()
        cleanup_before = archive_and_purge_rejected_records(settings, database)
        print(
            f"abstractless_before={abstractless_rejected_before} "
            f"cleanup_before=archived:{cleanup_before.archived_records} "
            f"deleted:{cleanup_before.records_deleted} "
            f"remaining:{cleanup_before.remaining_rejected_records}",
            flush=True,
        )
    bulk = CiderBulkHarvester(settings, database).run(
        target_new_accepted_abstracts=args.target,
        page_size=args.page_size,
        max_runs=args.max_runs,
        profile=args.profile,
        **_query_wave_override(args.query_set),
        sources=tuple(args.sources) if args.sources else None,
        start_page=args.start_page,
        progress=lambda message: print(message, flush=True),
    )
    cleanup_after = None
    normalized = 0
    reclassified = 0
    abstractless_rejected_after = 0
    if args.defer_maintenance:
        print("maintenance_after=deferred", flush=True)
    else:
        normalized = store.normalize_existing_text()
        reclassified = store.reclassify_existing()
        abstractless_rejected_after = store.reject_abstractless_records()
        cleanup_after = archive_and_purge_rejected_records(settings, database)
        print(
            f"abstractless_after={abstractless_rejected_after} "
            f"cleanup_after=archived:{cleanup_after.archived_records} "
            f"deleted:{cleanup_after.records_deleted} "
            f"remaining:{cleanup_after.remaining_rejected_records}",
            flush=True,
        )

    index_payload: dict[str, Any] | None = None
    if not args.no_index and store.pending_abstracts(limit=1):
        backend = SentenceTransformerBackend(settings)
        report = index_bibliographic_abstracts(settings, store, backend)
        index_payload = report.model_dump(mode="json")
        print(
            f"index=indexed:{report.records_indexed} failed:{report.records_failed} "
            f"pruned:{report.records_pruned}",
            flush=True,
        )

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "query_set": args.query_set,
        "bulk": bulk.model_dump(mode="json"),
        "maintenance_deferred": args.defer_maintenance,
        "cleanup_before": (
            cleanup_before.model_dump(mode="json") if cleanup_before is not None else None
        ),
        "cleanup_after": (
            cleanup_after.model_dump(mode="json") if cleanup_after is not None else None
        ),
        "abstractless_rejected_before": abstractless_rejected_before,
        "abstractless_rejected_after": abstractless_rejected_after,
        "normalized_record_count": normalized,
        "reclassified_hit_count": reclassified,
        "index": index_payload,
        "statistics": store.statistics(),
        "archive_statistics": store.archive_statistics(),
    }
    report_path = _write_report(settings.paths.exports_dir, payload)
    print(
        f"result=new_accepted_abstracts:{bulk.new_accepted_abstracts} "
        f"target_reached:{bulk.target_reached} stop_reason:{bulk.stop_reason}",
        flush=True,
    )
    print(f"report={report_path.resolve()}", flush=True)
    return 0 if bulk.target_reached else 2


def _query_wave_override(query_set: str) -> dict[str, object]:
    if query_set == "expanded":
        return {"query_waves": CIDER_EXPANDED_QUERY_WAVES}
    if query_set == "specialized":
        return {"query_waves": CIDER_SPECIALIZED_QUERY_WAVES}
    if query_set == "materials":
        return {"query_waves": CIDER_MATERIAL_QUERY_WAVES}
    if query_set == "microbiology":
        return {"query_waves": CIDER_MICROBIOLOGY_QUERY_WAVES}
    return {}


def _write_report(exports_dir: Path, report: dict[str, Any]) -> Path:
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = exports_dir / f"cider-harvest-bulk-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
