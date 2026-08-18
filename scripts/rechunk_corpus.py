"""Rechunk the immutable common corpus with the exact local embedding tokenizer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings, load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.ingestion.chunker import ScientificChunker
from app.ingestion.pdf_extractor import PageText
from app.ingestion.token_budget import LocalEmbeddingTokenBudget
from app.resource_lock import ResourceFileLock, corpus_resource_lock_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--apply", action="store_true", help="Apply staged chunks to SQLite")
    parser.add_argument("--keep-staging", action="store_true", help="Retain the staging database")
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _assert_quiescent(connection: sqlite3.Connection) -> None:
    active_harvests = connection.execute(
        "SELECT COUNT(*) FROM bibliographic_harvest_runs WHERE state = 'running'"
    ).fetchone()[0]
    active_ingestions = connection.execute(
        """
        SELECT COUNT(*) FROM ingestion_jobs
        WHERE state IN ('pending', 'extracting', 'extracted', 'chunking', 'persisting')
        """
    ).fetchone()[0]
    if active_harvests or active_ingestions:
        raise RuntimeError(
            f"corpus writer is active: harvests={active_harvests}, ingestions={active_ingestions}"
        )


def _load_pages(cache_path: Path) -> list[PageText]:
    with cache_path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    pages = [PageText(**page) for page in payload["pages"]]
    if not pages:
        raise ValueError(f"extraction cache has no pages: {cache_path.name}")
    return pages


def _create_staging(settings: Settings, destination: Path) -> dict[str, int]:
    destination.unlink(missing_ok=True)
    tokenizer = LocalEmbeddingTokenBudget.from_settings(settings)
    chunker = ScientificChunker(
        target_tokens=settings.ingestion.target_tokens,
        max_tokens=settings.ingestion.max_tokens,
        overlap_tokens=settings.ingestion.overlap_tokens,
        token_budget=tokenizer,
    )
    with (
        closing(sqlite3.connect(settings.paths.database_path)) as source,
        closing(sqlite3.connect(destination)) as staging,
    ):
        source.row_factory = sqlite3.Row
        staging.execute("PRAGMA journal_mode=DELETE")
        staging.execute(
            """
            CREATE TABLE chunks (
                article_id TEXT NOT NULL,
                section TEXT,
                subsection TEXT,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                token_count INTEGER NOT NULL,
                PRIMARY KEY(article_id, chunk_index)
            ) WITHOUT ROWID
            """
        )
        articles = source.execute("SELECT id, sha256 FROM articles ORDER BY id").fetchall()
        chunk_total = 0
        maximum = 0
        for position, article in enumerate(articles, start=1):
            cache_path = settings.paths.extracted_dir / f"{article['sha256']}.pages.json"
            if not cache_path.is_file():
                raise FileNotFoundError(f"missing extraction cache: {article['id']}")
            chunks = chunker.chunk(_load_pages(cache_path))
            if not chunks:
                raise ValueError(f"no chunks produced for article: {article['id']}")
            staging.executemany(
                """
                INSERT INTO chunks (
                    article_id, section, subsection, page_start, page_end,
                    chunk_index, text, token_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        article["id"],
                        chunk.section,
                        chunk.subsection,
                        chunk.page_start,
                        chunk.page_end,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.token_count,
                    )
                    for chunk in chunks
                ],
            )
            chunk_total += len(chunks)
            maximum = max(maximum, *(chunk.token_count for chunk in chunks))
            if position % 50 == 0:
                staging.commit()
                print(
                    f"staging articles={position}/{len(articles)} chunks={chunk_total}",
                    flush=True,
                )
        staging.commit()
        invalid = staging.execute(
            "SELECT COUNT(*) FROM chunks WHERE token_count > ?",
            (settings.embeddings.max_sequence_length,),
        ).fetchone()[0]
        if invalid:
            raise RuntimeError(f"staging contains {invalid} oversized chunks")
    return {"articles": len(articles), "chunks": chunk_total, "max_tokens": maximum}


def _backup_database(settings: Settings, run_directory: Path) -> dict[str, Any]:
    source_path = settings.paths.database_path
    free = shutil.disk_usage(run_directory).free
    if free < source_path.stat().st_size + 1024 * 1024 * 1024:
        raise RuntimeError("insufficient free disk space for verified SQLite backup")
    destination = run_directory / "science_rag-before-rechunk.sqlite3"
    with (
        closing(sqlite3.connect(source_path)) as source,
        closing(sqlite3.connect(destination)) as backup,
    ):
        source.backup(backup, pages=8192, sleep=0.05)
    with closing(sqlite3.connect(destination)) as verified:
        if verified.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite backup failed quick_check")
    return {
        "path": str(destination),
        "size_bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
    }


def _evidence_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute("SELECT * FROM evidence ORDER BY id")]


