"""Explainable aggregation and diversity-aware selection of distinct articles."""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.config import Settings
from app.corpora import CorpusScope, corpus_scope_label
from app.database.sqlite import Database
from app.retrieval.hybrid_search import HybridChunkResult, HybridSearchService
from app.retrieval.lexical_search import STOPWORDS, TOKEN_PATTERN, QueryMode

DiversityMode = Literal["none", "theme", "year", "journal", "balanced"]
VALID_DIVERSITY_MODES = frozenset({"none", "theme", "year", "journal", "balanced"})


class ArticleRankingIntegrityError(RuntimeError):
    """Raised when candidate metadata is missing from the authoritative database."""


class ArticleScoreComponents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    best_fragment: float = Field(ge=0.0, le=1.0)
    top_three_mean: float = Field(ge=0.0, le=1.0)
    title_relevance: float = Field(ge=0.0, le=1.0)
    abstract_relevance: float = Field(ge=0.0, le=1.0)
    central_concept: float = Field(ge=0.0, le=1.0)


class RankedArticle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    base_rank: int = Field(ge=1)
    article_id: str
    doi: str | None
    title: str
    abstract: str | None
    authors: list[str]
    journal: str | None
    publication_year: int | None
    language: str | None
    source: str
    validation_status: Literal["validated", "indexed"]
    base_score: float = Field(ge=0.0, le=1.0)
    adjusted_score: float = Field(ge=0.0, le=1.0)
    diversity_penalty: float = Field(ge=0.0, le=1.0)
    diversity_reasons: list[str]
    score_components: ArticleScoreComponents
    matched_chunk_count: int = Field(ge=1)
    best_chunk_id: int = Field(gt=0)
    best_hybrid_rank: int = Field(ge=1)
    top_chunk_ids: list[int] = Field(min_length=1, max_length=8)
    page_ranges: list[str] = Field(min_length=1, max_length=8)
    scope: CorpusScope = CorpusScope.COMMON

    @computed_field
    @property
    def citation_label(self) -> str:
        """Application-rendered citation identity with an unambiguous origin."""

        return f"[{corpus_scope_label(self.scope)} · {self.article_id}]"


class ArticleRankingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    query_terms: list[str]
    central_concepts: list[str]
    diversity_mode: DiversityMode
    requested_article_count: int = Field(ge=1)
    available_article_count: int = Field(ge=0)
    selected_article_count: int = Field(ge=0)
    excluded_article_ids: list[str]
    hybrid_candidate_count: int = Field(ge=0)
    query_variant_count: int = Field(default=1, ge=1)
    lexical_candidate_count: int = Field(default=0, ge=0)
    dense_candidate_count: int = Field(default=0, ge=0)
    rrf_unique_candidate_count: int = Field(default=0, ge=0)
    vector_search_degraded: bool = False
    articles: list[RankedArticle]
    duration_seconds: float = Field(ge=0.0)


@dataclass(frozen=True, slots=True)
class _Candidate:
    article: RankedArticle
    theme_terms: frozenset[str]


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _terms(value: str) -> list[str]:
    normalized = _normalized_text(value)
    return [
        token
        for token in TOKEN_PATTERN.findall(normalized)
        if len(token) >= 2 and token not in STOPWORDS
    ]


def _term_relevance(query_terms: frozenset[str], value: str | None) -> float:
    if not query_terms or not value:
        return 0.0
    value_terms = frozenset(_terms(value))
    return len(query_terms.intersection(value_terms)) / len(query_terms)


def _concept_relevance(concepts: Sequence[str], searchable_text: str) -> float:
    if not concepts:
        return 0.0
    normalized_haystack = f" {' '.join(_terms(searchable_text))} "
    found = 0
    for concept in concepts:
        normalized_concept = " ".join(_terms(concept))
        if normalized_concept and f" {normalized_concept} " in normalized_haystack:
            found += 1
    return found / len(concepts)


