from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.database.sqlite import Database
from app.ingestion.embeddings import EmbeddedChunkBatch
from app.memory import MemoryLimitError
from app.retrieval.hybrid_search import (
    HybridSearchService,
    RankedList,
    reciprocal_rank_fusion,
)
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService


class FixedQueryBackend:
    model_name = "fake/hybrid"
    dimension = 2

    def __init__(self) -> None:
        self.closed = False

    def encode_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def encode_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def close(self) -> None:
        self.closed = True


class LowMemoryGuard:
    def check(self, operation: str) -> None:
        raise MemoryLimitError(f"synthetic low-memory condition during {operation}")


def _seed_hybrid_database(database: Database) -> list[int]:
    database.save_article_and_chunks(
        {
            "id": "article-a",
            "sha256": "a" * 64,
            "doi": None,
            "title": "Fermentation temperature and aroma",
            "authors": [],
            "publication_year": 2024,
            "pdf_path": "data/pdf/a.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 2,
                "page_end": 2,
                "chunk_index": 0,
                "text": "Fermentation temperature changed the aroma concentration.",
                "token_count": 7,
            },
            {
                "section": "Discussion",
                "page_start": 3,
                "page_end": 3,
                "chunk_index": 1,
                "text": "Temperature during fermentation requires further study.",
                "token_count": 7,
            },
        ],
    )
    database.save_article_and_chunks(
        {
            "id": "article-b",
            "sha256": "b" * 64,
            "doi": None,
            "title": "Unrelated synthetic control",
            "authors": [],
            "publication_year": 2023,
            "pdf_path": "data/pdf/b.pdf",
            "validation_status": "indexed",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": 5,
                "page_end": 5,
                "chunk_index": 0,
                "text": "A semantic control passage without exact query terminology.",
                "token_count": 8,
            }
        ],
    )
    return [int(row["id"]) for row in database.chunks_for_embedding(limit=10)]


def test_weighted_rrf_is_exact_deterministic_and_deduplicated() -> None:
    fused = reciprocal_rank_fusion(
        [
            RankedList("lexical", 0.35, (1, 2, 3)),
            RankedList("vector", 0.45, (3, 2, 4)),
        ],
        k=60,
    )
    assert [result.chunk_id for result in fused] == [3, 2, 4, 1]
    assert fused[0].score == pytest.approx(0.35 / 63 + 0.45 / 61)
    assert fused[0].source_ranks == {"lexical": 3, "vector": 1}

    duplicate = reciprocal_rank_fusion([RankedList("one", 1.0, (1, 1, 2))], k=60)
    assert duplicate[0].score == pytest.approx(1.0 / 61)
    assert duplicate[0].source_ranks == {"one": 1}


def test_rrf_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="positive"):
        reciprocal_rank_fusion([], k=0)
    with pytest.raises(ValueError, match="unique"):
        reciprocal_rank_fusion([RankedList("same", 1.0, (1,)), RankedList("same", 1.0, (2,))])
    with pytest.raises(ValueError, match="negative"):
        reciprocal_rank_fusion([RankedList("bad", -1.0, (1,))])


