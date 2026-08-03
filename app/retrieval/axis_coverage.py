"""Preserve research-axis coverage while merging article retrieval results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.retrieval.article_ranking import RankedArticle
from app.updates.models import normalize_doi


def canonical_article_key(article: RankedArticle) -> str:
    """Return a stable identity: normalized DOI first, local article id otherwise."""

    doi = normalize_doi(article.doi)
    if doi is not None:
        return f"doi:{doi}"
    return f"article:{article.article_id.strip().casefold()}"


@dataclass(frozen=True, slots=True)
class AxisCoveragePool:
    """Deduplicated candidates plus their rank within each research axis."""

    articles: list[RankedArticle]
    axis_ranks: dict[str, dict[str, int]]


def merge_axis_rankings(
    global_articles: Sequence[RankedArticle],
    axis_articles: Mapping[str, Sequence[RankedArticle]],
) -> AxisCoveragePool:
    """Merge global and per-axis rankings without losing niche-axis candidates.

    The global ranking remains authoritative when the same DOI occurs more than
    once. Per-axis memberships are accumulated on the canonical identity even
    when a duplicate article record is discarded.
    """

    retained: dict[str, RankedArticle] = {}
    order: list[str] = []
    for article in global_articles:
        key = canonical_article_key(article)
        if key not in retained:
            retained[key] = article
            order.append(key)

    axis_ranks: dict[str, dict[str, int]] = {}
    for raw_axis_key, articles in axis_articles.items():
        axis_key = raw_axis_key.strip()
        if not axis_key or axis_key in axis_ranks:
            continue
        ranks: dict[str, int] = {}
        for rank, article in enumerate(articles, start=1):
            key = canonical_article_key(article)
            ranks.setdefault(key, rank)
            if key not in retained:
                retained[key] = article
                order.append(key)
        axis_ranks[axis_key] = ranks

    return AxisCoveragePool(
        articles=[retained[key] for key in order],
        axis_ranks=axis_ranks,
    )


def select_with_axis_coverage(
    assessed_articles: Sequence[tuple[float, RankedArticle]],
    *,
    article_count: int,
    axis_ranks: Mapping[str, Mapping[str, int]],
) -> list[RankedArticle]:
    """Apply a soft quota of one distinct article per axis, then fill globally."""

    if article_count < 1:
        raise ValueError("article count must be positive")
    assessed_order = {
        canonical_article_key(article): position
        for position, (_score, article) in enumerate(assessed_articles)
    }
    by_key = {canonical_article_key(article): article for _score, article in assessed_articles}
    selected: list[RankedArticle] = []
    selected_keys: set[str] = set()

    for ranks in axis_ranks.values():
        eligible = [key for key in ranks if key in by_key and key not in selected_keys]
        if not eligible:
            continue
        winner = min(
            eligible,
            key=lambda key: (ranks[key], assessed_order[key], key),
        )
        selected.append(by_key[winner])
        selected_keys.add(winner)
        if len(selected) >= article_count:
            return selected

    for _score, article in assessed_articles:
        if len(selected) >= article_count:
            break
        key = canonical_article_key(article)
        if key in selected_keys:
            continue
        selected.append(article)
        selected_keys.add(key)
    return selected
