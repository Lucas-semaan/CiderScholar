"""One-time, admin-only migration from legacy paths to the common corpus."""

from __future__ import annotations

import hashlib
import shutil
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.corpora import (
    CorpusScope,
    LocalProfile,
    authorize_corpus_mutation,
    corpus_paths,
)
from app.database.sqlite import Database

ARTICLE_COLUMNS = (
    "id",
    "sha256",
    "doi",
    "title",
    "abstract",
    "authors",
    "journal",
    "publication_year",
    "language",
    "pdf_path",
    "validation_status",
    "source",
    "created_at",
    "indexed_at",
)
CHUNK_COLUMNS = (
    "id",
    "article_id",
    "section",
    "subsection",
    "page_start",
    "page_end",
    "chunk_index",
    "text",
    "token_count",
    "embedding_status",
)
INGESTION_COLUMNS = (
    "id",
    "pdf_path",
    "sha256",
    "state",
    "article_id",
    "error_type",
    "error_message",
    "attempt_count",
    "created_at",
    "updated_at",
)
BIBLIOGRAPHIC_COLUMNS = (
    "id",
    "canonical_key",
    "doi",
    "title",
    "abstract",
    "authors",
    "journal",
    "publication_year",
    "citation_count",
    "url",
    "content_hash",
    "embedding_status",
    "created_at",
    "updated_at",
    "relevance_status",
    "relevance_score",
    "relevance_reason",
    "relevance_theme",
    "manual_decision",
    "manual_reviewed_at",
)
BIBLIOGRAPHIC_SOURCE_COLUMNS = (
    "record_id",
    "source",
    "source_id",
    "first_seen_at",
    "last_seen_at",
)


class CorpusMigrationError(RuntimeError):
    """Legacy data cannot be copied without ambiguity or loss."""


@dataclass(frozen=True, slots=True)
class CorpusMigrationReport:
    source_articles: int
    target_articles: int
    source_chunks: int
    target_chunks: int
    copied_pdfs: int
    doi_fingerprint: str
    source_abstracts: int
    target_abstracts: int
    abstracts_imported: int
    abstracts_deduplicated: int
    abstracts_skipped_full_text: int


@dataclass(frozen=True, slots=True)
class LegacyAbstractMigrationReport:
    source_abstracts: int
    target_abstracts: int
    abstracts_imported: int
    abstracts_deduplicated: int
    abstracts_skipped_full_text: int


def _rows(database: Database, table: str, columns: tuple[str, ...]):
    selected = ", ".join(columns)
    with closing(database.connect()) as connection:
        return connection.execute(f"SELECT {selected} FROM {table} ORDER BY rowid").fetchall()


def _copy_pdf(source: Path, destination_root: Path, sha256: str) -> Path:
    if not source.is_file():
        raise CorpusMigrationError(f"legacy PDF is unavailable: {source}")
    destination = destination_root / "migrated" / f"{sha256[:12]}-{source.name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source, destination)
    return destination


def _copytree_without_overwrite(source: Path, destination: Path) -> None:
    """Copy legacy caches while preserving every already-present target file."""

    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _insert_rows(connection, table: str, columns: tuple[str, ...], rows) -> None:
    if not rows:
        return
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f"INSERT OR IGNORE INTO {table} ({names}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def _doi_fingerprint(rows) -> str:
    dois = sorted(str(row["doi"]).strip().casefold() for row in rows if row["doi"])
    return hashlib.sha256("\n".join(dois).encode("utf-8")).hexdigest()


