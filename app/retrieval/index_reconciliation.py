"""Safe, text-free reconciliation between SQLite chunks and a Qdrant index."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from typing import Any

from qdrant_client import models

from app.database.sqlite import Database
from app.retrieval.vector_search import QdrantLocalIndex


@dataclass(frozen=True)
class IndexReconciliationReport:
    """Counts from one idempotent reconciliation pass (never includes chunk text)."""

    sqlite_chunks: int
    qdrant_points_seen: int
    stale_points_deleted: int
    invalid_points_deleted: int
    valid_points_preserved: int
    missing_chunks_queued: int
    invalid_chunks_queued: int
    chunks_marked_indexed: int

    def model_dump(self) -> dict[str, int]:
        return asdict(self)


def reconcile_chunk_index(database: Database, index: QdrantLocalIndex) -> IndexReconciliationReport:
    """Make SQLite's chunk routing metadata authoritative without reading vectors or text.

    The caller must already have marked the generation ``building``.  Each mutation is
    deliberately safe to repeat: interruption may leave a building manifest, and the
    next pass simply re-scans both stores before queuing only missing/invalid chunks.
    """

    expected = _sqlite_chunk_payloads(database)
    actual = _qdrant_chunk_points(index)
    stale_ids: list[int | str] = []
    invalid_ids: list[int | str] = []
    valid_chunk_ids: set[int] = set()
    invalid_chunk_ids: set[int] = set()

    for point_id, payload in actual:
        if isinstance(point_id, bool) or not isinstance(point_id, int) or point_id <= 0:
            invalid_ids.append(point_id)
            continue
        desired = expected.get(point_id)
        if desired is None:
            stale_ids.append(point_id)
            continue
        if not _payload_matches(payload, point_id, desired, index.model_name):
            invalid_ids.append(point_id)
            invalid_chunk_ids.add(point_id)
            continue
        valid_chunk_ids.add(point_id)

    present_chunk_ids = {
        point_id
        for point_id, _payload in actual
        if isinstance(point_id, int) and not isinstance(point_id, bool) and point_id > 0
    }
    missing_chunk_ids = set(expected) - present_chunk_ids
    # Invalid points with a SQLite ID must be regenerated even though a point was present.
    queued_ids = missing_chunk_ids | invalid_chunk_ids

    _delete_raw_point_ids(index, stale_ids + invalid_ids)
    _update_embedding_status_in_batches(database, sorted(valid_chunk_ids), "indexed")
    _update_embedding_status_in_batches(database, sorted(queued_ids), "pending")
    if valid_chunk_ids or queued_ids:
        database.refresh_fully_indexed_articles()

    return IndexReconciliationReport(
        sqlite_chunks=len(expected),
        qdrant_points_seen=len(actual),
        stale_points_deleted=len(stale_ids),
        invalid_points_deleted=len(invalid_ids),
        valid_points_preserved=len(valid_chunk_ids),
        missing_chunks_queued=len(missing_chunk_ids),
        invalid_chunks_queued=len(invalid_chunk_ids),
        chunks_marked_indexed=len(valid_chunk_ids),
    )


def _sqlite_chunk_payloads(database: Database) -> dict[int, tuple[str, str | None, int, int]]:
    with closing(database.connect()) as connection:
        return {
            int(row[0]): (str(row[1]), row[2], int(row[3]), int(row[4]))
            for row in connection.execute(
                "SELECT id, article_id, section, page_start, page_end FROM chunks"
            )
        }


def _qdrant_chunk_points(index: QdrantLocalIndex) -> list[tuple[int | str, dict[str, Any]]]:
    """Scroll IDs and small payloads only; never retrieve vectors or scientific text."""

    if not index.collection_exists():
        return []
    points: list[tuple[int | str, dict[str, Any]]] = []
    offset: int | str | None = None
    while True:
        page, offset = index.client.scroll(
            collection_name=index.collection_name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in page:
            # Retain malformed payload/ID long enough to remove it safely.
            points.append(
                (point.id, dict(point.payload) if isinstance(point.payload, dict) else {})
            )
        if offset is None:
            return points


def _payload_matches(
    payload: dict[str, Any],
    chunk_id: int,
    expected: tuple[str, str | None, int, int],
    model_name: str,
) -> bool:
    article_id, section, page_start, page_end = expected
    return (
        payload.get("kind") == "chunk"
        and payload.get("chunk_id") == chunk_id
        and payload.get("article_id") == article_id
        and payload.get("section") == section
        and payload.get("page_start") == page_start
        and payload.get("page_end") == page_end
        and payload.get("model_name") == model_name
    )


def _delete_raw_point_ids(index: QdrantLocalIndex, point_ids: Sequence[int | str]) -> None:
    unique_ids = list(dict.fromkeys(point_ids))
    if not unique_ids:
        return
    index.client.delete(
        collection_name=index.collection_name,
        points_selector=models.PointIdsList(points=unique_ids),
        wait=True,
    )


def _update_embedding_status_in_batches(
    database: Database,
    chunk_ids: Sequence[int],
    status: str,
) -> None:
    """Stay below SQLite variable limits for production-sized corpora."""

    batch_size = 900
    for start in range(0, len(chunk_ids), batch_size):
        database.update_embedding_status(chunk_ids[start : start + batch_size], status)
