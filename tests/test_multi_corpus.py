from __future__ import annotations

import inspect

from app.corpora import CorpusScope
from app.retrieval import multi_corpus
from app.retrieval.article_ranking import ArticleScoreComponents, RankedArticle
from app.retrieval.hybrid_search import HybridChunkResult
from app.retrieval.lexical_search import (
    LexicalSearchResponse,
    LexicalSearchResult,
    PreparedLexicalQuery,
)
from app.retrieval.multi_corpus import (
    MultiCorpusLexicalSearchService,
    MultiCorpusReader,
    MultiCorpusVectorSearchService,
    deduplicate_scoped_articles,
    merge_scoped_hybrid_results,
)
from app.retrieval.vector_search import VectorSearchResult


def test_multi_corpus_reader_opens_and_closes_scopes_sequentially() -> None:
    events: list[str] = []
    open_scopes: set[CorpusScope] = set()

    class FakeReader:
        def __init__(self, scope: CorpusScope) -> None:
            self.scope = scope
            assert not open_scopes
            open_scopes.add(scope)
            events.append(f"open:{scope}")

        def close(self) -> None:
            open_scopes.remove(self.scope)
            events.append(f"close:{self.scope}")

    reader = MultiCorpusReader(
        {
            CorpusScope.COMMON: lambda: FakeReader(CorpusScope.COMMON),
        }
    )

    results = reader.read_sequentially(
        lambda scope, _reader: [scope.value],
    )

    assert results == ["common"]
    assert events == ["open:common", "close:common"]
    assert not open_scopes


def test_multi_corpus_reader_has_no_fastapi_dependency() -> None:
    assert "fastapi" not in inspect.getsource(multi_corpus)


def test_lexical_search_reads_common_and_marks_every_hit() -> None:
    events: list[str] = []

    class FakeLexicalReader:
        def __init__(self, scope: CorpusScope) -> None:
            self.scope = scope

        def search(self, query: str, **_kwargs) -> LexicalSearchResponse:
            events.append(f"search:{self.scope}")
            return LexicalSearchResponse(
                query=PreparedLexicalQuery(
                    original_query=query,
                    normalized_query=query.casefold(),
                    terms=[query.casefold()],
                    fts5_expression=f'"{query.casefold()}"',
                    mode="any",
                ),
                results=[
                    LexicalSearchResult(
                        rank=1,
                        chunk_id=1,
                        article_id=f"{self.scope}-article",
                        article_title=f"Titre {self.scope}",
                        publication_year=2026,
                        section=None,
                        page_start=1,
                        page_end=1,
                        text=f"Preuve {self.scope}",
                        bm25_score=-1.0,
                        relevance_score=1.0,
                    )
                ],
                duration_seconds=0.01,
            )

        def close(self) -> None:
            events.append(f"close:{self.scope}")

    service = MultiCorpusLexicalSearchService(
        MultiCorpusReader(
            {scope: lambda scope=scope: FakeLexicalReader(scope) for scope in CorpusScope}
        )
    )

    response = service.search("Cidre")

    assert [result.scope for result in response.results] == [CorpusScope.COMMON]
    assert events == [
        "search:common",
        "close:common",
    ]


def test_vector_search_opens_and_closes_indexes_sequentially() -> None:
    events: list[str] = []
    open_scope: CorpusScope | None = None

    class FakeVectorReader:
        def __init__(self, scope: CorpusScope) -> None:
            nonlocal open_scope
            assert open_scope is None
            open_scope = scope
            self.scope = scope
            events.append(f"open:{scope}")

        def search(self, _query: str, **_kwargs) -> list[VectorSearchResult]:
            return [
                VectorSearchResult(
                    chunk_id=1,
                    article_id=f"{self.scope}-article",
                    score=0.9,
                    section=None,
                    page_start=1,
                    page_end=1,
                    text=f"Preuve {self.scope}",
                )
            ]

        def close(self) -> None:
            nonlocal open_scope
            events.append(f"close:{self.scope}")
            open_scope = None

    service = MultiCorpusVectorSearchService(
        MultiCorpusReader(
            {scope: lambda scope=scope: FakeVectorReader(scope) for scope in CorpusScope}
        )
    )

    response = service.search("fermentation")

    assert [result.scope for result in response.results] == [CorpusScope.COMMON]
    assert events == ["open:common", "close:common"]
    assert open_scope is None


def test_multi_corpus_fusion_is_deterministic_and_explainable() -> None:
    def hybrid(chunk_id: int, score: float, rank: int) -> HybridChunkResult:
        return HybridChunkResult(
            rank=rank,
            chunk_id=chunk_id,
            article_id=f"article-{chunk_id}",
            article_title=f"Titre {chunk_id}",
            publication_year=2026,
            section=None,
            page_start=1,
            page_end=1,
            text="Preuve",
            hybrid_score=score,
            lexical_rank=rank,
            vector_rank=None,
            lexical_score=1.0,
            vector_score=None,
            source_ranks={"lexical:0": rank},
            source_contributions={"lexical:0": score},
            matched_queries=["cidre"],
        )

    merged = merge_scoped_hybrid_results(
        {
            CorpusScope.COMMON: [hybrid(1, 0.8, 1), hybrid(3, 0.7, 2)],
        },
        limit=3,
    )

    assert [(item.rank, item.scope, item.corpus_rank) for item in merged] == [
        (1, CorpusScope.COMMON, 1),
        (2, CorpusScope.COMMON, 2),
    ]
    assert merged[0].hybrid_score == 0.8
    assert merged[0].source_contributions == {"lexical:0": 0.8}


def _ranked_article(
    article_id: str,
    scope: CorpusScope,
    *,
    doi: str | None,
    score: float,
) -> RankedArticle:
    return RankedArticle(
        rank=1,
        base_rank=1,
        article_id=article_id,
        doi=doi,
        title=article_id,
        abstract="Résumé",
        authors=[],
        journal=None,
        publication_year=2026,
        language="fr",
        source="local",
        validation_status="validated",
        base_score=score,
        adjusted_score=score,
        diversity_penalty=0.0,
        diversity_reasons=[],
        score_components=ArticleScoreComponents(
            best_fragment=score,
            top_three_mean=score,
            title_relevance=score,
            abstract_relevance=score,
            central_concept=score,
        ),
        matched_chunk_count=1,
        best_chunk_id=1,
        best_hybrid_rank=1,
        top_chunk_ids=[1],
        page_ranges=["1"],
        scope=scope,
    )


def test_duplicate_doi_keeps_first_common_article() -> None:
    first = _ranked_article("first", CorpusScope.COMMON, doi="10.1000/duplicate", score=0.9)
    duplicate = _ranked_article(
        "duplicate", CorpusScope.COMMON, doi="https://doi.org/10.1000/DUPLICATE", score=0.7
    )

    deduplicated = deduplicate_scoped_articles([first, duplicate])

    assert [article.article_id for article in deduplicated] == ["first"]


def test_doi_less_articles_are_never_merged_on_title_alone() -> None:
    common = _ranked_article(
        "other-same-title",
        CorpusScope.COMMON,
        doi=None,
        score=0.8,
    )
    other = _ranked_article(
        "same-title",
        CorpusScope.COMMON,
        doi=None,
        score=0.8,
    )

    deduplicated = deduplicate_scoped_articles([other, common])

    assert {(article.scope, article.article_id) for article in deduplicated} == {
        (CorpusScope.COMMON, "same-title"),
        (CorpusScope.COMMON, "other-same-title"),
    }
