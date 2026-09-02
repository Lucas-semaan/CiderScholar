"""Exclude explicit ``fichier local`` PDF sources while retaining them for audit."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.database.sqlite import Database
from app.services.workflows import exclude_unidentifiable_local_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Optional config.yaml path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the exclusion; without this flag only write the preview report",
    )
    parser.add_argument("--report", type=Path, help="JSON report destination")
    return parser


def _snapshot_database(source_path: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(source_path)) as source,
        closing(sqlite3.connect(destination)) as target,
    ):
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or str(integrity[0]).lower() != "ok":
        destination.unlink(missing_ok=True)
        raise RuntimeError("the pre-exclusion SQLite backup failed its integrity check")
    return destination


def main() -> None:
    arguments = build_parser().parse_args()
    settings = load_settings(arguments.config)
    database = Database(settings.paths.common_database_path)
    candidates = database.unidentifiable_local_articles()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = (
        arguments.report
        or settings.paths.data_dir
        / "exports"
        / "local-pdf-metadata-exclusion"
        / f"unidentifiable-local-sources-{timestamp}.json"
    ).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    records = [dict(row) for row in candidates]
    report: dict[str, object] = {
        "criterion": "source=local AND normalized title=fichier local",
        "database": str(database.path.resolve()),
        "applied": False,
        "candidate_count": len(records),
        "records": records,
    }
    if arguments.apply and records:
        backup_path = _snapshot_database(
            database.path,
            settings.paths.data_dir
            / "backups"
            / "metadata-exclusion"
            / f"science-rag-before-unidentifiable-exclusion-{timestamp}.sqlite3",
        )
        database.initialize()
        result = exclude_unidentifiable_local_sources(
            settings,
            database,
            article_ids=[str(record["id"]) for record in records],
        )
        report.update(
            {
                "applied": True,
                "backup": str(backup_path.resolve()),
                "result": result,
            }
        )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report_path)
    print(json.dumps({key: value for key, value in report.items() if key != "records"}))


if __name__ == "__main__":
    main()