def test_hybrid_search_fuses_channels_and_hydrates_sqlite(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    chunk_ids = _seed_hybrid_database(database)
    backend = FixedQueryBackend()
    index = QdrantLocalIndex(settings, model_name=backend.model_name, collection_name="hybrid")
    index.upsert(
        EmbeddedChunkBatch(
            chunk_ids=tuple(chunk_ids),
            article_ids=("article-a", "article-a", "article-b"),
            sections=("Results", "Discussion", "Results"),
            page_starts=(2, 3, 5),
            page_ends=(2, 3, 5),
            vectors=((1.0, 0.0), (0.0, 1.0), (0.9, 0.1)),
            model_name=backend.model_name,
            vector_dimension=2,
        )
    )
    hybrid = HybridSearchService(
        settings,
        database,
        LexicalSearchService(settings, database),
        VectorSearchService(database, backend, index),
    )
    try:
        response = hybrid.search("temperature fermentation", limit=3)
        assert response.results[0].chunk_id == chunk_ids[0]
        assert response.results[0].lexical_rank is not None
        assert response.results[0].vector_rank == 1
        assert response.results[0].page_start == 2
        assert response.results[0].text.startswith("Fermentation temperature")
        assert response.lexical_candidates == 2
        assert response.vector_candidates == 3
        assert response.unique_candidates == 3
        assert response.reserved_reranker_weight == 0.20
    finally:
        hybrid.close()


def test_hybrid_filters_are_applied_to_both_channels(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    chunk_ids = _seed_hybrid_database(database)
    backend = FixedQueryBackend()
    index = QdrantLocalIndex(
        settings, model_name=backend.model_name, collection_name="filtered_hybrid"
    )
    index.upsert(
        EmbeddedChunkBatch(
            chunk_ids=tuple(chunk_ids),
            article_ids=("article-a", "article-a", "article-b"),
            sections=("Results", "Discussion", "Results"),
            page_starts=(2, 3, 5),
            page_ends=(2, 3, 5),
            vectors=((1.0, 0.0), (0.0, 1.0), (0.9, 0.1)),
            model_name=backend.model_name,
            vector_dimension=2,
        )
    )
    hybrid = HybridSearchService(
        settings,
        database,
        LexicalSearchService(settings, database),
        VectorSearchService(database, backend, index),
    )
    try:
        by_article = hybrid.search("temperature", article_ids=["article-a"], limit=10)
        assert {result.article_id for result in by_article.results} == {"article-a"}
        by_section = hybrid.search("temperature", sections=["Results"], limit=10)
        assert all(result.section == "Results" for result in by_section.results)
    finally:
        hybrid.close()


def test_hybrid_query_variants_are_deduplicated_and_bounded(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    chunk_ids = _seed_hybrid_database(database)
    backend = FixedQueryBackend()
    index = QdrantLocalIndex(settings, model_name=backend.model_name, collection_name="variants")
    index.upsert(
        EmbeddedChunkBatch(
            chunk_ids=tuple(chunk_ids),
            article_ids=("article-a", "article-a", "article-b"),
            sections=("Results", "Discussion", "Results"),
            page_starts=(2, 3, 5),
            page_ends=(2, 3, 5),
            vectors=((1.0, 0.0), (0.0, 1.0), (0.9, 0.1)),
            model_name=backend.model_name,
            vector_dimension=2,
        )
    )
    hybrid = HybridSearchService(
        settings,
        database,
        LexicalSearchService(settings, database),
        VectorSearchService(database, backend, index),
    )
    try:
        response = hybrid.search(
            "temperature fermentation",
            query_variants=["Temperature Fermentation", "aroma concentration"],
            limit=2,
        )
        assert response.queries == [
            "temperature fermentation",
            "aroma concentration",
        ]
        settings.retrieval.hybrid_max_query_variants = 2
        with pytest.raises(ValueError, match="too many"):
            hybrid.search("one", query_variants=["two", "three"])
        with pytest.raises(ValueError, match="cannot be empty"):
            hybrid.search("  ")
    finally:
        hybrid.close()


def test_hybrid_search_keeps_full_text_lexical_results_when_vectors_exceed_memory(
    settings,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _seed_hybrid_database(database)
    backend = FixedQueryBackend()
    index = QdrantLocalIndex(
        settings,
        model_name=backend.model_name,
        collection_name="low_memory_hybrid",
    )
    hybrid = HybridSearchService(
        settings,
        database,
        LexicalSearchService(settings, database),
        VectorSearchService(database, backend, index),
    )
    hybrid.memory = LowMemoryGuard()

    response = hybrid.search(
        "temperature fermentation",
        query_variants=["aroma concentration"],
        limit=3,
    )

    assert {result.article_id for result in response.results} == {"article-a"}
    assert all(result.lexical_rank is not None for result in response.results)
    assert all(result.vector_rank is None for result in response.results)
    assert response.lexical_candidates == 3
    assert response.vector_candidates == 0
    assert response.vector_search_degraded is True
    assert backend.closed is True