def _restore_evidence(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    statement = f"INSERT INTO evidence ({', '.join(columns)}) VALUES ({placeholders})"
    for row in rows:
        match = connection.execute(
            """
            SELECT id FROM chunks
            WHERE article_id = ?
              AND page_start <= ? AND page_end >= ?
              AND instr(text, ?) > 0
            ORDER BY abs(page_start - ?) + abs(page_end - ?), chunk_index
            LIMIT 1
            """,
            (
                row["article_id"],
                row["page_start"],
                row["page_end"],
                row["source_excerpt"],
                row["page_start"],
                row["page_end"],
            ),
        ).fetchone()
        if match is None:
            raise RuntimeError(f"cannot remap persisted evidence: {row['id']}")
        row["chunk_id"] = int(match[0])
        connection.execute(statement, [row[column] for column in columns])


def _apply_staging(settings: Settings, staging_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(settings.paths.database_path, timeout=60)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        _assert_quiescent(connection)
        evidence = _evidence_rows(connection)
        connection.execute("ATTACH DATABASE ? AS staged", (str(staging_path),))
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM evidence")
            connection.execute(
                """
                UPDATE chunks
                SET section = (
                        SELECT section FROM staged.chunks AS s
                        WHERE s.article_id = chunks.article_id
                          AND s.chunk_index = chunks.chunk_index
                    ),
                    subsection = (
                        SELECT subsection FROM staged.chunks AS s
                        WHERE s.article_id = chunks.article_id
                          AND s.chunk_index = chunks.chunk_index
                    ),
                    page_start = (
                        SELECT page_start FROM staged.chunks AS s
                        WHERE s.article_id = chunks.article_id
                          AND s.chunk_index = chunks.chunk_index
                    ),
                    page_end = (
                        SELECT page_end FROM staged.chunks AS s
                        WHERE s.article_id = chunks.article_id
                          AND s.chunk_index = chunks.chunk_index
                    ),
                    text = (
                        SELECT text FROM staged.chunks AS s
                        WHERE s.article_id = chunks.article_id
                          AND s.chunk_index = chunks.chunk_index
                    ),
                    token_count = (
                        SELECT token_count FROM staged.chunks AS s
                        WHERE s.article_id = chunks.article_id
                          AND s.chunk_index = chunks.chunk_index
                    ),
                    embedding_status = 'pending'
                WHERE EXISTS (
                    SELECT 1 FROM staged.chunks AS s
                    WHERE s.article_id = chunks.article_id
                      AND s.chunk_index = chunks.chunk_index
                )
                """
            )
            connection.execute(
                """
                DELETE FROM chunks
                WHERE NOT EXISTS (
                    SELECT 1 FROM staged.chunks AS s
                    WHERE s.article_id = chunks.article_id
                      AND s.chunk_index = chunks.chunk_index
                )
                """
            )
            connection.execute(
                """
                INSERT INTO chunks (
                    article_id, section, subsection, page_start, page_end,
                    chunk_index, text, token_count, embedding_status
                )
                SELECT s.article_id, s.section, s.subsection, s.page_start, s.page_end,
                       s.chunk_index, s.text, s.token_count, 'pending'
                FROM staged.chunks AS s
                WHERE NOT EXISTS (
                    SELECT 1 FROM chunks AS c
                    WHERE c.article_id = s.article_id AND c.chunk_index = s.chunk_index
                )
                """
            )
            connection.execute(
                """
                UPDATE articles
                SET validation_status = CASE
                        WHEN validation_status = 'indexed' THEN 'validated'
                        ELSE validation_status
                    END,
                    indexed_at = NULL
                WHERE EXISTS (SELECT 1 FROM chunks WHERE chunks.article_id = articles.id)
                """
            )
            _restore_evidence(connection, evidence)
            connection.execute(
                """
                UPDATE document_element_relations AS relation
                SET related_chunk_id = (
                    SELECT chunk.id
                    FROM document_elements AS element
                    JOIN chunks AS chunk ON chunk.article_id = element.article_id
                    WHERE element.id = relation.element_id
                      AND chunk.page_start <= relation.page_number
                      AND chunk.page_end >= relation.page_number
                      AND instr(chunk.text, relation.source_excerpt) > 0
                    ORDER BY chunk.chunk_index
                    LIMIT 1
                )
                """
            )
            chunk_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            staged_count = connection.execute("SELECT COUNT(*) FROM staged.chunks").fetchone()[0]
            fts_count = connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
            oversized = connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE token_count > ?",
                (settings.embeddings.max_sequence_length,),
            ).fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if chunk_count != staged_count or fts_count != chunk_count or oversized or foreign_keys:
                raise RuntimeError(
                    "rechunk verification failed: "
                    f"chunks={chunk_count}, staged={staged_count}, fts={fts_count}, "
                    f"oversized={oversized}, foreign_keys={len(foreign_keys)}"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("DETACH DATABASE staged")
    return {"chunks": int(chunk_count), "fts": int(fts_count), "oversized": int(oversized)}


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    settings = settings_for_corpus(load_settings(arguments.config), CorpusScope.COMMON)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_directory = settings.paths.data_dir / "backups" / "rechunk-v2" / stamp
    run_directory.mkdir(parents=True, exist_ok=False)
    staging_path = run_directory / "chunks-v2-staging.sqlite3"
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "database": str(settings.paths.database_path),
        "chunker_contract_version": 2,
        "settings": {
            "target_tokens": settings.ingestion.target_tokens,
            "max_tokens": settings.ingestion.max_tokens,
            "overlap_tokens": settings.ingestion.overlap_tokens,
            "embedding_max_sequence_length": settings.embeddings.max_sequence_length,
        },
    }
    lock = ResourceFileLock(corpus_resource_lock_path(settings.paths.qdrant_dir))
    try:
        lock.acquire()
        with closing(sqlite3.connect(settings.paths.database_path)) as connection:
            _assert_quiescent(connection)
        report["staging"] = _create_staging(settings, staging_path)
        if arguments.apply:
            report["backup"] = _backup_database(settings, run_directory)
            report["applied"] = _apply_staging(settings, staging_path)
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["status"] = "applied" if arguments.apply else "staged"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["error_message"] = str(exc)[:1000]
        raise
    finally:
        lock.release()
        report_path = run_directory / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if arguments.apply and not arguments.keep_staging and report.get("status") == "applied":
            staging_path.unlink(missing_ok=True)
        print(f"report={report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
