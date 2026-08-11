from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.database.sqlite import Database
from app.ingestion.embeddings import EmbeddedChunkBatch, EmbeddingBatchProcessor
from app.retrieval.vector_search import (
    QdrantLocalIndex,
    VectorIndexConfigurationError,
    VectorSearchService,
    clear_query_vector_cache,
)


class FakeBackend:
    def __init__(self, dimension: int = 2) -> None:
        self.model_name = "fake/multilingual"
        self.dimension = dimension
        self.closed = False
        self.query_calls = 0

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            [1.0, float(index % 2)] + [0.0] * (self.dimension - 2) for index, _ in enumerate(texts)
        ]

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        self.query_calls += 1
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]

    def close(self) -> None:
        self.closed = True


def _batch(model_name: str = "fake/multilingual") -> EmbeddedChunkBatch:
    return EmbeddedChunkBatch(
        chunk_ids=(1, 2),
        article_ids=("article-a", "article-b"),
        sections=("Results", "Discussion"),
        page_starts=(2, 5),
        page_ends=(2, 5),
        vectors=((1.0, 0.0), (0.0, 1.0)),
        model_name=model_name,
        vector_dimension=2,
    )


def _seed_database(database: Database, count: int = 3) -> list[int]:
    database.save_article_and_chunks(
        {
            "id": "article-a",
            "sha256": "v" * 64,
            "doi": None,
            "title": "Synthetic vector article",
            "authors": [],
            "pdf_path": "data/pdf/vector.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": index + 1,
                "page_end": index + 1,
                "chunk_index": index,
                "text": f"Authoritative SQLite passage {index}",
                "token_count": 4,
            }
            for index in range(count)
        ],
    )
    return [int(row["id"]) for row in database.chunks_for_embedding(limit=count)]


def test_qdrant_local_index_persists_searches_and_filters(settings) -> None:
    index = QdrantLocalIndex(
        settings, model_name="fake/multilingual", collection_name="test_vectors"
    )
    try:
        index.upsert(_batch())
        assert index.count() == 2
        results = index.search((1.0, 0.0), limit=2)
        assert [result.chunk_id for result in results] == [1, 2]
        filtered = index.search([1.0, 0.0], article_ids=["article-b"])
        assert [result.article_id for result in filtered] == ["article-b"]
        filtered_section = index.search([1.0, 0.0], sections=["Discussion"])
        assert [result.chunk_id for result in filtered_section] == [2]
        assert index.search([1.0, 0.0], sections=[]) == []
        with pytest.raises(ValueError, match="positive"):
            index.search([1.0, 0.0], limit=-1)
        with pytest.raises(ValueError, match="positive"):
            index.search([1.0, 0.0], limit=0)
        points = index.client.retrieve(
            collection_name=index.collection_name,
            ids=[1],
            with_payload=True,
            with_vectors=False,
        )
        assert "text" not in (points[0].payload or {})
    finally:
        index.close()

    reopened = QdrantLocalIndex(
        settings, model_name="fake/multilingual", collection_name="test_vectors"
    )
    try:
        assert reopened.count() == 2
        assert reopened.search([1.0, 0.0], limit=1)[0].chunk_id == 1
    finally:
        reopened.close()


def test_existing_collection_rejects_wrong_model_or_dimension(settings) -> None:
    index = QdrantLocalIndex(
        settings, model_name="fake/multilingual", collection_name="compatibility"
    )
    try:
        index.upsert(_batch())
        with pytest.raises(VectorIndexConfigurationError, match="dimension"):
            index.ensure_collection(3)
    finally:
        index.close()

    wrong_model = QdrantLocalIndex(
        settings, model_name="other/model", collection_name="compatibility"
    )
    try:
        with pytest.raises(VectorIndexConfigurationError, match="collection model"):
            wrong_model.ensure_collection(2)
    finally:
        wrong_model.close()


