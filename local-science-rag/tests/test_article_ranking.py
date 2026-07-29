from __future__ import annotations

from contextlib import closing

import pytest

from app.database.sqlite import Database
from app.retrieval.article_ranking import ArticleRankingService
from app.retrieval.hybrid_search import HybridChunkResult


def _seed_article(
    database: Database,
    article_id: str,
    *,
    title: str,
    abstract: str | None,
    journal: str | None = None,
    year: int | None = None,
    doi: str | None = None,
    chunks: list[str] | None = None,
) -> list[int]:
    database.save_article_and_chunks(
        {
            "id": article_id,
            "sha256": f"{int(article_id.rsplit('-', 1)[-1]):064x}",
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "authors": ["Ada Example", "Louis Test"],
            "journal": journal,
            "publication_year": year,
            "language": "en",
            "pdf_path": f"data/pdf/{article_id}.pdf",
            "validation_status": "indexed",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "page_start": index + 2,
                "page_end": index + 2,
                "chunk_index": index,
                "text": text,
                "token_count": max(len(text.split()), 1),
                "embedding_status": "indexed",
            }
            for index, text in enumerate(chunks or [title])
        ],
    )
    with closing(database.connect()) as connection:
        return [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM chunks WHERE article_id = ? ORDER BY chunk_index",
                (article_id,),
            )
        ]


def _hybrid(
    chunk_id: int,
    article_id: str,
    *,
    rank: int,
    score: float,
    text: str,
    title: str = "untrusted retrieval title",
) -> HybridChunkResult:
    return HybridChunkResult(
        rank=rank,
        chunk_id=chunk_id,
        article_id=article_id,
        article_title=title,
        publication_year=1900,
        section="Results",
        page_start=rank + 1,
        page_end=rank + 1,
        text=text,
        hybrid_score=score,
        lexical_rank=rank,
        vector_rank=rank,
        lexical_score=1.0,
        vector_score=0.9,
        source_ranks={"lexical:0": rank, "vector:0": rank},
        source_contributions={"lexical:0": score / 2, "vector:0": score / 2},
        matched_queries=["query"],
    )


def test_article_score_aggregates_fragments_and_metadata_relevance(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    chunk_ids = _seed_article(
        database,
        "article-1",
        title="Apple response",
        abstract="Climate observations",
        year=2024,
        doi="10.1000/authoritative",
        chunks=["apple response", "secondary result", "third result"],
    )
    other_id = _seed_article(
        database,
        "article-2",
        title="Control study",
        abstract="Unrelated observations",
        chunks=["control"],
    )[0]
    results = [
        _hybrid(chunk_ids[0], "article-1", rank=1, score=0.9, text="apple response"),
        _hybrid(chunk_ids[1], "article-1", rank=2, score=0.6, text="secondary"),
        _hybrid(chunk_ids[2], "article-1", rank=3, score=0.3, text="third"),
        _hybrid(other_id, "article-2", rank=4, score=0.45, text="control"),
    ]

    response = ArticleRankingService(settings, database).rank_candidates(
        "apple climate",
        results,
        article_count=2,
        diversity_mode="none",
        central_concepts=["apple"],
    )

    article = response.articles[0]
    assert article.article_id == "article-1"
    assert article.title == "Apple response"
    assert article.doi == "10.1000/authoritative"
    assert article.authors == ["Ada Example", "Louis Test"]
    assert article.score_components.best_fragment == 1.0
    assert article.score_components.top_three_mean == 1.0
    assert article.score_components.title_relevance == 0.5
    assert article.score_components.abstract_relevance == 0.5
    assert article.score_components.central_concept == 1.0
    assert article.base_score == 0.875
    assert article.top_chunk_ids == chunk_ids
    assert article.page_ranges == ["2", "3", "4"]


def test_selects_exactly_twenty_distinct_articles_from_twenty_five(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    results: list[HybridChunkResult] = []
    for index in range(25):
        article_id = f"article-{index + 1}"
        chunk_id = _seed_article(
            database,
            article_id,
            title=f"Topic study {index + 1}",
            abstract="Topic evidence",
            journal=f"Journal {index % 5}",
            year=2000 + index,
        )[0]
        results.append(
            _hybrid(
                chunk_id,
                article_id,
                rank=index + 1,
                score=1.0 - index / 100,
                text=f"Topic evidence {index + 1}",
            )
        )

    response = ArticleRankingService(settings, database).rank_candidates(
        "topic", results, diversity_mode="none"
    )

    assert response.requested_article_count == 20
    assert response.available_article_count == 25
    assert response.selected_article_count == 20
    assert len({article.article_id for article in response.articles}) == 20
    assert [article.rank for article in response.articles] == list(range(1, 21))


def test_balanced_diversity_defers_near_duplicate(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    common = {
        "title": "Temperature effects in cider fermentation",
        "abstract": "Temperature changes cider fermentation aroma.",
        "journal": "Cider Science",
        "year": 2024,
        "chunks": ["Temperature changes cider fermentation aroma."],
    }
    first_id = _seed_article(database, "article-1", **common)[0]
    duplicate_id = _seed_article(database, "article-2", **common)[0]
    diverse_id = _seed_article(
        database,
        "article-3",
        title="Cider temperature and phenolic stability",
        abstract="Phenolic oxidation measurements across storage conditions.",
        journal="Food Chemistry",
        year=2018,
        chunks=["Cider temperature affected phenolic oxidation during storage."],
    )[0]
    results = [
        _hybrid(
            first_id,
            "article-1",
            rank=1,
            score=1.00,
            text=common["chunks"][0],
        ),
        _hybrid(
            duplicate_id,
            "article-2",
            rank=2,
            score=0.99,
            text=common["chunks"][0],
        ),
        _hybrid(
            diverse_id,
            "article-3",
            rank=3,
            score=0.96,
            text="Cider temperature affected phenolic oxidation during storage.",
        ),
    ]
    service = ArticleRankingService(settings, database)

    without_diversity = service.rank_candidates(
        "temperature cider", results, article_count=3, diversity_mode="none"
    )
    balanced = service.rank_candidates(
        "temperature cider", results, article_count=3, diversity_mode="balanced"
    )

    assert [item.article_id for item in without_diversity.articles] == [
        "article-1",
        "article-2",
        "article-3",
    ]
    assert [item.article_id for item in balanced.articles] == [
        "article-1",
        "article-3",
        "article-2",
    ]
    assert balanced.articles[2].diversity_penalty == pytest.approx(0.15)
    assert any("near-duplicate" in reason for reason in balanced.articles[2].diversity_reasons)


def test_exclusions_and_empty_candidates_are_reported(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    first = _seed_article(
        database,
        "article-1",
        title="First topic",
        abstract=None,
    )[0]
    second = _seed_article(
        database,
        "article-2",
        title="Second topic",
        abstract=None,
    )[0]
    results = [
        _hybrid(first, "article-1", rank=1, score=1.0, text="first topic"),
        _hybrid(second, "article-2", rank=2, score=0.9, text="second topic"),
    ]
    service = ArticleRankingService(settings, database)

    response = service.rank_candidates(
        "topic", results, exclude_article_ids=["article-1", "article-1"]
    )
    empty = service.rank_candidates("topic", [], article_count=20)

    assert response.excluded_article_ids == ["article-1"]
    assert [article.article_id for article in response.articles] == ["article-2"]
    assert empty.available_article_count == 0
    assert empty.selected_article_count == 0
    assert empty.articles == []
