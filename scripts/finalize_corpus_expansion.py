"""Consolidate, clean, index, and verify a completed corpus-expansion campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.updates.cleanup import archive_and_purge_rejected_records
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import (
    index_bibliographic_abstracts,
    verify_bibliographic_abstract_index,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Confirm the archived rejection cleanup and index mutations",
    )
    parser.add_argument(
        "--backup-manifest",
        type=Path,
        required=True,
        help="Verified pre-campaign SQLite backup manifest",
    )
    parser.add_argument("--no-index", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.apply:
        raise SystemExit("--apply is required for corpus expansion finalization")
    backup = _verify_backup(arguments.backup_manifest)
    settings = settings_for_corpus(load_settings(arguments.config), CorpusScope.COMMON)
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    _require_no_running_harvest(database)
    store = BibliographicHarvestStore(database)
    before = store.statistics()

    normalized = store.normalize_existing_text()
    reclassified = store.reclassify_existing()
    doi_less_reviewed = store.review_doi_less_abstracts()
    abstractless_rejected = store.reject_abstractless_records()
    cleanup = archive_and_purge_rejected_records(settings, database)

    index_payload: dict[str, Any] | None = None
    verification_payload: dict[str, Any] | None = None
    if not arguments.no_index:
        index_report = index_bibliographic_abstracts(
            settings,
            store,
            SentenceTransformerBackend(settings),
            retry_failed=True,
            raise_on_error=True,
        )
        index_payload = index_report.model_dump(mode="json")
        verification_payload = verify_bibliographic_abstract_index(
            settings,
            store,
        ).model_dump(mode="json")

    with closing(database.connect()) as connection:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        themes = [
            dict(row)
            for row in connection.execute(
                """
                SELECT COALESCE(relevance_theme, '(none)') AS theme,
                    relevance_status, COUNT(*) AS records,
                    SUM(CASE WHEN abstract IS NOT NULL AND trim(abstract) != ''
                        THEN 1 ELSE 0 END) AS abstracts
                FROM bibliographic_records
                GROUP BY relevance_theme, relevance_status
                ORDER BY theme, relevance_status
                """
            )
        ]
    if quick_check != "ok":
        raise RuntimeError("SQLite quick_check failed after corpus expansion finalization")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "database_path": str(database.path.resolve()),
        "backup": backup,
        "before": before,
        "normalized_records": normalized,
        "reclassified_hits": reclassified,
        "doi_less_reviewed": doi_less_reviewed,
        "abstractless_rejected": abstractless_rejected,
        "cleanup": cleanup.model_dump(mode="json"),
        "after": store.statistics(),
        "themes": themes,
        "index": index_payload,
        "index_verification": verification_payload,
        "sqlite_quick_check": quick_check,
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = settings.paths.exports_dir / f"corpus-expansion-finalization-{stamp}.json"
    _write_json(report_path, payload)
    print(json.dumps({**payload, "report_path": str(report_path)}, ensure_ascii=False))
    return 0


def _verify_backup(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.resolve().read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("sqlite_quick_check") != "ok":
        raise ValueError("pre-campaign backup manifest is invalid")
    backup_path = Path(str(manifest.get("backup") or "")).resolve()
    if not backup_path.is_file():
        raise ValueError("pre-campaign SQLite backup is unavailable")
    if backup_path.stat().st_size != int(manifest.get("size") or -1):
        raise ValueError("pre-campaign SQLite backup size differs from its manifest")
    if _sha256_file(backup_path) != str(manifest.get("sha256") or ""):
        raise ValueError("pre-campaign SQLite backup hash differs from its manifest")
    with closing(sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)) as backup:
        if str(backup.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise ValueError("pre-campaign SQLite backup failed quick_check")
    return {
        "manifest": str(manifest_path.resolve()),
        "path": str(backup_path),
        "size": backup_path.stat().st_size,
        "sha256": manifest["sha256"],
        "sqlite_quick_check": "ok",
    }


def _require_no_running_harvest(database: Database) -> None:
    with closing(database.connect()) as connection:
        rows = list(
            connection.execute(
                """
                SELECT id, profile, started_at
                FROM bibliographic_harvest_runs
                WHERE state = 'running'
                ORDER BY started_at
                """
            )
        )
    if rows:
        profiles = ", ".join(str(row["profile"]) for row in rows[:5])
        raise RuntimeError(f"bibliographic harvest is still running: {profiles}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
