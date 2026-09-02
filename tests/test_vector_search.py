from __future__ import annotations

import warnings
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


def test_qdrant_indexes_share_one_lazy_client_owner(settings, monkeypatch) -> None:
    created_clients = []

    class FakeQdrantClient:
        def __init__(self, **options) -> None:
            self.options = options
            self.close_count = 0
            created_clients.append(self)

        def close(self) -> None:
            self.close_count += 1

    monkeypatch.setattr(
        "app.retrieval.vector_search.QdrantClient",
        FakeQdrantClient,
    )
    owner = QdrantLocalIndex(settings, model_name="fake/multilingual")
    abstracts = QdrantLocalIndex(
        settings,
        model_name="fake/multilingual",
        collection_name="bibliographic_abstracts",
        client_owner=owner,
    )
    chunks = QdrantLocalIndex(
        settings,
        model_name="fake/multilingual",
        collection_name="science_chunks",
        client_owner=owner,
    )

    assert created_clients == []
    assert abstracts.client is chunks.client is owner.client
    assert len(created_clients) == 1

    abstracts.close()
    chunks.close()
    assert created_clients[0].close_count == 0
    owner.close()
    owner.close()
    assert created_clients[0].close_count == 1


def test_qdrant_local_large_collection_recommendation_is_suppressed(
    settings,
    monkeypatch,
) -> None:
    class WarningQdrantClient:
        def __init__(self, **_options) -> None:
            warnings.warn(
                "Local mode is not recommended for collections with more than 20,000 points. "
                "Collection <science_chunks> contains 321,127 points.",
                UserWarning,
                stacklevel=2,
            )
            warnings.warn(
                "Another Qdrant warning that remains actionable.", UserWarning, stacklevel=2
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "app.retrieval.vector_search.QdrantClient",
        WarningQdrantClient,
    )
    index = QdrantLocalIndex(settings, model_name="fake/multilingual")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _ = index.client

    assert [str(item.message) for item in caught] == [
        "Another Qdrant warning that remains actionable."
    ]
    index.close()


def test_qdrant_client_owner_rejects_another_local_path(settings) -> None:
    owner = QdrantLocalIndex(settings, model_name="fake/multilingual")

    with pytest.raises(ValueError, match="same local path"):
        QdrantLocalIndex(
            settings,
            model_name="fake/multilingual",
            path=settings.paths.qdrant_dir / "other-corpus",
            client_owner=owner,
        )


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


def test_vector_search_many_matches_sequential_searches_and_batches_uncached_queries(
    settings,
) -> None:
    clear_query_vector_cache()
    database = Database(settings.paths.database_path)
    database.initialize()
    chunk_ids = _seed_database(database, count=2)
    backend = FakeBackend()
    index = QdrantLocalIndex(
        settings, model_name=backend.model_name, collection_name="batch_search"
    )
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
    queries = [
        "first scientific question",
        "second scientific question",
        "first scientific question",
    ]
    try:
        batch = service.search_many(queries, limit=2)
        assert backend.query_calls == 1
        assert service.query_cache_misses == 2
        assert service.query_cache_hits == 1
        assert [[result.model_dump() for result in group] for group in batch] == [
            [result.model_dump() for result in service.search(query, limit=2)] for query in queries
        ]
    finally:
        service.close()