def test_qdrant_deletes_only_explicit_chunk_points(settings) -> None:
    index = QdrantLocalIndex(
        settings, model_name="fake/multilingual", collection_name="point_deletion"
    )
    try:
        index.upsert(_batch())
        assert index.delete_points([1, 1]) == 1
        assert index.count() == 1
        assert [result.chunk_id for result in index.search([0.0, 1.0])] == [2]
        with pytest.raises(ValueError, match="positive"):
            index.delete_points([-1])
    finally:
        index.close()


def test_embedding_processor_indexes_qdrant_in_bounded_batches(settings) -> None:
    settings.embeddings.batch_size = 2
    database = Database(settings.paths.database_path)
    database.initialize()
    _seed_database(database, count=5)
    backend = FakeBackend()
    index = QdrantLocalIndex(settings, model_name=backend.model_name, collection_name="processor")
    try:
        report = EmbeddingBatchProcessor(settings, database, backend).run(index)
        assert report.batches_completed == 3
        assert report.chunks_indexed == 5
        assert index.count() == 5
        assert database.embedding_status_counts() == {"indexed": 5}
        article = database.article_by_sha256("v" * 64)
        assert article is not None
        assert article["validation_status"] == "indexed"
        assert article["indexed_at"] is not None

        assert database.reset_all_embedding_statuses() == 5
        assert database.embedding_status_counts() == {"pending": 5}
        article = database.article_by_sha256("v" * 64)
        assert article is not None
        assert article["validation_status"] == "validated"
        assert article["indexed_at"] is None
    finally:
        index.close()


def test_vector_search_hydrates_text_only_from_sqlite(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    chunk_ids = _seed_database(database, count=2)
    backend = FakeBackend()
    index = QdrantLocalIndex(settings, model_name=backend.model_name, collection_name="hydration")
    try:
        index.upsert(
            EmbeddedChunkBatch(
                chunk_ids=tuple(chunk_ids),
                article_ids=("article-a", "article-a"),
                sections=("Ignored", "Ignored"),
                page_starts=(99, 99),
                page_ends=(99, 99),
                vectors=((1.0, 0.0), (0.0, 1.0)),
                model_name=backend.model_name,
                vector_dimension=2,
            )
        )
        results = VectorSearchService(database, backend, index).search("local question", limit=2)
        assert results[0].text == "Authoritative SQLite passage 0"
        assert results[0].page_start == 1
        assert results[0].section == "Results"
    finally:
        index.close()


def test_vector_search_rejects_a_backend_with_a_different_model(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    backend = FakeBackend()
    index = QdrantLocalIndex(settings, model_name="other/model", collection_name="model-mismatch")
    try:
        with pytest.raises(VectorIndexConfigurationError, match="embedding backend model"):
            VectorSearchService(database, backend, index).search("local question")
    finally:
        index.close()


def test_vector_search_reuses_query_vector_and_respects_backend_ownership(settings) -> None:
    clear_query_vector_cache()
    database = Database(settings.paths.database_path)
    database.initialize()
    chunk_ids = _seed_database(database, count=2)
    backend = FakeBackend()
    index = QdrantLocalIndex(settings, model_name=backend.model_name, collection_name="query_cache")
    index.upsert(
        EmbeddedChunkBatch(
            chunk_ids=tuple(chunk_ids),
            article_ids=("article-a", "article-a"),
            sections=("Results", "Discussion"),
            page_starts=(1, 2),
            page_ends=(1, 2),
            vectors=((1.0, 0.0), (0.0, 1.0)),
            model_name=backend.model_name,
            vector_dimension=2,
        )
    )
    service = VectorSearchService(database, backend, index, close_backend=False)

    assert service.search("same scientific query", limit=1)
    assert service.search("same scientific query", limit=1)
    assert backend.query_calls == 1
    assert service.query_cache_misses == 1
    assert service.query_cache_hits == 1

    service.close()
    assert backend.closed is False
