"""Reconsider rejected historical notices after an editorial-scope correction."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from app.config import load_settings
from app.database.sqlite import Database
from app.services.corpus_migration import (
    BIBLIOGRAPHIC_COLUMNS,
    BIBLIOGRAPHIC_SOURCE_COLUMNS,
)
from app.updates.doi_exclusions import DoiExclusionRegistry
from app.updates.editorial_scope import classify_editorial_record
from scripts.review_historical_titles import backup_editorial_state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_report", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    source_report = arguments.source_report.resolve()
    payload = json.loads(source_report.read_text(encoding="utf-8"))
    if payload.get("state") != "applied":
        raise ValueError("the source editorial report was not applied")
    backup_path = Path(payload["backup"]["database"]).resolve()
    if not backup_path.is_file():
        raise FileNotFoundError(f"editorial backup unavailable: {backup_path}")
    rejected_ids = {
        str(item["record_id"]) for item in payload["records"] if item["decision"] == "rejected"
    }

    with closing(sqlite3.connect(backup_path)) as backup:
        backup.row_factory = sqlite3.Row
        records = []
        sources: dict[str, list[sqlite3.Row]] = {}
        for record_id in sorted(rejected_ids):
            row = backup.execute(
                "SELECT * FROM bibliographic_records WHERE id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                continue
            decision = classify_editorial_record(dict(row))
            if decision.decision != "accepted":
                continue
            records.append((row, decision.reason))
            sources[record_id] = list(
                backup.execute(
                    "SELECT * FROM bibliographic_record_sources WHERE record_id = ?",
                    (record_id,),
                )
            )

    settings = load_settings()
    database = Database(settings.paths.database_path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    decisions = [
        {
            "record_id": str(row["id"]),
            "title": str(row["title"]),
            "doi": row["doi"],
            "theme": row["relevance_theme"],
            "has_abstract": bool(row["abstract"] and str(row["abstract"]).strip()),
            "decision": "accepted",
            "editorial_reason": reason,
        }
        for row, reason in records
    ]
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_report": str(source_report),
        "policy_correction": (
            "Les matrices périphériques sont admises lorsqu'un mécanisme ou procédé "
            "est explicitement transférable au cidre."
        ),
        "state": "dry_run",
        "counts": {
            "reconsidered": len(rejected_ids),
            "reinstated": len(decisions),
            "reinstated_with_abstract": sum(item["has_abstract"] for item in decisions),
        },
        "records": decisions,
    }
    output = settings.paths.exports_dir / f"historical-title-reconsideration-{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not arguments.apply:
        print(json.dumps({"report": str(output), **report["counts"]}, ensure_ascii=False))
        return 0

    backup_dir = settings.paths.data_dir / "backups" / f"editorial-reconsideration-{stamp}"
    database_backup, exclusions_backup = backup_editorial_state(database, backup_dir)
    insert_columns = ", ".join(BIBLIOGRAPHIC_COLUMNS)
    placeholders = ", ".join("?" for _ in BIBLIOGRAPHIC_COLUMNS)
    source_columns = ", ".join(BIBLIOGRAPHIC_SOURCE_COLUMNS)
    source_placeholders = ", ".join("?" for _ in BIBLIOGRAPHIC_SOURCE_COLUMNS)
    restored = 0
    deduplicated = 0
    restored_dois: list[str] = []
    with database.transaction() as connection:
        for row, reason in records:
            record_id = str(row["id"])
            existing = connection.execute(
                """
                SELECT id FROM bibliographic_records
                WHERE id = ? OR canonical_key = ?
                    OR (doi IS NOT NULL AND lower(doi) = lower(?))
                LIMIT 1
                """,
                (record_id, row["canonical_key"], row["doi"]),
            ).fetchone()
            if existing is not None:
                deduplicated += 1
                if row["doi"]:
                    restored_dois.append(str(row["doi"]))
                continue
            values = []
            for column in BIBLIOGRAPHIC_COLUMNS:
                if column == "relevance_status":
                    values.append("accepted")
                elif column == "relevance_reason":
                    values.append(reason)
                elif column == "embedding_status":
                    values.append(
                        "pending"
                        if row["abstract"] and str(row["abstract"]).strip()
                        else "not_applicable"
                    )
                elif column == "manual_decision":
                    values.append("accepted")
                elif column == "manual_reviewed_at":
                    values.append(datetime.now(UTC).isoformat())
                else:
                    values.append(row[column])
            connection.execute(
                f"INSERT INTO bibliographic_records ({insert_columns}) VALUES ({placeholders})",
                values,
            )
            connection.executemany(
                f"INSERT OR IGNORE INTO bibliographic_record_sources ({source_columns}) "
                f"VALUES ({source_placeholders})",
                [
                    tuple(source[column] for column in BIBLIOGRAPHIC_SOURCE_COLUMNS)
                    for source in sources[record_id]
                ],
            )
            restored += 1
            if row["doi"]:
                restored_dois.append(str(row["doi"]))

    registry = DoiExclusionRegistry.for_database(database.path)
    reinstated_dois = registry.reinstate_many(restored_dois)
    report["state"] = "applied"
    report["counts"]["restored"] = restored
    report["counts"]["deduplicated"] = deduplicated
    report["counts"]["doi_exclusions_reinstated"] = reinstated_dois
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
