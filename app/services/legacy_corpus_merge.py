"""One-time, lossless merge of the former split corpus into the common corpus."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.database.sqlite import Database


class LegacyCorpusMergeError(RuntimeError):
    """The former split corpus cannot be merged without losing traceability."""


@dataclass(frozen=True, slots=True)
class LegacyCorpusMergeReport:
    source_database: str
    target_database: str
    backup_database: str
    source_articles: int
    imported_articles: int
    deduplicated_articles: int
    imported_chunks: int
    imported_elements: int
    imported_relations: int
    imported_table_cells: int
    imported_ocr_traces: int
    copied_managed_pdfs: int
    pending_vector_chunks: int

    def model_dump(self) -> dict[str, object]:
        return asdict(self)


def _snapshot_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(source)) as origin,
        closing(sqlite3.connect(destination)) as target,
    ):
        origin.backup(target)


def _normalized_doi(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold()


def _managed_pdf_target(
    source_path: Path,
    *,
    legacy_root: Path,
    destination_root: Path,
    sha256: str,
) -> tuple[Path, bool]:
    if source_path.is_file():
        # Source bibliographies often use descriptive names longer than the
        # Windows path limit.  The verified SHA is globally stable and keeps
        # the consolidated path short enough for CopyFile2.
        suffix = source_path.suffix.casefold() or ".pdf"
        destination = destination_root / "merged" / f"{sha256}{suffix}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file():
            shutil.copy2(source_path, destination)
        return destination, True
    try:
        source_path.resolve().relative_to(legacy_root.resolve())
    except ValueError:
        return source_path, False
    raise LegacyCorpusMergeError(f"legacy managed PDF is unavailable: {source_path}")


def _merge_legacy_source(
    settings: Settings,
    *,
    source_path: Path,
    legacy_root: Path,
    create_backup: bool,
) -> LegacyCorpusMergeReport:
    """Merge one historical corpus database without modifying its source files."""

    target_path = settings.paths.common_database_path
    if not source_path.is_file():
        raise LegacyCorpusMergeError(f"legacy corpus database is unavailable: {source_path}")
    if source_path.resolve() == target_path.resolve():
        raise LegacyCorpusMergeError("legacy and common corpus databases must be different")
    target = Database(target_path)
    target.initialize()

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = (
        settings.paths.data_dir
        / "backups"
        / f"pre-single-corpus-merge-{timestamp}"
        / "common.sqlite3"
    )
    if create_backup:
        _snapshot_database(target_path, backup_path)

    source_uri = f"file:{source_path.resolve().as_posix()}?mode=ro"
    copied_pdfs = 0
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        source.row_factory = sqlite3.Row
        source_articles = list(source.execute("SELECT * FROM articles ORDER BY rowid"))

    with closing(target.connect()) as connection:
        existing = connection.execute("SELECT id, sha256, doi FROM articles").fetchall()
    by_sha = {str(row["sha256"]): str(row["id"]) for row in existing}
    by_doi = {
        doi: str(row["id"]) for row in existing if (doi := _normalized_doi(row["doi"])) is not None
    }
    existing_ids = {str(row["id"]) for row in existing}
    article_rows: list[tuple[object, ...]] = []
    article_map: dict[str, str] = {}
    deduplicated = 0
    for row in source_articles:
        source_id = str(row["id"])
        doi = _normalized_doi(row["doi"])
        target_id = by_sha.get(str(row["sha256"])) or (by_doi.get(doi) if doi else None)
        if target_id is not None:
            deduplicated += 1
            continue
        if source_id in existing_ids:
            raise LegacyCorpusMergeError(f"article identifier collision: {source_id}")
        managed_path, copied = _managed_pdf_target(
            Path(str(row["pdf_path"])),
            legacy_root=legacy_root,
            destination_root=settings.paths.common_pdf_dir,
            sha256=str(row["sha256"]),
        )
        copied_pdfs += int(copied)
        article_map[source_id] = source_id
        article_rows.append(
            (
                source_id,
                row["sha256"],
                row["doi"],
                row["title"],
                row["abstract"],
                row["authors"],
                row["journal"],
                row["publication_year"],
                row["language"],
                str(managed_path),
                row["validation_status"],
                row["source"],
                row["created_at"],
                None,
            )
        )

    imported_chunks = imported_elements = imported_relations = imported_cells = 0
    imported_ocr = 0
    with target.transaction() as connection:
        connection.execute("ATTACH DATABASE ? AS legacy", (str(source_path.resolve()),))
        try:
            connection.execute(
                "CREATE TEMP TABLE article_map(source_id TEXT PRIMARY KEY, target_id TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO article_map(source_id, target_id) VALUES (?, ?)",
                article_map.items(),
            )
            connection.executemany(
                """
                INSERT INTO articles (
                    id, sha256, doi, title, abstract, authors, journal, publication_year,
                    language, pdf_path, validation_status, source, created_at, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                article_rows,
            )

            maximum_chunk_id = int(
                connection.execute("SELECT COALESCE(MAX(id), 0) FROM chunks").fetchone()[0]
            )
            connection.execute(
                """
                CREATE TEMP TABLE chunk_map(
                    source_id INTEGER PRIMARY KEY,
                    target_id INTEGER NOT NULL,
                    target_article_id TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO chunk_map(source_id, target_id, target_article_id)
                SELECT c.id, ? + c.id, am.target_id
                FROM legacy.chunks AS c
                JOIN article_map AS am ON am.source_id = c.article_id
                """,
                (maximum_chunk_id,),
            )
            connection.execute(
                """
                INSERT INTO chunks (
                    id, article_id, section, subsection, page_start, page_end,
                    chunk_index, text, token_count, embedding_status
                )
                SELECT cm.target_id, cm.target_article_id, c.section, c.subsection,
                       c.page_start, c.page_end, c.chunk_index, c.text, c.token_count, 'pending'
                FROM legacy.chunks AS c
                JOIN chunk_map AS cm ON cm.source_id = c.id
                """
            )
            imported_chunks = int(
                connection.execute("SELECT COUNT(*) FROM chunk_map").fetchone()[0]
            )

            connection.execute(
                """
                CREATE TEMP TABLE element_map(
                    source_id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    target_article_id TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO element_map(source_id, target_id, target_article_id)
                SELECT e.id, 'merged:' || e.id, am.target_id
                FROM legacy.document_elements AS e
                JOIN article_map AS am ON am.source_id = e.article_id
                """
            )
            connection.execute(
                """
                INSERT INTO document_elements (
                    id, article_id, local_element_id, kind, page_number, bbox_json,
                    source_kind, source_locator, original_caption, synthetic_caption
                )
                SELECT em.target_id, em.target_article_id, e.local_element_id, e.kind,
                       e.page_number, e.bbox_json, e.source_kind, e.source_locator,
                       e.original_caption, e.synthetic_caption
                FROM legacy.document_elements AS e
                JOIN element_map AS em ON em.source_id = e.id
                """
            )
            imported_elements = int(
                connection.execute("SELECT COUNT(*) FROM element_map").fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO document_element_relations (
                    element_id, relation, page_number, related_chunk_id,
                    source_excerpt, source_excerpt_sha256
                )
                SELECT em.target_id, r.relation, r.page_number, cm.target_id,
                       r.source_excerpt, r.source_excerpt_sha256
                FROM legacy.document_element_relations AS r
                JOIN element_map AS em ON em.source_id = r.element_id
                LEFT JOIN chunk_map AS cm ON cm.source_id = r.related_chunk_id
                """
            )
            imported_relations = int(connection.execute("SELECT changes()").fetchone()[0])
            connection.execute(
                """
                INSERT INTO document_table_cells(element_id, row_index, column_index, text)
                SELECT em.target_id, c.row_index, c.column_index, c.text
                FROM legacy.document_table_cells AS c
                JOIN element_map AS em ON em.source_id = c.element_id
                """
            )
            imported_cells = int(connection.execute("SELECT changes()").fetchone()[0])
            connection.execute(
                """
                INSERT OR IGNORE INTO ocr_page_traces (
                    pdf_sha256, page_number, article_id, language, confidence,
                    confidence_method, embedded_text_original, ocr_text,
                    admitted, decision_reason
                )
                SELECT o.pdf_sha256, o.page_number, am.target_id, o.language, o.confidence,
                       o.confidence_method, o.embedded_text_original, o.ocr_text,
                       o.admitted, o.decision_reason
                FROM legacy.ocr_page_traces AS o
                JOIN article_map AS am ON am.source_id = o.article_id
                """
            )
            imported_ocr = int(connection.execute("SELECT changes()").fetchone()[0])
            connection.execute(
                """
                INSERT OR IGNORE INTO ingestion_jobs (
                    pdf_path, sha256, state, article_id, error_type, error_message,
                    attempt_count, created_at, updated_at
                )
                SELECT j.pdf_path, j.sha256, j.state,
                       am.target_id, j.error_type, j.error_message, j.attempt_count,
                       j.created_at, j.updated_at
                FROM legacy.ingestion_jobs AS j
                LEFT JOIN article_map AS am ON am.source_id = j.article_id
                WHERE j.article_id IS NULL OR am.target_id IS NOT NULL
                """
            )
        finally:
            # The transaction's connection closes immediately afterwards, which
            # safely detaches the legacy database outside the active transaction.
            pass

    legacy_extracted = legacy_root / "extracted"
    if legacy_extracted.is_dir():
        shutil.copytree(
            legacy_extracted,
            settings.paths.common_extracted_dir,
            dirs_exist_ok=True,
        )

    with closing(target.connect()) as connection:
        target_articles = int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        target_chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    if target_articles < len(existing) + len(article_map) or target_chunks < imported_chunks:
        raise LegacyCorpusMergeError("post-merge corpus counts are incomplete")
    return LegacyCorpusMergeReport(
        source_database=str(source_path),
        target_database=str(target_path),
        backup_database=str(backup_path),
        source_articles=len(source_articles),
        imported_articles=len(article_map),
        deduplicated_articles=deduplicated,
        imported_chunks=imported_chunks,
        imported_elements=imported_elements,
        imported_relations=imported_relations,
        imported_table_cells=imported_cells,
        imported_ocr_traces=imported_ocr,
        copied_managed_pdfs=copied_pdfs,
        pending_vector_chunks=imported_chunks,
    )


def merge_legacy_split_corpus(settings: Settings) -> LegacyCorpusMergeReport:
    """Consolidate every historical scientific corpus into ``data/common``.

    The legacy application database and the former split directory both remain
    untouched.  A SQLite snapshot of the common corpus is created before the
    first import so the operation has a recoverable rollback point.
    """

    common_path = settings.paths.common_database_path.resolve()
    sources = (
        (settings.paths.database_path, settings.paths.data_dir),
        (
            settings.paths.data_dir / "private" / "database" / "science_rag.sqlite3",
            settings.paths.data_dir / "private",
        ),
    )
    reports: list[LegacyCorpusMergeReport] = []
    for source_path, source_root in sources:
        if not source_path.is_file() or source_path.resolve() == common_path:
            continue
        reports.append(
            _merge_legacy_source(
                settings,
                source_path=source_path,
                legacy_root=source_root,
                create_backup=not reports,
            )
        )
    if not reports:
        raise LegacyCorpusMergeError("no historical corpus database is available to merge")

    return LegacyCorpusMergeReport(
        source_database=";".join(report.source_database for report in reports),
        target_database=str(common_path),
        backup_database=reports[0].backup_database,
        source_articles=sum(report.source_articles for report in reports),
        imported_articles=sum(report.imported_articles for report in reports),
        deduplicated_articles=sum(report.deduplicated_articles for report in reports),
        imported_chunks=sum(report.imported_chunks for report in reports),
        imported_elements=sum(report.imported_elements for report in reports),
        imported_relations=sum(report.imported_relations for report in reports),
        imported_table_cells=sum(report.imported_table_cells for report in reports),
        imported_ocr_traces=sum(report.imported_ocr_traces for report in reports),
        copied_managed_pdfs=sum(report.copied_managed_pdfs for report in reports),
        pending_vector_chunks=sum(report.pending_vector_chunks for report in reports),
    )
