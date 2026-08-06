"""Resolve stored DOI values, ingest accessible full-text PDFs, and update the RAG."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.admin.secrets import AdminBibliographicKeyVault
from app.config import load_settings
from app.corpora import CorpusScope, load_local_profile, settings_for_corpus
from app.database.sqlite import Database
from app.services.workflows import index_pending_chunks
from app.updates.full_text import FullTextHarvestService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Persist provider availability without downloading or ingesting PDFs",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Skip one-DOI-at-a-time Unpaywall and Crossref fallbacks",
    )
    parser.add_argument(
        "--max-downloads",
        type=int,
        help="Override the configured maximum accepted PDFs for this run",
    )
    parser.add_argument(
        "--max-native-downloads",
        type=int,
        help="Override the configured maximum JATS/TEI/text source files for this run",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Leave newly ingested chunks pending instead of updating Qdrant",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings(arguments.config)
    AdminBibliographicKeyVault(
        settings,
        load_local_profile(),
    ).hydrate_process_environment()
    if not settings.full_text.enabled:
        print("full_text.enabled=false; aucune acquisition lancée", file=sys.stderr)
        return 2
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    rag_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    rag_database = Database(rag_settings.paths.database_path)
    rag_database.initialize()
    audit, harvest = FullTextHarvestService(
        settings,
        database,
        rag_settings=rag_settings,
        rag_database=rag_database,
    ).run(
        audit_only=arguments.audit_only,
        include_slow_fallbacks=not arguments.fast,
        max_downloads=arguments.max_downloads,
        max_native_downloads=arguments.max_native_downloads,
        progress=lambda message: print(message, flush=True),
    )

    index_payload: dict[str, Any] | None = None
    if not arguments.audit_only and not arguments.no_index and harvest.article_ids:
        print(f"indexation RAG: {len(harvest.article_ids)} articles", flush=True)
        index_report = index_pending_chunks(
            rag_settings,
            rag_database,
            article_ids=harvest.article_ids,
            retry_failed=True,
        )
        index_payload = index_report.model_dump(mode="json")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database_path": str(settings.paths.database_path),
        "rag_database_path": str(rag_settings.paths.database_path),
        "audit": audit.model_dump(mode="json"),
        "harvest": harvest.model_dump(mode="json"),
        "index": index_payload,
    }
    report_path = _write_report(settings.paths.exports_dir, payload)
    print(
        f"audit=doi:{audit.doi_count} résolus:{audit.resolved_count} "
        f"acceptés_résolus:{audit.resolved_accepted_count}",
        flush=True,
    )
    print(
        f"ingestion=téléchargés:{harvest.downloaded} ingérés:{harvest.ingested} "
        f"doublons:{harvest.duplicate} différés:{harvest.deferred} "
        f"échecs:{harvest.failed}",
        flush=True,
    )
    print(
        "natif="
        f"téléchargés:{harvest.native_downloaded} "
        f"déjà_présents:{harvest.native_already_downloaded} "
        f"différés:{harvest.native_deferred} échecs:{harvest.native_failed}",
        flush=True,
    )
    print(f"rapport={report_path.resolve()}", flush=True)
    if arguments.audit_only:
        return 0
    return 0 if harvest.ingested or harvest.already_ingested else 2


def _write_report(exports_dir: Path, payload: dict[str, Any]) -> Path:
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = exports_dir / f"full-text-harvest-{stamp}.json"
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    raise SystemExit(main())
