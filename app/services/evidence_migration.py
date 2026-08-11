"""Verified transfer of legacy persisted evidence into the common scientific corpus."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.corpora import CorpusScope, LocalProfile, authorize_corpus_mutation
from app.database.sqlite import Database

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "queries": (
        "id",
        "original_query",
        "expanded_queries",
        "created_at",
        "duration_seconds",
        "selected_article_ids",
        "corpus_version",
        "model_version",
        "parameters_hash",
    ),
    "article_evidence_runs": (
        "query_id",
        "article_id",
        "state",
        "relevance_score",
        "question_addressed",
        "topics",
        "contradictions",
        "missing_information",
        "selected_chunk_ids",
        "attempt_count",
        "error_type",
        "error_message",
        "created_at",
        "updated_at",
    ),
    "evidence": (
        "id",
        "query_id",
        "article_id",
        "chunk_id",
        "claim",
        "source_excerpt",
        "page_start",
        "page_end",
        "relevance_score",
    ),
    "synthesis_runs": (
        "query_id",
        "state",
        "model_version",
        "theme_plan",
        "final_synthesis",
        "answer_markdown",
        "cited_evidence_ids",
        "attempt_count",
        "error_type",
        "error_message",
        "created_at",
        "updated_at",
    ),
    "theme_synthesis_runs": (
        "query_id",
        "theme_id",
        "state",
        "theme_label",
        "article_ids",
        "synthesis_json",
        "attempt_count",
        "error_type",
        "error_message",
        "created_at",
        "updated_at",
    ),
}


class EvidenceMigrationError(RuntimeError):
    """Persisted evidence cannot be safely transferred to the common corpus."""


@dataclass(frozen=True, slots=True)
class EvidenceMigrationReport:
    applied: bool
    backup_path: str | None
    backup_sha256: str | None
    backup_size_bytes: int | None
    source_counts: dict[str, int]
    inserted_counts: dict[str, int]
    already_present_counts: dict[str, int]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def _read_only_connection(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.is_file():
        raise EvidenceMigrationError(f"database is unavailable: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return closing(connection)


def _backup_database(source: Path, destination: Path) -> tuple[str, int]:
    """Create and verify an online SQLite snapshot without duplicating corpus assets."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    with (
        closing(sqlite3.connect(source_uri, uri=True, timeout=30.0)) as origin,
        closing(sqlite3.connect(destination)) as target,
    ):
        origin.backup(target)
    with _read_only_connection(destination) as verified:
        if verified.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise EvidenceMigrationError("evidence migration backup failed quick_check")
    digest = hashlib.sha256()
    with destination.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest(), destination.stat().st_size


def _rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    columns = ", ".join(TABLE_COLUMNS[table])
    try:
        return connection.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall()
    except sqlite3.OperationalError as error:
        raise EvidenceMigrationError(f"required table is unavailable: {table}") from error


def _decode_id_list(raw: object, *, label: str) -> list[str]:
    try:
        values = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError) as error:
        raise EvidenceMigrationError(f"{label} is not valid JSON") from error
    if not isinstance(values, list) or any(not isinstance(value, (str, int)) for value in values):
        raise EvidenceMigrationError(f"{label} is not an identifier list")
    return [str(value) for value in values]


def _article_ids(rows_by_table: dict[str, list[sqlite3.Row]]) -> set[str]:
    identifiers = {
        str(row["article_id"])
        for table in ("article_evidence_runs", "evidence")
        for row in rows_by_table[table]
    }
    for row in rows_by_table["queries"]:
        identifiers.update(
            _decode_id_list(row["selected_article_ids"], label="queries.selected_article_ids")
        )
    return identifiers


def _chunk_ids(rows_by_table: dict[str, list[sqlite3.Row]]) -> set[int]:
    identifiers = {int(row["chunk_id"]) for row in rows_by_table["evidence"]}
    for row in rows_by_table["article_evidence_runs"]:
        try:
            identifiers.update(
                int(value)
                for value in _decode_id_list(
                    row["selected_chunk_ids"],
                    label="article_evidence_runs.selected_chunk_ids",
                )
            )
        except ValueError as error:
            raise EvidenceMigrationError("selected_chunk_ids contains a non-integer id") from error
    return identifiers


def _single_row(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[object, ...],
    *,
    label: str,
) -> sqlite3.Row:
    row = connection.execute(query, parameters).fetchone()
    if row is None:
        raise EvidenceMigrationError(f"missing {label} in common corpus")
    return row


