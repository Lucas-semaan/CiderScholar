"""Search isolated corpora sequentially and merge their ranked articles."""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths, settings_for_corpus
from app.database.sqlite import Database
from app.retrieval.article_ranking import ArticleRankingResponse, RankedArticle
from app.retrieval.multi_corpus import deduplicate_scoped_articles
from app.services.workflows import rank_question


def rank_question_across_corpora(
    settings: Settings,
    *,
    question: str,
    article_count: int,
    diversity_mode: str,
    scopes: Sequence[CorpusScope],
    variants: Sequence[str] | None = None,
    central_concepts: Sequence[str] | None = None,
    excluded_article_ids: Sequence[str] | None = None,
) -> ArticleRankingResponse:
    """Open and close all heavyweight readers for one scope before the next."""

    if not scopes:
        raise ValueError("at least one corpus scope is required")
    started = perf_counter()
    responses: list[ArticleRankingResponse] = []
    articles: list[RankedArticle] = []
    for scope in tuple(dict.fromkeys(scopes)):
        scoped_settings = settings_for_corpus(settings, scope)
        response = rank_question(
            scoped_settings,
            Database(corpus_paths(settings, scope).database_path),
            question=question,
            article_count=article_count,
            diversity_mode=diversity_mode,
            variants=variants,
            central_concepts=central_concepts,
            excluded_article_ids=excluded_article_ids,
        )
        responses.append(response)
        articles.extend(
            article.model_copy(update={"scope": scope}) for article in response.articles
        )

    deduplicated = deduplicate_scoped_articles(articles)
    merged = deduplicated[:article_count]
    first = responses[0]
    return ArticleRankingResponse(
        query=first.query,
        query_terms=first.query_terms,
        central_concepts=first.central_concepts,
        diversity_mode=first.diversity_mode,
        requested_article_count=article_count,
        available_article_count=len(deduplicated),
        selected_article_count=len(merged),
        excluded_article_ids=list(excluded_article_ids or ()),
        hybrid_candidate_count=sum(item.hybrid_candidate_count for item in responses),
        articles=merged,
        duration_seconds=perf_counter() - started,
    )