def _authors(raw_authors: object) -> list[str]:
    if not isinstance(raw_authors, str):
        return []
    try:
        parsed = json.loads(raw_authors)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(author) for author in parsed if str(author).strip()]


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / len(left.union(right))


class ArticleRankingService:
    """Rank only scientific relevance, then optionally reduce result redundancy."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        hybrid: HybridSearchService | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.hybrid = hybrid

    def _mode(self, requested: DiversityMode | None) -> DiversityMode:
        config = self.settings.article_ranking
        mode = config.diversity_mode if requested is None else requested
        if mode not in VALID_DIVERSITY_MODES:
            raise ValueError(f"unsupported diversity mode: {mode}")
        if not config.diversity_enabled:
            return "none"
        return mode

    @staticmethod
    def _derived_concepts(query_terms: Sequence[str]) -> list[str]:
        return sorted(set(query_terms), key=lambda term: (-len(term), term))[:3]

    def _build_candidates(
        self,
        query: str,
        chunks: Sequence[HybridChunkResult],
        concepts: Sequence[str],
    ) -> list[_Candidate]:
        grouped: dict[str, list[HybridChunkResult]] = defaultdict(list)
        for chunk in chunks:
            grouped[chunk.article_id].append(chunk)
        if not grouped:
            return []

        details = self.database.article_details_by_ids(list(grouped))
        missing = sorted(set(grouped).difference(details))
        if missing:
            raise ArticleRankingIntegrityError(
                f"article metadata unavailable in SQLite: {', '.join(missing)}"
            )

        ordered_chunks: dict[str, list[HybridChunkResult]] = {}
        best_scores: dict[str, float] = {}
        top_three_means: dict[str, float] = {}
        for article_id, article_chunks in grouped.items():
            ordered = sorted(
                article_chunks,
                key=lambda chunk: (-chunk.hybrid_score, chunk.rank, chunk.chunk_id),
            )
            ordered_chunks[article_id] = ordered
            best_scores[article_id] = ordered[0].hybrid_score
            top = ordered[:3]
            top_three_means[article_id] = sum(chunk.hybrid_score for chunk in top) / len(top)

        maximum_best = max(best_scores.values()) or 1.0
        maximum_mean = max(top_three_means.values()) or 1.0
        query_terms = frozenset(_terms(query))
        config = self.settings.article_ranking
        candidates: list[RankedArticle] = []
        themes: dict[str, frozenset[str]] = {}
        for article_id, ordered in ordered_chunks.items():
            metadata = details[article_id]
            title = str(metadata["title"])
            abstract = str(metadata["abstract"]) if metadata["abstract"] else None
            retained = ordered[: config.top_chunks_per_article]
            searchable = " ".join([title, abstract or "", *(chunk.text for chunk in retained)])
            components = ArticleScoreComponents(
                best_fragment=best_scores[article_id] / maximum_best,
                top_three_mean=top_three_means[article_id] / maximum_mean,
                title_relevance=_term_relevance(query_terms, title),
                abstract_relevance=_term_relevance(query_terms, abstract),
                central_concept=_concept_relevance(concepts, searchable),
            )
            base_score = (
                config.best_fragment_weight * components.best_fragment
                + config.top_three_mean_weight * components.top_three_mean
                + config.title_relevance_weight * components.title_relevance
                + config.abstract_relevance_weight * components.abstract_relevance
                + config.central_concept_weight * components.central_concept
            )
            candidates.append(
                RankedArticle(
                    rank=1,
                    base_rank=1,
                    article_id=article_id,
                    doi=metadata["doi"],
                    title=title,
                    abstract=abstract,
                    authors=_authors(metadata["authors"]),
                    journal=metadata["journal"],
                    publication_year=metadata["publication_year"],
                    language=metadata["language"],
                    source=str(metadata["source"]),
                    validation_status=metadata["validation_status"],
                    base_score=min(max(base_score, 0.0), 1.0),
                    adjusted_score=min(max(base_score, 0.0), 1.0),
                    diversity_penalty=0.0,
                    diversity_reasons=[],
                    score_components=components,
                    matched_chunk_count=len(ordered),
                    best_chunk_id=ordered[0].chunk_id,
                    best_hybrid_rank=min(chunk.rank for chunk in ordered),
                    top_chunk_ids=[chunk.chunk_id for chunk in retained],
                    page_ranges=[
                        (
                            str(chunk.page_start)
                            if chunk.page_start == chunk.page_end
                            else f"{chunk.page_start}-{chunk.page_end}"
                        )
                        for chunk in retained
                    ],
                )
            )
            themes[article_id] = frozenset(_terms(searchable))

        candidates.sort(
            key=lambda article: (
                -article.base_score,
                article.best_hybrid_rank,
                article.article_id,
            )
        )
        return [
            _Candidate(
                article=article.model_copy(update={"base_rank": base_rank, "rank": base_rank}),
                theme_terms=themes[article.article_id],
            )
            for base_rank, article in enumerate(candidates, start=1)
        ]

    def _pair_redundancy(
        self,
        candidate: _Candidate,
        selected: _Candidate,
        mode: DiversityMode,
    ) -> tuple[float, list[str]]:
        theme_similarity = _jaccard(candidate.theme_terms, selected.theme_terms)
        same_year = (
            candidate.article.publication_year is not None
            and candidate.article.publication_year == selected.article.publication_year
        )
        candidate_journal = _normalized_text(candidate.article.journal or "").strip()
        selected_journal = _normalized_text(selected.article.journal or "").strip()
        same_journal = bool(candidate_journal and candidate_journal == selected_journal)

        values: list[float] = []
        reasons: list[str] = []
        if mode in {"theme", "balanced"}:
            thematic_value = theme_similarity
            if theme_similarity >= self.settings.article_ranking.near_duplicate_threshold:
                thematic_value = 1.0
                reasons.append(f"near-duplicate theme ({theme_similarity:.2f})")
            elif theme_similarity > 0:
                reasons.append(f"theme similarity {theme_similarity:.2f}")
            values.append(thematic_value)
        if mode in {"year", "balanced"}:
            values.append(float(same_year))
            if same_year:
                reasons.append(f"same year ({candidate.article.publication_year})")
        if mode in {"journal", "balanced"}:
            values.append(float(same_journal))
            if same_journal:
                reasons.append(f"same journal ({candidate.article.journal})")
        return (sum(values) / len(values) if values else 0.0), reasons

    def _diversify(
        self,
        candidates: Sequence[_Candidate],
        count: int,
        mode: DiversityMode,
    ) -> list[RankedArticle]:
        remaining = list(candidates)
        selected: list[_Candidate] = []
        output: list[RankedArticle] = []
        strength = self.settings.article_ranking.diversity_strength
        while remaining and len(output) < count:
            assessed: list[tuple[float, _Candidate, list[str]]] = []
            for candidate in remaining:
                redundancy = 0.0
                reasons: list[str] = []
                for prior in selected:
                    pair_redundancy, pair_reasons = self._pair_redundancy(candidate, prior, mode)
                    if pair_redundancy > redundancy:
                        redundancy = pair_redundancy
                        reasons = pair_reasons
                penalty = strength * redundancy
                adjusted = max(candidate.article.base_score - penalty, 0.0)
                assessed.append((adjusted, candidate, reasons))
            adjusted, winner, reasons = min(
                assessed,
                key=lambda item: (
                    -item[0],
                    item[1].article.base_rank,
                    item[1].article.article_id,
                ),
            )
            penalty = max(winner.article.base_score - adjusted, 0.0)
            ranked = winner.article.model_copy(
                update={
                    "rank": len(output) + 1,
                    "adjusted_score": adjusted,
                    "diversity_penalty": penalty,
                    "diversity_reasons": reasons if penalty > 0 else [],
                }
            )
            selected.append(winner)
            output.append(ranked)
            remaining.remove(winner)
        return output

    def rank_candidates(
        self,
        query: str,
        hybrid_results: Sequence[HybridChunkResult],
        *,
        article_count: int | None = None,
        diversity_mode: DiversityMode | None = None,
        central_concepts: Sequence[str] | None = None,
        exclude_article_ids: Sequence[str] | None = None,
    ) -> ArticleRankingResponse:
        started = perf_counter()
        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError("article ranking query cannot be empty")
        requested_count = (
            self.settings.retrieval.default_article_count
            if article_count is None
            else article_count
        )
        if not 1 <= requested_count <= 100:
            raise ValueError("article count must be between 1 and 100")
        mode = self._mode(diversity_mode)
        exclusions = sorted(
            {article_id.strip() for article_id in (exclude_article_ids or []) if article_id.strip()}
        )
        excluded = set(exclusions)
        eligible_chunks = [chunk for chunk in hybrid_results if chunk.article_id not in excluded]
        query_terms = list(dict.fromkeys(_terms(cleaned_query)))
        supplied_concepts = (
            self._derived_concepts(query_terms) if central_concepts is None else central_concepts
        )
        concepts = list(
            dict.fromkeys(concept.strip() for concept in supplied_concepts if concept.strip())
        )
        candidates = self._build_candidates(cleaned_query, eligible_chunks, concepts)
        articles = self._diversify(candidates, requested_count, mode)
        return ArticleRankingResponse(
            query=cleaned_query,
            query_terms=query_terms,
            central_concepts=concepts,
            diversity_mode=mode,
            requested_article_count=requested_count,
            available_article_count=len(candidates),
            selected_article_count=len(articles),
            excluded_article_ids=exclusions,
            hybrid_candidate_count=len(eligible_chunks),
            lexical_candidate_count=sum(
                chunk.lexical_rank is not None for chunk in eligible_chunks
            ),
            dense_candidate_count=sum(chunk.vector_rank is not None for chunk in eligible_chunks),
            rrf_unique_candidate_count=len(eligible_chunks),
            articles=articles,
            duration_seconds=perf_counter() - started,
        )

    def search(
        self,
        query: str,
        *,
        query_variants: Sequence[str] | None = None,
        article_count: int | None = None,
        diversity_mode: DiversityMode | None = None,
        central_concepts: Sequence[str] | None = None,
        exclude_article_ids: Sequence[str] | None = None,
        lexical_mode: QueryMode = "any",
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> ArticleRankingResponse:
        if self.hybrid is None:
            raise RuntimeError("hybrid search service is required for search")
        started = perf_counter()
        requested_count = (
            self.settings.retrieval.default_article_count
            if article_count is None
            else article_count
        )
        if not 1 <= requested_count <= 100:
            raise ValueError("article count must be between 1 and 100")
        hybrid_limit = max(
            self.settings.retrieval.hybrid_default_limit,
            requested_count * self.settings.article_ranking.top_chunks_per_article,
        )
        hybrid_limit = min(hybrid_limit, 1000)
        hybrid_response = self.hybrid.search(
            query,
            query_variants=query_variants,
            limit=hybrid_limit,
            candidate_limit=max(hybrid_limit, self.settings.retrieval.hybrid_candidate_limit),
            lexical_mode=lexical_mode,
            article_ids=article_ids,
            sections=sections,
        )
        response = self.rank_candidates(
            query,
            hybrid_response.results,
            article_count=requested_count,
            diversity_mode=diversity_mode,
            central_concepts=central_concepts,
            exclude_article_ids=exclude_article_ids,
        )
        return response.model_copy(
            update={
                "duration_seconds": perf_counter() - started,
                "query_variant_count": len(hybrid_response.queries),
                "lexical_candidate_count": hybrid_response.lexical_candidates,
                "dense_candidate_count": hybrid_response.vector_candidates,
                "rrf_unique_candidate_count": hybrid_response.unique_candidates,
                "vector_search_degraded": hybrid_response.vector_search_degraded,
            }
        )

    def close(self) -> None:
        if self.hybrid is not None:
            self.hybrid.close()
