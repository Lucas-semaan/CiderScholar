"""Common-corpus reading without a web-framework dependency."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from time import perf_counter
from typing import Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.retrieval.article_ranking import RankedArticle
from app.retrieval.hybrid_search import HybridChunkResult
from app.retrieval.lexical_search import (
    LexicalSearchResponse,
    LexicalSearchResult,
    LexicalSearchService,
    QueryMode,
)
from app.retrieval.vector_search import (
    QdrantLocalIndex,
    VectorSearchResult,
    VectorSearchService,
)
from app.updates.models import normalize_doi

ResultT = TypeVar("ResultT")


class ClosableCorpusReader(Protocol):
    """Small resource contract shared by lexical and vector adapters."""

    def close(self) -> None: ...


ReaderFactory = Callable[[], ClosableCorpusReader]
ReadOperation = Callable[[CorpusScope, ClosableCorpusReader], Sequence[ResultT]]


class MultiCorpusReader:
    """Open, read and close the common corpus."""

    def __init__(self, factories: Mapping[CorpusScope, ReaderFactory]) -> None:
        self._factories = dict(factories)

    def read_sequentially(
        self,
        operation: ReadOperation[ResultT],
        *,
        scopes: Sequence[CorpusScope] = (CorpusScope.COMMON,),
    ) -> list[ResultT]:
        requested = tuple(dict.fromkeys(scopes))
        results: list[ResultT] = []
        for scope in requested:
            factory = self._factories.get(scope)
            if factory is None:
                continue
            reader = factory()
            try:
                results.extend(operation(scope, reader))
            finally:
                reader.close()
        return results


class MultiCorpusLexicalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[LexicalSearchResult]
    duration_seconds_by_scope: dict[CorpusScope, float]
    duration_seconds: float = Field(ge=0.0)


class LexicalCorpusReader(ClosableCorpusReader, Protocol):
    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        mode: QueryMode = "any",
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> LexicalSearchResponse: ...


class _LexicalReaderAdapter:
    def __init__(self, service: LexicalSearchService) -> None:
        self.service = service

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        mode: QueryMode = "any",
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> LexicalSearchResponse:
        return self.service.search(
            query,
            limit=limit,
            mode=mode,
            article_ids=article_ids,
            sections=sections,
        )

    def close(self) -> None:
        return None


def lexical_corpus_factories(settings: Settings) -> dict[CorpusScope, ReaderFactory]:
    return {
        scope: (
            lambda scope=scope: _LexicalReaderAdapter(
                LexicalSearchService(
                    settings,
                    Database(corpus_paths(settings, scope).database_path),
                )
            )
        )
        for scope in (CorpusScope.COMMON,)
    }


class MultiCorpusLexicalSearchService:
    def __init__(self, reader: MultiCorpusReader) -> None:
        self.reader = reader

    @classmethod
    def from_settings(cls, settings: Settings) -> MultiCorpusLexicalSearchService:
        return cls(MultiCorpusReader(lexical_corpus_factories(settings)))

    def search(
        self,
        query: str,
        *,
        limit_per_scope: int | None = None,
        mode: QueryMode = "any",
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
        scopes: Sequence[CorpusScope] = (CorpusScope.COMMON,),
    ) -> MultiCorpusLexicalResponse:
        durations: dict[CorpusScope, float] = {}

        def search_scope(
            scope: CorpusScope,
            raw_reader: ClosableCorpusReader,
        ) -> list[LexicalSearchResult]:
            corpus_reader = cast(LexicalCorpusReader, raw_reader)
            response = corpus_reader.search(
                query,
                limit=limit_per_scope,
                mode=mode,
                article_ids=article_ids,
                sections=sections,
            )
            durations[scope] = response.duration_seconds
            return [result.model_copy(update={"scope": scope}) for result in response.results]

        results = self.reader.read_sequentially(search_scope, scopes=scopes)
        return MultiCorpusLexicalResponse(
            query=query.strip(),
            results=results,
            duration_seconds_by_scope=durations,
            duration_seconds=sum(durations.values()),
        )


class MultiCorpusVectorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[VectorSearchResult]
    duration_seconds_by_scope: dict[CorpusScope, float]
    duration_seconds: float = Field(ge=0.0)


class VectorCorpusReader(ClosableCorpusReader, Protocol):
    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> list[VectorSearchResult]: ...


class _VectorReaderAdapter:
    def __init__(self, service: VectorSearchService) -> None:
        self.service = service

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> list[VectorSearchResult]:
        return self.service.search(
            query,
            limit=limit,
            article_ids=article_ids,
            sections=sections,
        )

    def close(self) -> None:
        self.service.close()


def vector_corpus_factories(settings: Settings) -> dict[CorpusScope, ReaderFactory]:
    return {
        scope: (
            lambda scope=scope: _VectorReaderAdapter(
                VectorSearchService(
                    Database(corpus_paths(settings, scope).database_path),
                    SentenceTransformerBackend(settings),
                    QdrantLocalIndex(
                        settings,
                        path=corpus_paths(settings, scope).qdrant_dir,
                    ),
                )
            )
        )
        for scope in (CorpusScope.COMMON,)
    }


class MultiCorpusVectorSearchService:
    def __init__(self, reader: MultiCorpusReader) -> None:
        self.reader = reader

    @classmethod
    def from_settings(cls, settings: Settings) -> MultiCorpusVectorSearchService:
        return cls(MultiCorpusReader(vector_corpus_factories(settings)))

    def search(
        self,
        query: str,
        *,
        limit_per_scope: int | None = None,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
        scopes: Sequence[CorpusScope] = (CorpusScope.COMMON,),
    ) -> MultiCorpusVectorResponse:
        durations: dict[CorpusScope, float] = {}

        def search_scope(
            scope: CorpusScope,
            raw_reader: ClosableCorpusReader,
        ) -> list[VectorSearchResult]:
            started = perf_counter()
            corpus_reader = cast(VectorCorpusReader, raw_reader)
            results = corpus_reader.search(
                query,
                limit=limit_per_scope,
                article_ids=article_ids,
                sections=sections,
            )
            durations[scope] = perf_counter() - started
            return [result.model_copy(update={"scope": scope}) for result in results]

        results = self.reader.read_sequentially(search_scope, scopes=scopes)
        return MultiCorpusVectorResponse(
            query=query.strip(),
            results=results,
            duration_seconds_by_scope=durations,
            duration_seconds=sum(durations.values()),
        )


def merge_scoped_hybrid_results(
    results_by_scope: Mapping[CorpusScope, Sequence[HybridChunkResult]],
    *,
    limit: int,
) -> list[HybridChunkResult]:
    """Merge common-corpus RRF scores with stable, explainable tie breaks."""

    if limit <= 0:
        raise ValueError("common-corpus result limit must be positive")
    candidates = [
        result.model_copy(update={"scope": scope, "corpus_rank": result.rank})
        for scope in (CorpusScope.COMMON,)
        for result in results_by_scope.get(scope, ())
    ]
    candidates.sort(
        key=lambda result: (
            -result.hybrid_score,
            result.corpus_rank,
            result.article_id,
            result.chunk_id,
        )
    )
    return [
        result.model_copy(update={"rank": rank})
        for rank, result in enumerate(candidates[:limit], start=1)
    ]


def deduplicate_scoped_articles(
    articles: Sequence[RankedArticle],
) -> list[RankedArticle]:
    """Deduplicate DOI matches while keeping DOI-less records distinct."""

    by_doi: dict[str, RankedArticle] = {}
    without_doi: list[RankedArticle] = []
    for article in articles:
        doi = normalize_doi(article.doi)
        if doi is None:
            without_doi.append(article)
            continue
        current = by_doi.get(doi)
        if current is None:
            by_doi[doi] = article
    retained = [*by_doi.values(), *without_doi]
    retained.sort(
        key=lambda article: (
            -article.adjusted_score,
            article.base_rank,
            article.article_id,
        )
    )
    return [
        article.model_copy(update={"rank": rank}) for rank, article in enumerate(retained, start=1)
    ]
