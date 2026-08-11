"""Weighted Reciprocal Rank Fusion of local FTS5 and Qdrant results."""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.corpora import CorpusScope
from app.database.sqlite import Database
from app.memory import MemoryGuard, MemoryLimitError
from app.retrieval.lexical_search import LexicalSearchService, QueryMode
from app.retrieval.vector_search import VectorSearchService

LOGGER = logging.getLogger(__name__)


class HybridSearchIntegrityError(RuntimeError):
    """Raised when a fused candidate cannot be reconstructed from SQLite."""


@dataclass(frozen=True, slots=True)
class RankedList:
    source: str
    weight: float
    chunk_ids: tuple[int, ...]


class FusedRank(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: int = Field(gt=0)
    score: float = Field(ge=0.0)
    source_ranks: dict[str, int]
    source_contributions: dict[str, float]


def reciprocal_rank_fusion(
    rankings: Sequence[RankedList],
    *,
    k: int = 60,
    limit: int | None = None,
) -> list[FusedRank]:
    """Fuse bounded ranked lists; raw BM25/cosine magnitudes are intentionally ignored."""

    if k <= 0:
        raise ValueError("RRF k must be positive")
    if limit is not None and limit <= 0:
        raise ValueError("RRF limit must be positive")
    sources = [ranking.source for ranking in rankings]
    if len(sources) != len(set(sources)):
        raise ValueError("RRF source names must be unique")

    scores: dict[int, float] = {}
    source_ranks: dict[int, dict[str, int]] = {}
    contributions: dict[int, dict[str, float]] = {}
    for ranking in rankings:
        if ranking.weight < 0:
            raise ValueError("RRF weights cannot be negative")
        seen: set[int] = set()
        for rank, chunk_id in enumerate(ranking.chunk_ids, start=1):
            if chunk_id <= 0:
                raise ValueError("RRF chunk ids must be positive")
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            contribution = ranking.weight / (k + rank)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + contribution
            source_ranks.setdefault(chunk_id, {})[ranking.source] = rank
            contributions.setdefault(chunk_id, {})[ranking.source] = contribution

    ordered_ids = sorted(
        scores,
        key=lambda chunk_id: (
            -scores[chunk_id],
            min(source_ranks[chunk_id].values()),
            chunk_id,
        ),
    )
    if limit is not None:
        ordered_ids = ordered_ids[:limit]
    return [
        FusedRank(
            chunk_id=chunk_id,
            score=scores[chunk_id],
            source_ranks=source_ranks[chunk_id],
            source_contributions=contributions[chunk_id],
        )
        for chunk_id in ordered_ids
    ]


class HybridChunkResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    corpus_rank: int | None = Field(default=None, ge=1)
    chunk_id: int = Field(gt=0)
    article_id: str
    article_title: str
    publication_year: int | None
    section: str | None
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    text: str
    hybrid_score: float = Field(ge=0.0)
    lexical_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)
    lexical_score: float | None = Field(default=None, ge=0.0)
    vector_score: float | None = None
    source_ranks: dict[str, int]
    source_contributions: dict[str, float]
    matched_queries: list[str]
    scope: CorpusScope = CorpusScope.COMMON


class HybridSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_query: str
    queries: list[str]
    results: list[HybridChunkResult]
    lexical_candidates: int = Field(ge=0)
    vector_candidates: int = Field(ge=0)
    vector_search_degraded: bool = False
    unique_candidates: int = Field(ge=0)
    lexical_weight: float = Field(ge=0.0)
    vector_weight: float = Field(ge=0.0)
    reserved_reranker_weight: float = Field(ge=0.0)
    rrf_k: int = Field(gt=0)
    duration_seconds: float = Field(ge=0.0)


