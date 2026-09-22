"""Explicit, read-only inspection of the bounded hybrid retrieval pipeline."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.retrieval.hybrid_search import HybridSearchService
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.scientific_intent import analyze_scientific_intent
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService


def inspect_retrieval(
    settings: Settings,
    database: Database,
    *,
    query: str,
    limit: int,
) -> dict[str, Any]:
    """Run retrieval only after an explicit request; never invoke generation or acquisition."""

    backend = SentenceTransformerBackend(settings)
    service = HybridSearchService(
        settings,
        database,
        LexicalSearchService(settings, database),
        VectorSearchService(database, backend, QdrantLocalIndex(settings)),
    )
    try:
        response = service.search(query, limit=limit, candidate_limit=max(limit, 20))
    finally:
        service.close()
    return {
        "query": response.original_query,
        "queries": response.queries,
        "intent": analyze_scientific_intent(query).model_dump(mode="json"),
        "counters": {
            "lexical_candidates": response.lexical_candidates,
            "vector_candidates": response.vector_candidates,
            "unique_candidates": response.unique_candidates,
            "vector_query_count": response.vector_query_count,
            "vector_search_degraded": response.vector_search_degraded,
            "dense_article_prefilter_used": response.dense_article_prefilter_used,
            "dense_article_prefilter_article_count": response.dense_article_prefilter_article_count,
            "dense_global_query_count": response.dense_global_query_count,
            "duration_seconds": response.duration_seconds,
        },
        "rrf": {
            "k": response.rrf_k,
            "lexical_weight": response.lexical_weight,
            "vector_weight": response.vector_weight,
        },
        "candidates": [
            {
                "rank": item.rank,
                "chunk_id": item.chunk_id,
                "article_id": item.article_id,
                "article_title": item.article_title,
                "section": item.section,
                "locator_kind": item.locator_kind,
                "page_start": item.page_start,
                "page_end": item.page_end,
                "section_path": item.section_path,
                "paragraph_start": item.paragraph_start,
                "paragraph_end": item.paragraph_end,
                "hybrid_score": item.hybrid_score,
                "lexical_rank": item.lexical_rank,
                "vector_rank": item.vector_rank,
                "lexical_score": item.lexical_score,
                "vector_score": item.vector_score,
                "source_ranks": item.source_ranks,
                "source_contributions": item.source_contributions,
                "matched_queries": item.matched_queries,
                "outline_expanded": "outline" in item.source_ranks,
            }
            for item in response.results
        ],
    }
