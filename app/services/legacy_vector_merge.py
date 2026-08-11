"""One-time remapping of historical local Qdrant points into the common corpus."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from qdrant_client import models

from app.config import Settings
from app.database.sqlite import Database
from app.retrieval.index_manifest import (
    prepare_index_generation_mutation,
    write_ready_index_generation_manifest,
)
from app.retrieval.vector_search import QdrantLocalIndex, VectorIndexConfigurationError


class LegacyVectorMergeError(RuntimeError):
    """Historical vectors cannot be safely associated with the common chunks."""


@dataclass(frozen=True, slots=True)
class LegacyVectorMergeReport:
    source_indexes: int
    transferred_points: int
    skipped_points: int

    def model_dump(self) -> dict[str, int]:
        return asdict(self)


def _source_chunk_offset(target_path: Path, source_path: Path) -> int | None:
    """Find the positive chunk-ID offset created by the SQLite corpus merge."""

    with closing(sqlite3.connect(target_path)) as connection:
        connection.execute("ATTACH DATABASE ? AS legacy", (str(source_path.resolve()),))
        try:
            row = connection.execute(
                """
                SELECT target_chunk.id - source_chunk.id AS offset, COUNT(*) AS mapped_count
                FROM legacy.chunks AS source_chunk
                JOIN chunks AS target_chunk
                  ON target_chunk.article_id = source_chunk.article_id
                 AND target_chunk.section IS source_chunk.section
                 AND target_chunk.subsection IS source_chunk.subsection
                 AND target_chunk.page_start = source_chunk.page_start
                 AND target_chunk.page_end = source_chunk.page_end
                 AND target_chunk.chunk_index = source_chunk.chunk_index
                 AND target_chunk.text = source_chunk.text
                WHERE target_chunk.id > source_chunk.id
                GROUP BY offset
                ORDER BY mapped_count DESC, offset DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.execute("DETACH DATABASE legacy")
    return int(row[0]) if row is not None else None


def _vector_dimension(index: QdrantLocalIndex, settings: Settings) -> int | None:
    if not index.collection_exists():
        return None
    info = index.client.get_collection(index.collection_name)
    vectors = info.config.params.vectors
    if not isinstance(vectors, models.VectorParams):
        raise LegacyVectorMergeError("named vectors are not supported")
    metadata = info.config.metadata or {}
    if metadata.get("model_name") != settings.embeddings.model_name:
        raise VectorIndexConfigurationError("historical index uses another embedding model")
    return int(vectors.size)


def _copy_source_points(
    *,
    settings: Settings,
    target_database: Database,
    target_index: QdrantLocalIndex,
    source_database_path: Path,
    source_index_path: Path,
) -> tuple[int, int, bool]:
    if not source_database_path.is_file() or not source_index_path.is_dir():
        return 0, 0, False
    chunk_offset = _source_chunk_offset(target_database.path, source_database_path)
    if chunk_offset is None:
        return 0, 0, False

    source_index = QdrantLocalIndex(settings, path=source_index_path)
    try:
        dimension = _vector_dimension(source_index, settings)
        if dimension is None:
            return 0, 0, False
        target_index.ensure_collection(dimension)
        transferred = skipped = 0
        scroll_offset: Any = None
        while True:
            records, scroll_offset = source_index.client.scroll(
                collection_name=source_index.collection_name,
                offset=scroll_offset,
                limit=512,
                with_payload=True,
                with_vectors=True,
            )
            if not records:
                break
            candidates: list[tuple[int, int, Any]] = []
            for record in records:
                payload = record.payload or {}
                source_id = payload.get("chunk_id")
                article_id = payload.get("article_id")
                if (
                    not isinstance(source_id, int)
                    or not isinstance(article_id, str)
                    or int(record.id) != source_id
                    or record.vector is None
                ):
                    skipped += 1
                    continue
                candidates.append((source_id, source_id + chunk_offset, record))
            target_chunks = target_database.chunks_by_ids(
                [target_id for _source_id, target_id, _record in candidates]
            )
            points: list[models.PointStruct] = []
            indexed_ids: list[int] = []
            for _source_id, target_id, record in candidates:
                payload = record.payload or {}
                target_chunk = target_chunks.get(target_id)
                if target_chunk is None or str(target_chunk["article_id"]) != payload["article_id"]:
                    skipped += 1
                    continue
                remapped_payload = dict(payload)
                remapped_payload["chunk_id"] = target_id
                points.append(
                    models.PointStruct(
                        id=target_id,
                        vector=record.vector,
                        payload=remapped_payload,
                    )
                )
                indexed_ids.append(target_id)
            if points:
                target_index.client.upsert(
                    collection_name=target_index.collection_name,
                    points=points,
                    wait=True,
                )
                target_database.update_embedding_status(indexed_ids, "indexed")
                transferred += len(indexed_ids)
            if scroll_offset is None:
                break
        return transferred, skipped, True
    finally:
        source_index.close()


def transfer_legacy_vectors(settings: Settings) -> LegacyVectorMergeReport:
    """Copy only already-indexed chunks from historical Qdrant collections.

    Existing common points are retained. The operation is idempotent because
    the transferred point identifiers are deterministic and Qdrant upserts
    replace only those same remapped points.
    """

    target_database = Database(settings.paths.common_database_path)
    target_database.initialize()
    target_index = QdrantLocalIndex(settings, path=settings.paths.common_qdrant_dir)
    sources = (
        (settings.paths.database_path, settings.paths.qdrant_dir),
        (
            settings.paths.data_dir / "private" / "database" / "science_rag.sqlite3",
            settings.paths.data_dir / "private" / "qdrant",
        ),
    )
    source_indexes = transferred = skipped = 0
    index_manifest = None
    try:
        index_manifest = prepare_index_generation_mutation(target_index)
        for source_database_path, source_index_path in sources:
            if source_database_path.resolve() == target_database.path.resolve():
                continue
            current_transferred, current_skipped, source_used = _copy_source_points(
                settings=settings,
                target_database=target_database,
                target_index=target_index,
                source_database_path=source_database_path,
                source_index_path=source_index_path,
            )
            source_indexes += int(source_used)
            transferred += current_transferred
            skipped += current_skipped
        if transferred:
            target_database.refresh_fully_indexed_articles()
        if index_manifest is not None:
            write_ready_index_generation_manifest(
                target_database,
                target_index,
                generation_id=index_manifest.generation_id,
                created_at=index_manifest.created_at,
            )
    finally:
        target_index.close()
    return LegacyVectorMergeReport(
        source_indexes=source_indexes,
        transferred_points=transferred,
        skipped_points=skipped,
    )
