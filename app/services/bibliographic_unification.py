"""Consolidate legacy bibliographic runs into the authoritative scientific database."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.database.sqlite import Database


class BibliographicUnificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_database: str
    target_database: str
    run_ids: list[str]
    source_records: int = Field(ge=0)
    inserted_records: int = Field(ge=0)
    matched_records: int = Field(ge=0)
    inserted_sources: int = Field(ge=0)
    inserted_runs: int = Field(ge=0)
    inserted_hits: int = Field(ge=0)
    inserted_pdf_assets: int = Field(ge=0)
    inserted_native_assets: int = Field(ge=0)
    inserted_cooldowns: int = Field(ge=0)
    target_records: int = Field(ge=0)
    target_fts_records: int = Field(ge=0)


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]


def _rows_for_values(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    values: Sequence[str],
) -> list[sqlite3.Row]:
    if not values:
        return []
    placeholders = ", ".join("?" for _ in values)
    return list(
        connection.execute(
            f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY rowid",
            tuple(values),
        )
    )


def _insert_shared_row(
    connection: sqlite3.Connection,
    *,
    table: str,
    row: sqlite3.Row,
    target_columns: Sequence[str],
    updates: dict[str, Any] | None = None,
) -> int:
    values = dict(row)
    values.update(updates or {})
    columns = [column for column in target_columns if column in values]
    placeholders = ", ".join("?" for _ in columns)
    names = ", ".join(columns)
    cursor = connection.execute(
        f"INSERT OR IGNORE INTO {table} ({names}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )
    return max(0, int(cursor.rowcount))


def merge_bibliographic_runs(
    source: Database,
    target: Database,
    *,
    run_ids: Sequence[str],
) -> BibliographicUnificationReport:
    """Copy selected runs without creating a second bibliographic authority.

    The legacy database remains untouched. DOI and canonical-key matches map to the
    existing target record, while manual target decisions are never overwritten.
    """

    source_path = source.path.resolve()
    target_path = target.path.resolve()
    if source_path == target_path:
        raise ValueError("source and authoritative scientific database must be different")
    selected_runs = list(dict.fromkeys(value.strip() for value in run_ids if value.strip()))
    if not selected_runs:
        raise ValueError("at least one bibliographic harvest run is required")
    if not source_path.is_file():
        raise FileNotFoundError(f"legacy bibliographic database not found: {source_path}")

    target.initialize()
    with closing(_read_only(source_path)) as source_connection:
        runs = _rows_for_values(
            source_connection,
            "bibliographic_harvest_runs",
            "id",
            selected_runs,
        )
        found_runs = {str(row["id"]) for row in runs}
        missing_runs = sorted(set(selected_runs) - found_runs)
        if missing_runs:
            raise ValueError(f"unknown bibliographic harvest runs: {', '.join(missing_runs)}")
        hits = _rows_for_values(
            source_connection,
            "bibliographic_harvest_hits",
            "run_id",
            selected_runs,
        )
        record_ids = list(dict.fromkeys(str(row["record_id"]) for row in hits))
        records = _rows_for_values(
            source_connection,
            "bibliographic_records",
            "id",
            record_ids,
        )
        if len(records) != len(record_ids):
            raise RuntimeError("one selected harvest hit has no bibliographic record")
        sources = _rows_for_values(
            source_connection,
            "bibliographic_record_sources",
            "record_id",
            record_ids,
        )
        pdf_assets = _rows_for_values(
            source_connection,
            "full_text_assets",
            "record_id",
            record_ids,
        )
        native_assets = _rows_for_values(
            source_connection,
            "native_full_text_assets",
            "record_id",
            record_ids,
        )
        asset_sources = sorted(
            {str(row["source"]) for row in [*pdf_assets, *native_assets] if row["source"]}
        )
        cooldowns = _rows_for_values(
            source_connection,
            "full_text_provider_cooldowns",
            "source",
            asset_sources,
        )

    inserted_records = 0
    matched_records = 0
    inserted_sources = 0
    inserted_runs = 0
    inserted_hits = 0
    inserted_pdf_assets = 0
    inserted_native_assets = 0
    inserted_cooldowns = 0

    with target.transaction() as connection:
        target_columns = {
            table: _columns(connection, table)
            for table in (
                "bibliographic_records",
                "bibliographic_record_sources",
                "bibliographic_harvest_runs",
                "bibliographic_harvest_hits",
                "full_text_assets",
                "native_full_text_assets",
                "full_text_provider_cooldowns",
            )
        }
        target_records = list(
            connection.execute(
                "SELECT id, canonical_key, doi FROM bibliographic_records ORDER BY rowid"
            )
        )
        by_id = {str(row["id"]): str(row["id"]) for row in target_records}
        by_key = {str(row["canonical_key"]): str(row["id"]) for row in target_records}
        by_doi = {
            str(row["doi"]).strip().casefold(): str(row["id"])
            for row in target_records
            if row["doi"]
        }
        mapping: dict[str, str] = {}
        for row in records:
            source_id = str(row["id"])
            doi = str(row["doi"]).strip().casefold() if row["doi"] else None
            target_id = (by_doi.get(doi) if doi else None) or by_key.get(str(row["canonical_key"]))
            if target_id is None and source_id in by_id:
                raise RuntimeError("bibliographic record id collides with another identity")
            if target_id is None:
                abstract = str(row["abstract"] or "").strip()
                embedding_status = (
                    "pending"
                    if row["relevance_status"] == "accepted" and abstract
                    else "not_applicable"
                )
                inserted_records += _insert_shared_row(
                    connection,
                    table="bibliographic_records",
                    row=row,
                    target_columns=target_columns["bibliographic_records"],
                    updates={"embedding_status": embedding_status},
                )
                target_id = source_id
                by_id[source_id] = target_id
                by_key[str(row["canonical_key"])] = target_id
                if doi:
                    by_doi[doi] = target_id
            else:
                matched_records += 1
            mapping[source_id] = target_id

        for row in runs:
            inserted_runs += _insert_shared_row(
                connection,
                table="bibliographic_harvest_runs",
                row=row,
                target_columns=target_columns["bibliographic_harvest_runs"],
            )
        for row in sources:
            inserted_sources += _insert_shared_row(
                connection,
                table="bibliographic_record_sources",
                row=row,
                target_columns=target_columns["bibliographic_record_sources"],
                updates={"record_id": mapping[str(row["record_id"])]},
            )
        for row in hits:
            inserted_hits += _insert_shared_row(
                connection,
                table="bibliographic_harvest_hits",
                row=row,
                target_columns=target_columns["bibliographic_harvest_hits"],
                updates={"record_id": mapping[str(row["record_id"])]},
            )

        article_ids = {
            str(row[0]) for row in connection.execute("SELECT id FROM articles ORDER BY rowid")
        }
        for row in pdf_assets:
            article_id = str(row["article_id"]) if row["article_id"] else None
            updates: dict[str, Any] = {
                "record_id": mapping[str(row["record_id"])],
                "article_id": article_id if article_id in article_ids else None,
            }
            if article_id and article_id not in article_ids and row["state"] == "ingested":
                updates["state"] = "downloaded" if row["file_path"] else "available"
            inserted_pdf_assets += _insert_shared_row(
                connection,
                table="full_text_assets",
                row=row,
                target_columns=target_columns["full_text_assets"],
                updates=updates,
            )
        for row in native_assets:
            inserted_native_assets += _insert_shared_row(
                connection,
                table="native_full_text_assets",
                row=row,
                target_columns=target_columns["native_full_text_assets"],
                updates={"record_id": mapping[str(row["record_id"])]},
            )
        for row in cooldowns:
            inserted_cooldowns += _insert_shared_row(
                connection,
                table="full_text_provider_cooldowns",
                row=row,
                target_columns=target_columns["full_text_provider_cooldowns"],
            )

        target_record_count = int(
            connection.execute("SELECT COUNT(*) FROM bibliographic_records").fetchone()[0]
        )
        target_fts_count = int(
            connection.execute("SELECT COUNT(*) FROM bibliographic_records_fts").fetchone()[0]
        )
        if target_fts_count != target_record_count:
            raise RuntimeError("bibliographic SQLite and FTS counts diverged during unification")

    return BibliographicUnificationReport(
        source_database=str(source_path),
        target_database=str(target_path),
        run_ids=selected_runs,
        source_records=len(records),
        inserted_records=inserted_records,
        matched_records=matched_records,
        inserted_sources=inserted_sources,
        inserted_runs=inserted_runs,
        inserted_hits=inserted_hits,
        inserted_pdf_assets=inserted_pdf_assets,
        inserted_native_assets=inserted_native_assets,
        inserted_cooldowns=inserted_cooldowns,
        target_records=target_record_count,
        target_fts_records=target_fts_count,
    )