def _normalized_doi(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold()


def _eligible_legacy_abstracts(database: Database):
    selected = ", ".join(BIBLIOGRAPHIC_COLUMNS)
    with closing(database.connect()) as connection:
        return connection.execute(
            f"""
            SELECT {selected}
            FROM bibliographic_records
            WHERE relevance_status = 'accepted'
              AND abstract IS NOT NULL
              AND trim(abstract) != ''
            ORDER BY rowid
            """
        ).fetchall()


def _migrate_legacy_abstracts(
    legacy: Database,
    common: Database,
) -> tuple[int, int, int, int, int]:
    """Import accepted abstract-only notices and preserve their provider provenance."""

    records = _eligible_legacy_abstracts(legacy)
    source_rows = _rows(
        legacy,
        "bibliographic_record_sources",
        BIBLIOGRAPHIC_SOURCE_COLUMNS,
    )
    with common.transaction() as connection:
        full_text_dois = {
            normalized
            for row in connection.execute("SELECT doi FROM articles WHERE doi IS NOT NULL")
            if (normalized := _normalized_doi(row["doi"])) is not None
        }
        existing_rows = connection.execute(
            "SELECT id, canonical_key, doi FROM bibliographic_records ORDER BY rowid"
        ).fetchall()
        by_id = {str(row["id"]): str(row["id"]) for row in existing_rows}
        by_canonical_key = {str(row["canonical_key"]): str(row["id"]) for row in existing_rows}
        by_doi = {
            normalized: str(row["id"])
            for row in existing_rows
            if (normalized := _normalized_doi(row["doi"])) is not None
        }
        record_mapping: dict[str, str] = {}
        imported = 0
        deduplicated = 0
        skipped_full_text = 0
        insert_columns = ", ".join(BIBLIOGRAPHIC_COLUMNS)
        placeholders = ", ".join("?" for _ in BIBLIOGRAPHIC_COLUMNS)

        for row in records:
            source_id = str(row["id"])
            doi = _normalized_doi(row["doi"])
            if doi is not None and doi in full_text_dois:
                skipped_full_text += 1
                continue
            target_id = (
                by_id.get(source_id)
                or by_canonical_key.get(str(row["canonical_key"]))
                or (by_doi.get(doi) if doi is not None else None)
            )
            if target_id is None:
                values = [
                    "pending" if column == "embedding_status" else row[column]
                    for column in BIBLIOGRAPHIC_COLUMNS
                ]
                connection.execute(
                    f"""
                    INSERT INTO bibliographic_records ({insert_columns})
                    VALUES ({placeholders})
                    """,
                    values,
                )
                target_id = source_id
                by_id[source_id] = target_id
                by_canonical_key[str(row["canonical_key"])] = target_id
                if doi is not None:
                    by_doi[doi] = target_id
                imported += 1
            else:
                deduplicated += 1
            record_mapping[source_id] = target_id

        for row in source_rows:
            target_id = record_mapping.get(str(row["record_id"]))
            if target_id is None:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO bibliographic_record_sources (
                    record_id, source, source_id, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    row["source"],
                    row["source_id"],
                    row["first_seen_at"],
                    row["last_seen_at"],
                ),
            )
        connection.executemany(
            """
            INSERT OR IGNORE INTO bibliographic_record_sources (
                record_id, source, source_id
            ) VALUES (?, 'legacy', ?)
            """,
            [(target_id, source_id) for source_id, target_id in record_mapping.items()],
        )
        target_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM bibliographic_records
                WHERE relevance_status = 'accepted'
                  AND abstract IS NOT NULL
                  AND trim(abstract) != ''
                """
            ).fetchone()[0]
        )
    return len(records), target_count, imported, deduplicated, skipped_full_text


def migrate_legacy_abstracts(
    settings: Settings,
    *,
    profile: LocalProfile,
) -> LegacyAbstractMigrationReport:
    """Import only accepted legacy abstracts without touching PDF assets or chunk indexes."""

    authorize_corpus_mutation(CorpusScope.COMMON, profile)
    legacy = Database(settings.paths.database_path)
    common = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    if legacy.path.resolve() == common.path.resolve():
        raise CorpusMigrationError("legacy and common databases must be different")
    if not legacy.path.is_file():
        raise CorpusMigrationError(f"legacy database is unavailable: {legacy.path}")
    common.initialize()
    (
        source_abstracts,
        target_abstracts,
        abstracts_imported,
        abstracts_deduplicated,
        abstracts_skipped_full_text,
    ) = _migrate_legacy_abstracts(legacy, common)
    return LegacyAbstractMigrationReport(
        source_abstracts=source_abstracts,
        target_abstracts=target_abstracts,
        abstracts_imported=abstracts_imported,
        abstracts_deduplicated=abstracts_deduplicated,
        abstracts_skipped_full_text=abstracts_skipped_full_text,
    )


def migrate_legacy_corpus(
    settings: Settings,
    *,
    profile: LocalProfile,
) -> CorpusMigrationReport:
    """Copy only corpus-owned records and assets, leaving the legacy source intact."""

    authorize_corpus_mutation(CorpusScope.COMMON, profile)
    common_paths = corpus_paths(settings, CorpusScope.COMMON)
    common = Database(common_paths.database_path)
    legacy_path = settings.paths.database_path
    if legacy_path.resolve() == common.path.resolve():
        # The active configuration may already have been switched to the
        # common corpus. Keep the former location discoverable for the
        # explicit, additive migration instead of making that switch lossy.
        legacy_path = settings.paths.data_dir / "database" / common_paths.database_path.name
    legacy = Database(legacy_path)
    if legacy.path.resolve() == common.path.resolve():
        raise CorpusMigrationError("legacy and common databases must be different")
    if not legacy.path.is_file():
        raise CorpusMigrationError(f"legacy database is unavailable: {legacy.path}")
    common.initialize()

    articles = _rows(legacy, "articles", ARTICLE_COLUMNS)
    chunks = _rows(legacy, "chunks", CHUNK_COLUMNS)
    jobs = _rows(legacy, "ingestion_jobs", INGESTION_COLUMNS)
    copied_paths = {
        str(row["id"]): _copy_pdf(
            Path(str(row["pdf_path"])),
            common_paths.pdf_dir,
            str(row["sha256"]),
        )
        for row in articles
    }

    with common.transaction() as connection:
        _insert_rows(connection, "articles", ARTICLE_COLUMNS, articles)
        for article_id, pdf_path in copied_paths.items():
            connection.execute(
                "UPDATE articles SET pdf_path = ? WHERE id = ?",
                (str(pdf_path), article_id),
            )
        _insert_rows(connection, "chunks", CHUNK_COLUMNS, chunks)
        _insert_rows(connection, "ingestion_jobs", INGESTION_COLUMNS, jobs)

    (
        source_abstracts,
        target_abstracts,
        abstracts_imported,
        abstracts_deduplicated,
        abstracts_skipped_full_text,
    ) = _migrate_legacy_abstracts(legacy, common)

    legacy_extracted_dir = settings.paths.extracted_dir
    if legacy_extracted_dir.resolve() == common_paths.extracted_dir.resolve():
        legacy_extracted_dir = settings.paths.data_dir / "extracted"
    if legacy_extracted_dir.is_dir():
        _copytree_without_overwrite(legacy_extracted_dir, common_paths.extracted_dir)
    legacy_qdrant_dir = settings.paths.qdrant_dir
    if legacy_qdrant_dir.resolve() == common_paths.qdrant_dir.resolve():
        legacy_qdrant_dir = settings.paths.data_dir / "qdrant"
    if legacy_qdrant_dir.is_dir():
        _copytree_without_overwrite(legacy_qdrant_dir, common_paths.qdrant_dir)

    target_articles = _rows(common, "articles", ARTICLE_COLUMNS)
    target_chunks = _rows(common, "chunks", CHUNK_COLUMNS)
    source_by_id = {str(row["id"]): row for row in articles}
    target_by_id = {str(row["id"]): row for row in target_articles}
    for article_id, source in source_by_id.items():
        target = target_by_id.get(article_id)
        if target is None or target["doi"] != source["doi"]:
            raise CorpusMigrationError("article identifiers or DOI were not preserved")
    return CorpusMigrationReport(
        source_articles=len(articles),
        target_articles=len(target_articles),
        source_chunks=len(chunks),
        target_chunks=len(target_chunks),
        copied_pdfs=len(copied_paths),
        doi_fingerprint=_doi_fingerprint(articles),
        source_abstracts=source_abstracts,
        target_abstracts=target_abstracts,
        abstracts_imported=abstracts_imported,
        abstracts_deduplicated=abstracts_deduplicated,
        abstracts_skipped_full_text=abstracts_skipped_full_text,
    )
