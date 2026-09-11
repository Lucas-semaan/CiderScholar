"""Audit and repair Scopus source titles from local text exports, without network access."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
import unicodedata
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.file_integrity import sha256_file as _sha256_file
from app.updates.scopus_text import (
    ScopusSourceTitlePlan,
    ScopusTextRecord,
    apply_scopus_source_title_plan,
    parse_scopus_text_export,
    plan_scopus_source_title_reconciliation,
    plan_updates_as_dicts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Local Scopus text exports")
    parser.add_argument("--run-dir", type=Path, help="New audit directory")
    parser.add_argument("--config", type=Path, help="Optional config.yaml")
    parser.add_argument("--apply", action="store_true", help="Apply the audited repair")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = settings_for_corpus(load_settings(arguments.config), CorpusScope.COMMON)
    database = Database(settings.paths.database_path)
    if not database.path.is_file():
        raise FileNotFoundError(f"common corpus database not found: {database.path}")
    inputs = [path.resolve() for path in arguments.inputs]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Scopus exports not found: {missing}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (
        arguments.run_dir.resolve()
        if arguments.run_dir
        else settings.paths.exports_dir / "scopus-source-title-reconciliation" / stamp
    )
    run_dir.mkdir(parents=True, exist_ok=False)

    records: list[ScopusTextRecord] = []
    manifests = []
    preserved_dir = run_dir / "inputs"
    preserved_dir.mkdir()
    for path in inputs:
        parsed = parse_scopus_text_export(path)
        records.extend(parsed)
        digest = _sha256_file(path)
        destination = preserved_dir / path.name
        if destination.exists():
            destination = preserved_dir / f"{path.stem}-{digest[:12]}{path.suffix}"
        shutil.copy2(path, destination)
        manifests.append(
            {
                "path": str(path),
                "preserved_copy": str(destination),
                "bytes": path.stat().st_size,
                "sha256": digest,
                "records": len(parsed),
            }
        )

    with closing(_readonly_connection(database.path)) as connection:
        _require_no_running_harvest(connection)
        plan = plan_scopus_source_title_reconciliation(connection, records)
    _write_plan(run_dir, plan)
    distribution = _write_source_title_distribution(run_dir, records)
    with closing(_readonly_connection(database.path)) as connection:
        article_distribution = _write_article_source_title_distribution(
            run_dir,
            connection,
            records,
        )
    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "state": "dry_run",
        "local_first": True,
        "network_calls": 0,
        "database": str(database.path.resolve()),
        "inputs": manifests,
        "parse": {
            "records": len(records),
            "valid_dois": sum(record.doi is not None for record in records),
            "invalid_or_missing_dois": sum(record.doi is None for record in records),
            "missing_source_titles": 0,
            "explicit_publisher_fields": 0,
        },
        "plan": plan.summary(),
        "source_title_distribution": {
            "by_unique_scopus_eid": distribution,
            "by_distinct_article": article_distribution,
        },
        "backup": None,
        "apply": None,
        "verification": None,
    }
    report_path = run_dir / "report.json"
    _write_json(report_path, report)
    if not arguments.apply:
        print(json.dumps({"report": str(report_path), **plan.summary()}, ensure_ascii=False))
        return 0

    backup = _backup_database(database.path, run_dir / "pre-reconciliation.sqlite3")
    report["backup"] = backup
    _write_json(report_path, report)
    database.initialize()
    with database.transaction() as connection:
        refreshed = plan_scopus_source_title_reconciliation(connection, records)
        if refreshed.database_snapshot_sha256 != plan.database_snapshot_sha256:
            raise RuntimeError("bibliographic source metadata changed after the dry-run audit")
        applied = apply_scopus_source_title_plan(connection, refreshed)
    verification = _verify(database.path, records)
    if verification["remaining_source_metadata_updates"] != 0:
        raise RuntimeError("Scopus per-source titles remain incomplete after reconciliation")
    if verification["remaining_archived_source_metadata_updates"] != 0:
        raise RuntimeError("archived Scopus per-source titles remain incomplete")
    if verification["remaining_journal_repairs"] != 0:
        raise RuntimeError("Scopus scalar journal repairs remain after reconciliation")
    report["state"] = "applied"
    report["applied_at"] = datetime.now(UTC).isoformat()
    report["apply"] = applied
    report["verification"] = verification
    _write_json(report_path, report)
    print(json.dumps({"report": str(report_path), **applied, **verification}, ensure_ascii=False))
    return 0


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _require_no_running_harvest(connection: sqlite3.Connection) -> None:
    running = int(
        connection.execute(
            "SELECT COUNT(*) FROM bibliographic_harvest_runs WHERE state = 'running'"
        ).fetchone()[0]
    )
    if running:
        raise RuntimeError("a bibliographic harvest is active; source titles were not changed")


def _backup_database(source_path: Path, destination: Path) -> dict[str, Any]:
    with (
        closing(_readonly_connection(source_path)) as source,
        closing(sqlite3.connect(destination)) as target,
    ):
        source.backup(target)
    with closing(sqlite3.connect(destination)) as verified:
        quick_check = str(verified.execute("PRAGMA quick_check").fetchone()[0])
    if quick_check != "ok":
        raise RuntimeError("pre-reconciliation SQLite backup failed quick_check")
    return {
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": _sha256_file(destination),
        "sqlite_quick_check": quick_check,
    }


def _verify(path: Path, records: list[ScopusTextRecord]) -> dict[str, Any]:
    with closing(_readonly_connection(path)) as connection:
        plan = plan_scopus_source_title_reconciliation(connection, records)
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_keys = len(list(connection.execute("PRAGMA foreign_key_check")))
        bibliographic_count = int(
            connection.execute("SELECT COUNT(*) FROM bibliographic_records").fetchone()[0]
        )
        fts_count = int(
            connection.execute("SELECT COUNT(*) FROM bibliographic_records_fts").fetchone()[0]
        )
        scopus_source_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM bibliographic_record_sources WHERE source = 'scopus'"
            ).fetchone()[0]
        )
        scopus_source_titles = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM bibliographic_record_sources
                WHERE source = 'scopus' AND trim(coalesce(source_title, '')) != ''
                """
            ).fetchone()[0]
        )
        archived_scopus_source_titles = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM rejected_bibliographic_record_sources
                WHERE source = 'scopus' AND trim(coalesce(source_title, '')) != ''
                """
            ).fetchone()[0]
        )
    return {
        "sqlite_quick_check": quick_check,
        "foreign_key_violations": foreign_keys,
        "bibliographic_records": bibliographic_count,
        "bibliographic_fts_records": fts_count,
        "fts_matches_database": fts_count == bibliographic_count,
        "active_scopus_source_rows": scopus_source_rows,
        "active_scopus_source_titles": scopus_source_titles,
        "archived_scopus_source_titles": archived_scopus_source_titles,
        "remaining_source_metadata_updates": len(plan.source_metadata_updates),
        "remaining_archived_source_metadata_updates": len(plan.archived_source_metadata_updates),
        "remaining_journal_repairs": len(plan.journal_updates),
        "blocking_conflicts": plan.summary()["blocking_conflicts"],
    }


def _write_plan(run_dir: Path, plan: ScopusSourceTitlePlan) -> None:
    updates = plan_updates_as_dicts(plan)
    for key, values in updates.items():
        path = run_dir / f"{key.replace('_', '-')}.jsonl"
        path.write_text(
            "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
            encoding="utf-8",
        )
    _write_json(
        run_dir / "unmatched-source-ids.json",
        {"source_ids": list(plan.unmatched_source_ids)},
    )


def _write_source_title_distribution(
    run_dir: Path,
    records: list[ScopusTextRecord],
) -> dict[str, Any]:
    by_source_id: dict[str, ScopusTextRecord] = {}
    for record in records:
        current = by_source_id.get(record.source_id)
        if current is None or len(record.citation.source_title) > len(
            current.citation.source_title
        ):
            by_source_id[record.source_id] = record
    counts = Counter(record.citation.source_title for record in by_source_id.values())
    total = len(by_source_id)
    rows = [
        {
            "source_title": title,
            "count": count,
            "percent_of_unique_scopus_eids": round(100 * count / total, 6),
        }
        for title, count in counts.most_common()
    ]
    _write_json(run_dir / "source-title-summary.json", {"total": total, "rows": rows})
    with (run_dir / "source-title-summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return {
        "denominator": "unique_scopus_eids",
        "total": total,
        "unique_source_titles": len(rows),
        "json": str(run_dir / "source-title-summary.json"),
        "csv": str(run_dir / "source-title-summary.csv"),
        "top": rows[:20],
    }


def _write_article_source_title_distribution(
    run_dir: Path,
    connection: sqlite3.Connection,
    records: list[ScopusTextRecord],
) -> dict[str, Any]:
    by_source_id: dict[str, ScopusTextRecord] = {}
    for record in records:
        current = by_source_id.get(record.source_id)
        if current is None or len(record.citation.source_title) > len(
            current.citation.source_title
        ):
            by_source_id[record.source_id] = record
    active: dict[str, list[str]] = {}
    matched_source_ids: set[str] = set()
    for row in connection.execute(
        """
        SELECT record_id, source_id
        FROM bibliographic_record_sources
        WHERE source = 'scopus'
        ORDER BY record_id, source_id
        """
    ):
        source_id = str(row["source_id"])
        observation = by_source_id.get(source_id)
        if observation is None:
            continue
        matched_source_ids.add(source_id)
        active.setdefault(str(row["record_id"]), []).append(observation.citation.source_title)
    identities: dict[str, list[str]] = {
        f"active:{record_id}": titles for record_id, titles in active.items()
    }
    for source_id, record in by_source_id.items():
        if source_id in matched_source_ids:
            continue
        if record.doi:
            identity = f"doi:{record.doi}"
        else:
            identity = f"title:{_canonical_title(record.title)}:{record.citation.publication_year}"
        identities.setdefault(identity, []).append(record.citation.source_title)
    counts = Counter(_preferred_title(titles) for titles in identities.values())
    total = len(identities)
    rows = [
        {
            "source_title": title,
            "count": count,
            "percent_of_distinct_articles": round(100 * count / total, 6),
        }
        for title, count in counts.most_common()
    ]
    json_path = run_dir / "source-title-summary-by-article.json"
    csv_path = run_dir / "source-title-summary-by-article.csv"
    _write_json(json_path, {"total": total, "rows": rows})
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return {
        "denominator": "distinct_articles_doi_first_then_title_year",
        "total": total,
        "active_articles": len(active),
        "archived_or_absent_articles": total - len(active),
        "unique_source_titles": len(rows),
        "json": str(json_path),
        "csv": str(csv_path),
        "top": rows[:20],
    }


def _preferred_title(titles: list[str]) -> str:
    return max(dict.fromkeys(titles), key=lambda title: (len(title), title.casefold()))


def _canonical_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).casefold()
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_title)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
