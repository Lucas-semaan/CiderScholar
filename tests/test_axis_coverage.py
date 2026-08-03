from __future__ import annotations

from app.corpora import CorpusScope
from app.retrieval.article_ranking import ArticleScoreComponents, RankedArticle
from app.retrieval.axis_coverage import merge_axis_rankings, select_with_axis_coverage


def _article(
    article_id: str,
    *,
    doi: str | None = None,
    score: float = 0.8,
    title: str | None = None,
) -> RankedArticle:
    return RankedArticle(
        rank=1,
        base_rank=1,
        article_id=article_id,
        doi=doi,
        title=title or article_id,
        abstract="Abstract",
        authors=[],
        journal=None,
        publication_year=2026,
        language="en",
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
        scope=CorpusScope.COMMON,
    )


def test_axis_pool_keeps_candidates_omitted_by_global_fusion() -> None:
    dominant = _article("dominant")
    mechanism = _article("mechanism")
    method = _article("method")

    pool = merge_axis_rankings(
        [dominant],
        {
            "mechanisms": [mechanism, dominant],
            "methods": [method],
        },
    )

    assert [article.article_id for article in pool.articles] == [
        "dominant",
        "mechanism",
        "method",
    ]
    assert pool.axis_ranks["mechanisms"] == {
        "article:mechanism": 1,
        "article:dominant": 2,
    }


def test_axis_pool_deduplicates_normalized_doi_and_keeps_all_memberships() -> None:
    global_copy = _article("global-copy", doi="https://doi.org/10.1000/DUPLICATE")
    axis_copy = _article("axis-copy", doi="10.1000/duplicate.")

    pool = merge_axis_rankings(
        [global_copy],
        {"methods": [axis_copy]},
    )

    assert [article.article_id for article in pool.articles] == ["global-copy"]
    assert pool.axis_ranks["methods"] == {"doi:10.1000/duplicate": 1}


def test_axis_pool_does_not_merge_doi_less_articles_on_title_alone() -> None:
    first = _article("first-copy", title="Shared title without a DOI")
    second = _article("second-copy", title="Shared title without a DOI")

    pool = merge_axis_rankings([first], {"methods": [second]})

    assert [article.article_id for article in pool.articles] == [
        "first-copy",
        "second-copy",
    ]


def test_soft_axis_quota_precedes_global_score_and_uses_distinct_articles() -> None:
    dominant = _article("dominant", score=0.99)
    mechanism = _article("mechanism", score=0.70)
    method = _article("method", score=0.60)
    assessed = [(0.99, dominant), (0.70, mechanism), (0.60, method)]

    selected = select_with_axis_coverage(
        assessed,
        article_count=3,
        axis_ranks={
            "mechanisms": {
                "article:dominant": 1,
                "article:mechanism": 2,
            },
            "methods": {
                "article:dominant": 1,
                "article:method": 2,
            },
        },
    )

    assert [article.article_id for article in selected] == [
        "dominant",
        "method",
        "mechanism",
    ]
