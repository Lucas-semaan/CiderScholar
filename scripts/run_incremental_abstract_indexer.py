"""Keep the common abstract-only index current during a metadata harvest.

The worker is deliberately eventual and bounded: every pass opens the embedded
Qdrant store for only a small number of batches, then releases its process
lock.  SQLite WAL plus content-hash-conditional lifecycle updates mean that a
notice enriched while it is being embedded remains pending for a later pass.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.resource_lock import ResourceBusyError
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import index_bibliographic_abstracts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=30.0,
        help="Delay between incremental passes (default: 30)",
    )
    parser.add_argument(
        "--timeout-hours",
        type=float,
        default=10.0,
        help="Maximum worker lifetime; zero performs one pass (default: 10)",
    )
    parser.add_argument(
        "--max-batches-per-pass",
        type=int,
        default=1,
        help="Small Qdrant-lock window per pass (default: 1)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Also retry abstracts whose previous embedding failed",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        help="Append JSONL progress here (default: exports directory)",
    )
    return parser


def _append_event(path: Path, event: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")
    if args.timeout_hours < 0:
        raise SystemExit("--timeout-hours cannot be negative")
    if args.max_batches_per_pass <= 0:
        raise SystemExit("--max-batches-per-pass must be positive")

    settings = settings_for_corpus(load_settings(args.config), CorpusScope.COMMON)
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    log_path = args.log_path or (
        settings.paths.exports_dir
        / f"incremental-abstract-indexer-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    )
    deadline = time.monotonic() + (args.timeout_hours * 3600)
    backend = SentenceTransformerBackend(settings)
    exit_code = 0
    try:
        while True:
            now = datetime.now(UTC).isoformat()
            try:
                report = index_bibliographic_abstracts(
                    settings,
                    store,
                    backend,
                    close_backend=False,
                    retry_failed=args.retry_failed,
                    raise_on_error=False,
                    max_batches=args.max_batches_per_pass,
                )
                event: dict[str, object] = {
                    "at": now,
                    "state": "completed_pass" if report.error_type is None else "failed_pass",
                    **report.model_dump(mode="json"),
                    # This is intentionally a bounded probe, not a corpus
                    # counter: counting the full queue after every pass would
                    # extend the Qdrant lock window unnecessarily.
                    "has_pending_after_pass": bool(
                        store.pending_abstracts(limit=1, retry_failed=args.retry_failed)
                    ),
                }
                if report.error_type is not None:
                    exit_code = 1
            except ResourceBusyError as exc:
                event = {"at": now, "state": "deferred_qdrant_busy", "error": str(exc)[:300]}
            _append_event(log_path, event)
            print(json.dumps({**event, "log_path": str(log_path)}, ensure_ascii=False), flush=True)
            if args.timeout_hours == 0 or time.monotonic() >= deadline:
                break
            time.sleep(min(args.poll_seconds, max(0.0, deadline - time.monotonic())))
    finally:
        backend.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
