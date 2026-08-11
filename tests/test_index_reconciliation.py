from __future__ import annotations

import pytest
from qdrant_client import models

from app.database.sqlite import Database
from app.desktop.model_integrity import write_model_manifest
from app.ingestion.embeddings import EmbeddedChunkBatch, local_model_path
from app.retrieval.index_manifest import (
    IndexGenerationIntegrityError,
    assert_index_generation_ready,
    begin_index_generation,
    verify_index_generation_snapshot,
    write_ready_index_generation_manifest,
)
from app.retrieval.index_reconciliation import (
    _update_embedding_status_in_batches,
    reconcile_chunk_index,
)
from app.retrieval.vector_search import QdrantLocalIndex


def _index(settings) -> QdrantLocalIndex:
    settings.qdrant.collection_name = "index-reconciliation-tests"
    settings.embeddings.model_name = "fake/index-reconciliation"
    model_path = local_model_path(settings)
    model_path.mkdir(parents=True, exist_ok=True)
    (model_path / "weights.bin").write_bytes(b"fake reconciliation model")
    write_model_manifest(model_path, settings.embeddings.model_name)
    return QdrantLocalIndex(settings)


def _seed(database: Database, index: QdrantLocalIndex) -> tuple[int, int, int]:
    database.save_article_and_chunks(
        {
            "id": "reconciliation-article",
            "sha256": "r" * 64,
            "doi": None,
            "title": "Reconciliation article",
            "authors": [],
            "pdf_path": "data/pdf/reconciliation.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": number,
                "text": f"Chunk {number}",
                "token_count": 2,
            }
            for number in range(3)
        ],
    )
    ids = tuple(int(row["id"]) for row in database.chunks_for_embedding(limit=10))
    index.upsert(
        EmbeddedChunkBatch(
            chunk_ids=(ids[0],),
            article_ids=("reconciliation-article",),
            sections=("Results",),
            page_starts=(1,),
            page_ends=(1,),
            vectors=((1.0, 0.0),),
            model_name=index.model_name,
            vector_dimension=2,
        )
    )
    index.client.upsert(
        collection_name=index.collection_name,
        points=[
            models.PointStruct(
                id=ids[1],
                vector=[0.0, 1.0],
                payload={"kind": "chunk", "chunk_id": ids[1], "article_id": "wrong"},
            ),
            models.PointStruct(
                id=99999,
                vector=[0.0, 1.0],
                payload={"kind": "chunk", "chunk_id": 99999},
            ),
        ],
        wait=True,
    )
    database.update_embedding_status(ids, "indexed")
    return ids


def test_reconciliation_removes_stale_and_invalid_points_and_queues_only_affected_chunks(
    settings,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        chunk_ids = _seed(database, index)
        begin_index_generation(index)

        report = reconcile_chunk_index(database, index)

        assert report.stale_points_deleted == 1
        assert report.invalid_points_deleted == 1
        assert report.valid_points_preserved == 1
        assert report.missing_chunks_queued == 1
        assert report.invalid_chunks_queued == 1
        assert database.embedding_status_counts() == {"indexed": 1, "pending": 2}
        points, _offset = index.client.scroll(
            collection_name=index.collection_name, with_payload=True, with_vectors=False, limit=10
        )
        assert [point.id for point in points] == [chunk_ids[0]]
    finally:
        index.close()


def test_reconciliation_is_resumable_and_never_marks_an_incomplete_index_ready(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    index = _index(settings)
    try:
        chunk_ids = _seed(database, index)
        generation = begin_index_generation(index)
        first = reconcile_chunk_index(database, index)
        with pytest.raises(IndexGenerationIntegrityError, match="not ready"):
            assert_index_generation_ready(index)

        resumed = reconcile_chunk_index(database, index)
        assert first.missing_chunks_queued == 1
        assert resumed.valid_points_preserved == 1
        assert resumed.invalid_chunks_queued == 0
        assert database.embedding_status_counts() == {"indexed": 1, "pending": 2}

        index.upsert(
            EmbeddedChunkBatch(
                chunk_ids=chunk_ids[1:],
                article_ids=("reconciliation-article", "reconciliation-article"),
                sections=("Results", "Results"),
                page_starts=(1, 1),
                page_ends=(1, 1),
                vectors=((0.0, 1.0), (0.5, 0.5)),
                model_name=index.model_name,
                vector_dimension=2,
            )
        )
        database.update_embedding_status(chunk_ids[1:], "indexed")
        ready = write_ready_index_generation_manifest(
            database,
            index,
            generation_id=generation.generation_id,
            created_at=generation.created_at,
        )
        assert ready.state == "ready"
        assert verify_index_generation_snapshot(database, index).indexed_chunk_count == 3
    finally:
        index.close()


def test_reconciliation_batches_large_sqlite_status_updates() -> None:
    class RecordingDatabase:
        calls: list[tuple[list[int], str]]

        def __init__(self) -> None:
            self.calls = []

        def update_embedding_status(self, chunk_ids, status: str) -> None:
            self.calls.append((list(chunk_ids), status))

    database = RecordingDatabase()
    _update_embedding_status_in_batches(database, list(range(2050)), "indexed")  # type: ignore[arg-type]

    assert [len(chunk_ids) for chunk_ids, _status in database.calls] == [900, 900, 250]
    assert {status for _chunk_ids, status in database.calls} == {"indexed"}