class HybridSearchService:
    """Run light lexical retrieval, then local vector retrieval, sequentially."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        lexical: LexicalSearchService,
        vector: VectorSearchService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.lexical = lexical
        self.vector = vector
        self.memory = MemoryGuard(settings.memory)
        self._vector_disabled_by_memory = False

    def _disable_vector_after_memory_limit(
        self,
        *,
        operation: str,
        error: MemoryLimitError,
    ) -> None:
        """Release heavy vector resources while preserving lexical retrieval."""

        if self._vector_disabled_by_memory:
            return
        self._vector_disabled_by_memory = True
        LOGGER.warning(
            "Hybrid search continuing without vectors operation=%s error_type=%s",
            operation,
            type(error).__name__,
        )
        try:
            self.vector.close()
        except Exception:
            LOGGER.exception("Unable to release vector resources after memory limit")

    def _queries(self, original_query: str, query_variants: Sequence[str] | None) -> list[str]:
        original = original_query.strip()
        if not original:
            raise ValueError("hybrid query cannot be empty")
        candidates = [original, *(query_variants or [])]
        unique: list[str] = []
        normalized_seen: set[str] = set()
        for candidate in candidates:
            cleaned = candidate.strip()
            if not cleaned:
                continue
            normalized = unicodedata.normalize("NFKC", cleaned).casefold()
            if normalized not in normalized_seen:
                normalized_seen.add(normalized)
                unique.append(cleaned)
        if len(unique) > self.settings.retrieval.hybrid_max_query_variants:
            raise ValueError("too many hybrid query variants")
        return unique

    def search(
        self,
        query: str,
        *,
        query_variants: Sequence[str] | None = None,
        limit: int | None = None,
        candidate_limit: int | None = None,
        lexical_mode: QueryMode = "any",
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> HybridSearchResponse:
        started = perf_counter()
        queries = self._queries(query, query_variants)
        result_limit = self.settings.retrieval.hybrid_default_limit if limit is None else limit
        retrieval_limit = (
            self.settings.retrieval.hybrid_candidate_limit
            if candidate_limit is None
            else candidate_limit
        )
        if not 1 <= result_limit <= 1000:
            raise ValueError("hybrid result limit must be between 1 and 1000")
        if not 1 <= retrieval_limit <= 1000:
            raise ValueError("hybrid candidate limit must be between 1 and 1000")
        retrieval_limit = max(retrieval_limit, result_limit)

        rankings: list[RankedList] = []
        lexical_candidates = 0
        vector_candidates = 0
        lexical_ranks: dict[int, int] = {}
        vector_ranks: dict[int, int] = {}
        lexical_scores: dict[int, float] = {}
        vector_scores: dict[int, float] = {}
        matched_queries: dict[int, list[str]] = {}
        per_query_lexical_weight = self.settings.retrieval.lexical_weight / len(queries)
        per_query_vector_weight = self.settings.retrieval.vector_weight / len(queries)

        for query_index, current_query in enumerate(queries):
            lexical_response = self.lexical.search(
                current_query,
                limit=retrieval_limit,
                mode=lexical_mode,
                article_ids=article_ids,
                sections=sections,
            )
            lexical_candidates += len(lexical_response.results)
            rankings.append(
                RankedList(
                    source=f"lexical:{query_index}",
                    weight=per_query_lexical_weight,
                    chunk_ids=tuple(result.chunk_id for result in lexical_response.results),
                )
            )
            for result in lexical_response.results:
                lexical_ranks[result.chunk_id] = min(
                    lexical_ranks.get(result.chunk_id, result.rank), result.rank
                )
                lexical_scores[result.chunk_id] = max(
                    lexical_scores.get(result.chunk_id, 0.0), result.relevance_score
                )
                matched_queries.setdefault(result.chunk_id, []).append(current_query)

            if self._vector_disabled_by_memory:
                continue
            try:
                self.memory.check("hybrid vector query")
            except MemoryLimitError as exc:
                self._disable_vector_after_memory_limit(
                    operation="hybrid vector query",
                    error=exc,
                )
                continue
            try:
                vector_results = self.vector.search(
                    current_query,
                    limit=retrieval_limit,
                    article_ids=article_ids,
                    sections=sections,
                )
            except MemoryLimitError as exc:
                self._disable_vector_after_memory_limit(
                    operation="hybrid vector search",
                    error=exc,
                )
                continue
            try:
                self.memory.check("hybrid vector results")
            except MemoryLimitError as exc:
                # The bounded vector result set is already available. Keep it, but release
                # the model and index before processing more query variants.
                self._disable_vector_after_memory_limit(
                    operation="hybrid vector results",
                    error=exc,
                )
            vector_candidates += len(vector_results)
            rankings.append(
                RankedList(
                    source=f"vector:{query_index}",
                    weight=per_query_vector_weight,
                    chunk_ids=tuple(result.chunk_id for result in vector_results),
                )
            )
            for rank, result in enumerate(vector_results, start=1):
                vector_ranks[result.chunk_id] = min(vector_ranks.get(result.chunk_id, rank), rank)
                vector_scores[result.chunk_id] = max(
                    vector_scores.get(result.chunk_id, float("-inf")), result.score
                )
                queries_for_chunk = matched_queries.setdefault(result.chunk_id, [])
                if current_query not in queries_for_chunk:
                    queries_for_chunk.append(current_query)

        fused = reciprocal_rank_fusion(
            rankings,
            k=self.settings.retrieval.rrf_k,
            limit=result_limit,
        )
        details = self.database.chunk_details_by_ids([candidate.chunk_id for candidate in fused])
        results: list[HybridChunkResult] = []
        for rank, candidate in enumerate(fused, start=1):
            row = details.get(candidate.chunk_id)
            if row is None:
                raise HybridSearchIntegrityError(
                    f"fused chunk {candidate.chunk_id} is unavailable in SQLite"
                )
            results.append(
                HybridChunkResult(
                    rank=rank,
                    chunk_id=candidate.chunk_id,
                    article_id=str(row["article_id"]),
                    article_title=str(row["article_title"]),
                    publication_year=(
                        int(row["publication_year"])
                        if row["publication_year"] is not None
                        else None
                    ),
                    section=row["section"],
                    page_start=int(row["page_start"]),
                    page_end=int(row["page_end"]),
                    text=str(row["text"]),
                    hybrid_score=candidate.score,
                    lexical_rank=lexical_ranks.get(candidate.chunk_id),
                    vector_rank=vector_ranks.get(candidate.chunk_id),
                    lexical_score=lexical_scores.get(candidate.chunk_id),
                    vector_score=vector_scores.get(candidate.chunk_id),
                    source_ranks=candidate.source_ranks,
                    source_contributions=candidate.source_contributions,
                    matched_queries=matched_queries.get(candidate.chunk_id, []),
                )
            )
        return HybridSearchResponse(
            original_query=query.strip(),
            queries=queries,
            results=results,
            lexical_candidates=lexical_candidates,
            vector_candidates=vector_candidates,
            vector_search_degraded=self._vector_disabled_by_memory,
            unique_candidates=len(set(lexical_ranks).union(vector_ranks)),
            lexical_weight=self.settings.retrieval.lexical_weight,
            vector_weight=self.settings.retrieval.vector_weight,
            reserved_reranker_weight=self.settings.retrieval.reranker_weight,
            rrf_k=self.settings.retrieval.rrf_k,
            duration_seconds=perf_counter() - started,
        )

    def close(self) -> None:
        self.vector.close()
