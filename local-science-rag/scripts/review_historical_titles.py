"""Audit and apply conservative decisions to every historical review notice."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.database.sqlite import Database
from app.updates.doi_exclusions import DoiExclusionRegistry
from app.updates.editorial_scope import classify_editorial_record


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the audited decisions.")
    return parser


def backup_editorial_state(database: Database, destination: Path) -> tuple[Path, Path | None]:
    destination.mkdir(parents=True, exist_ok=False)
    database_backup = destination / database.path.name
    with closing(database.connect()) as source, closing(sqlite3.connect(database_backup)) as target:
        source.backup(target)
    with closing(sqlite3.connect(database_backup)) as verified:
        if verified.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("editorial backup failed SQLite integrity check")
    exclusions = DoiExclusionRegistry.for_database(database.path).path
    exclusions_backup = None
    if exclusions.is_file():
        exclusions_backup = destination / exclusions.name
        shutil.copy2(exclusions, exclusions_backup)
    return database_backup, exclusions_backup


def main() -> int:
    arguments = _parser().parse_args()
    settings = load_settings()
    database = Database(settings.paths.database_path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    with closing(database.connect()) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, title, doi, publication_year, citation_count,
                    relevance_theme, relevance_score, relevance_reason,
                    embedding_status, abstract
                FROM bibliographic_records
                WHERE relevance_status = 'review'
                ORDER BY relevance_theme, title COLLATE NOCASE, id
                """
            )
        ]
    decisions = []
    for row in rows:
        decision = classify_editorial_record(row)
        decisions.append(
            {
                "record_id": row["id"],
                "title": row["title"],
                "doi": row["doi"],
                "publication_year": row["publication_year"],
                "citation_count": row["citation_count"],
                "theme": row["relevance_theme"],
                "automatic_score": row["relevance_score"],
                "has_abstract": bool(row["abstract"] and str(row["abstract"]).strip()),
                "decision": decision.decision,
                "editorial_reason": decision.reason,
            }
        )
    accepted = [item for item in decisions if item["decision"] == "accepted"]
    rejected = [item for item in decisions if item["decision"] == "rejected"]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "scientific_scope": {
            "primary_axis": "cidre",
            "supporting_axes": [
                "biochimie",
                "microbiologie",
                "polyphénols",
                "protéines et azote",
                "jus de pomme",
                "arômes et procédés",
                "Pommeau",
                "Calvados et eaux-de-vie de cidre",
            ],
            "policy": (
                "Lien direct au cidre, à un produit cidricole dérivé ou à une matière/procédé "
                "explicitement utile; rejet des homonymes et mentions incidentes."
            ),
        },
        "state": "dry_run",
        "counts": {
            "reviewed": len(decisions),
            "accepted": len(accepted),
            "accepted_with_abstract": sum(item["has_abstract"] for item in accepted),
            "rejected": len(rejected),
        },
        "records": decisions,
    }
    output = settings.paths.exports_dir / f"historical-title-review-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not arguments.apply:
        print(json.dumps({"report": str(output), **report["counts"]}, ensure_ascii=False))
        return 0

    if any(row["embedding_status"] != "not_applicable" for row in rows):
        raise RuntimeError("review queue unexpectedly contains active vectors")
    backup_dir = settings.paths.data_dir / "backups" / f"editorial-review-{stamp}"
    database_backup, exclusions_backup = backup_editorial_state(database, backup_dir)
    registry = DoiExclusionRegistry.for_database(database.path)
    registry.exclude_many(
        {
            "doi": item["doi"],
            "title": item["title"],
            "reason": item["editorial_reason"],
            "origin": "historical_editorial_review",
        }
        for item in rejected
    )
    accepted_ids = [str(item["record_id"]) for item in accepted]
    rejected_ids = [str(item["record_id"]) for item in rejected]
    with database.transaction() as connection:
        current = {
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM bibliographic_records WHERE relevance_status = 'review'"
            )
        }
        expected = set(accepted_ids).union(rejected_ids)
        if current != expected:
            raise RuntimeError("review queue changed after audit; no decision was applied")
        connection.executemany(
            """
            UPDATE bibliographic_records
            SET relevance_status = 'accepted',
                embedding_status = CASE
                    WHEN abstract IS NULL OR trim(abstract) = ''
                    THEN 'not_applicable' ELSE 'pending' END,
                manual_decision = 'accepted',
                manual_reviewed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND relevance_status = 'review'
            """,
            [(record_id,) for record_id in accepted_ids],
        )
        connection.executemany(
            "DELETE FROM bibliographic_records WHERE id = ? AND relevance_status = 'review'",
            [(record_id,) for record_id in rejected_ids],
        )
    report["state"] = "applied"
    report["backup"] = {
        "database": str(database_backup),
        "doi_exclusions": str(exclusions_backup) if exclusions_backup else None,
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"report": str(output), **report["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