def _preflight_scientific_references(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    rows_by_table: dict[str, list[sqlite3.Row]],
) -> None:
    if list(source.execute("PRAGMA foreign_key_check")):
        raise EvidenceMigrationError("legacy database has foreign-key violations")
    if list(target.execute("PRAGMA foreign_key_check")):
        raise EvidenceMigrationError("common corpus has foreign-key violations")

    for article_id in sorted(_article_ids(rows_by_table)):
        source_article = _single_row(
            source,
            "SELECT id, sha256 FROM articles WHERE id = ?",
            (article_id,),
            label=f"legacy article {article_id}",
        )
        target_article = _single_row(
            target,
            "SELECT id, sha256 FROM articles WHERE id = ?",
            (article_id,),
            label=f"article {article_id}",
        )
        if tuple(source_article) != tuple(target_article):
            raise EvidenceMigrationError(f"article SHA-256 differs for {article_id}")

    for chunk_id in sorted(_chunk_ids(rows_by_table)):
        source_chunk = _single_row(
            source,
            "SELECT id, article_id, page_start, page_end, text FROM chunks WHERE id = ?",
            (chunk_id,),
            label=f"legacy chunk {chunk_id}",
        )
        target_chunk = _single_row(
            target,
            "SELECT id, article_id, page_start, page_end, text FROM chunks WHERE id = ?",
            (chunk_id,),
            label=f"chunk {chunk_id}",
        )
        if tuple(source_chunk) != tuple(target_chunk):
            raise EvidenceMigrationError(f"chunk identity differs for {chunk_id}")

    for row in rows_by_table["article_evidence_runs"]:
        article_id = str(row["article_id"])
        for chunk_id in _decode_id_list(
            row["selected_chunk_ids"],
            label="article_evidence_runs.selected_chunk_ids",
        ):
            target_chunk = _single_row(
                target,
                "SELECT article_id FROM chunks WHERE id = ?",
                (int(chunk_id),),
                label=f"chunk {chunk_id}",
            )
            if str(target_chunk["article_id"]) != article_id:
                raise EvidenceMigrationError("selected evidence chunk belongs to another article")

    for row in rows_by_table["evidence"]:
        target_chunk = _single_row(
            target,
            "SELECT article_id, page_start, page_end, text FROM chunks WHERE id = ?",
            (int(row["chunk_id"]),),
            label=f"chunk {row['chunk_id']}",
        )
        if str(target_chunk["article_id"]) != str(row["article_id"]):
            raise EvidenceMigrationError("evidence chunk belongs to another article")
        if int(target_chunk["page_start"]) != int(row["page_start"]) or int(
            target_chunk["page_end"]
        ) != int(row["page_end"]):
            raise EvidenceMigrationError("evidence pages differ from the common corpus")
        if str(row["source_excerpt"]) not in str(target_chunk["text"]):
            raise EvidenceMigrationError("evidence excerpt is not verbatim common-corpus text")


def _key_columns(table: str) -> tuple[str, ...]:
    if table == "queries" or table == "evidence" or table == "synthesis_runs":
        return ("id",) if table != "synthesis_runs" else ("query_id",)
    if table == "article_evidence_runs":
        return ("query_id", "article_id")
    return ("query_id", "theme_id")


def _target_row(
    connection: sqlite3.Connection, table: str, source_row: sqlite3.Row
) -> sqlite3.Row | None:
    columns = TABLE_COLUMNS[table]
    keys = _key_columns(table)
    where = " AND ".join(f"{key} = ?" for key in keys)
    values = tuple(source_row[key] for key in keys)
    return connection.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE {where}", values
    ).fetchone()


def _validate_target_rows(
    target: sqlite3.Connection, rows_by_table: dict[str, list[sqlite3.Row]]
) -> dict[str, int]:
    existing: dict[str, int] = {}
    for table, rows in rows_by_table.items():
        existing[table] = 0
        for row in rows:
            target_row = _target_row(target, table, row)
            if target_row is None:
                continue
            if tuple(target_row) != tuple(row[column] for column in TABLE_COLUMNS[table]):
                raise EvidenceMigrationError(f"conflicting {table} record already exists in common")
            existing[table] += 1
    return existing


def _insert_missing(
    connection: sqlite3.Connection,
    table: str,
    rows: list[sqlite3.Row],
) -> int:
    columns = TABLE_COLUMNS[table]
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    inserted = 0
    for row in rows:
        if _target_row(connection, table, row) is not None:
            continue
        connection.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            tuple(row[column] for column in columns),
        )
        inserted += 1
    return inserted


def migrate_legacy_evidence(
    settings: Settings,
    *,
    profile: LocalProfile,
    apply: bool = False,
) -> EvidenceMigrationReport:
    """Migrate only traceable scientific evidence; dry-run unless explicitly applied."""

    source_path = settings.paths.database_path
    target_path = settings.paths.common_database_path
    if source_path.resolve() == target_path.resolve():
        raise EvidenceMigrationError("legacy and common databases must be different")
    if apply:
        authorize_corpus_mutation(CorpusScope.COMMON, profile)
        Database(target_path).initialize()

    with _read_only_connection(source_path) as source, _read_only_connection(target_path) as target:
        rows_by_table = {table: _rows(source, table) for table in TABLE_COLUMNS}
        _preflight_scientific_references(source, target, rows_by_table)
        already_present = _validate_target_rows(target, rows_by_table)

    source_counts = {table: len(rows) for table, rows in rows_by_table.items()}
    inserted_counts = {table: 0 for table in TABLE_COLUMNS}
    backup_path: str | None = None
    backup_sha256: str | None = None
    backup_size_bytes: int | None = None
    if apply:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = (
            settings.paths.data_dir
            / "backups"
            / "sqlite"
            / f"pre-evidence-migration-{stamp}.sqlite3"
        )
        backup_sha256, backup_size_bytes = _backup_database(target_path, backup)
        backup_path = str(backup)
        database = Database(target_path)
        with database.transaction() as connection:
            existing_after_backup = _validate_target_rows(connection, rows_by_table)
            if existing_after_backup != already_present:
                raise EvidenceMigrationError("common evidence changed after preflight")
            for table in TABLE_COLUMNS:
                inserted_counts[table] = _insert_missing(connection, table, rows_by_table[table])
            if list(connection.execute("PRAGMA foreign_key_check")):
                raise EvidenceMigrationError(
                    "common corpus foreign-key check failed after migration"
                )

    return EvidenceMigrationReport(
        applied=apply,
        backup_path=backup_path,
        backup_sha256=backup_sha256,
        backup_size_bytes=backup_size_bytes,
        source_counts=source_counts,
        inserted_counts=inserted_counts,
        already_present_counts=already_present,
    )
