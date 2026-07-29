"""Framework-agnostic application workflows shared by API and scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any, BinaryIO

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths, corpus_scope_label, settings_for_corpus
from app.database.sqlite import Database
from app.deep_research.query_variants import (
    QueryVariant,
    build_bilingual_variants,
    query_variant_weight,
    variant_matches_text,
)
from app.ingestion.embeddings import (
    EmbeddingBatchProcessor,
    EmbeddingRunReport,
    SentenceTransformerBackend,
    local_model_path,
)
from app.ingestion.pipeline import IngestionPipeline, IngestionReport
from app.llm.argo_client import ArgoClient, ArgoError
from app.llm.article_evidence import ArticleEvidenceExtractor, EvidencePassageSelector
from app.llm.final_synthesis import (
    HierarchicalSynthesisService,
    SynthesisExecutionResult,
)
from app.models.chatbot import (
    ChatbotResult,
    ChatbotSource,
    ChatEvidencePassage,
    ChatEvidenceRecord,
)
from app.models.synthesis import BibliographyEntry, SynthesisResult
from app.retrieval.article_ranking import (
    ArticleRankingResponse,
    ArticleRankingService,
    RankedArticle,
)
from app.retrieval.hybrid_search import HybridChunkResult, HybridSearchService
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.query_planning import (
    ArgoQueryPlanningService,
    QueryPlanningResult,
    deterministic_query_plan,
)
from app.retrieval.reranker import (
    MultilingualReranker,
    RerankerCandidate,
    local_reranker_model_path,
)
from app.retrieval.scientific_intent import (
    ScientificIntent,
    analyze_scientific_intent,
    score_scientific_text,
)
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService
from app.services.chatbot import (
    ChatbotNoSourcesError,
    chatbot_sources_from_evidence,
    contextualize_retrieval_query,
    conversation_context,
    merge_chatbot_candidates,
)
from app.updates.full_text import FullTextHarvestService
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicSearchReport
from app.updates.pilot_rag import (
    CiderAbstractRagResult,
    CiderAbstractRagService,
    CiderEvidenceRagService,
)
from app.updates.service import BibliographicDiscoveryService
from app.updates.vector_index import (
    BibliographicHybridResponse,
    BibliographicHybridResult,
    BibliographicHybridSearchService,
    BibliographicVectorIndex,
    expand_cider_query,
)

ProgressCallback = Callable[[int, int, str, str], None]
SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
BIBTEX_KEY = re.compile(r"[^A-Za-z0-9_:-]+")
_LOCAL_CHAT_RETRIEVAL_LOCK = threading.Lock()


def _serialized_chat_retrieval(
    operation: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Keep local model/index access exclusive without serializing ARGO generation."""

    with _LOCAL_CHAT_RETRIEVAL_LOCK:
        return operation(*args, **kwargs)


def apply_runtime_overrides(
    settings: Settings, overrides: Mapping[str, Mapping[str, Any]]
) -> Settings:
    """Validate session-only settings without modifying config.yaml."""

    payload = settings.model_dump(mode="python")
    for section, values in overrides.items():
        if section not in payload or not isinstance(payload[section], dict):
            raise ValueError(f"unknown configuration section: {section}")
        payload[section].update(values)
    return Settings.model_validate(payload)


def save_uploaded_pdf(
    settings: Settings,
    *,
    original_name: str,
    stream: BinaryIO,
) -> Path:
    """Atomically store one explicitly uploaded PDF under the configured data tree."""

    base_name = Path(original_name).name
    cleaned = SAFE_FILE_NAME.sub("_", base_name).strip(" .")
    if not cleaned.lower().endswith(".pdf"):
        raise ValueError("uploaded file must have a .pdf extension")
    upload_dir = settings.paths.pdf_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="upload-", suffix=".tmp", dir=upload_dir)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as destination:
            while block := stream.read(1024 * 1024):
                digest.update(block)
                destination.write(block)
        target = upload_dir / f"{digest.hexdigest()[:12]}-{cleaned}"
        Path(temporary_name).replace(target)
        return target
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def pdf_paths(folder: str | Path, *, recursive: bool) -> Iterable[Path]:
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"dossier PDF introuvable : {root}")
    iterator = root.rglob("*.pdf") if recursive else root.glob("*.pdf")
    yield from sorted(path for path in iterator if path.is_file())


def ingest_paths(
    settings: Settings,
    database: Database,
    paths: Sequence[Path],
    *,
    progress: ProgressCallback | None = None,
) -> list[IngestionReport]:
    pipeline = IngestionPipeline(settings, database)
    reports: list[IngestionReport] = []
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        if progress is not None:
            progress(index - 1, total, path.name, "ingestion")
        report = pipeline.ingest_file(path)
        reports.append(report)
        if progress is not None:
            progress(index, total, path.name, report.status)
    return reports


def index_pending_chunks(
    settings: Settings,
    database: Database,
    *,
    article_ids: Sequence[str] | None = None,
    retry_failed: bool = False,
) -> EmbeddingRunReport:
    index = QdrantLocalIndex(settings)
    backend = SentenceTransformerBackend(settings)
    try:
        return EmbeddingBatchProcessor(settings, database, backend).run(
            index,
            retry_failed=retry_failed,
            stop_on_error=True,
            close_backend=True,
            article_ids=article_ids,
        )
    finally:
        backend.close()
        index.close()


def rank_question(
    settings: Settings,
    database: Database,
    *,
    question: str,
    article_count: int,
    diversity_mode: str,
    variants: Sequence[str] | None = None,
    central_concepts: Sequence[str] | None = None,
    excluded_article_ids: Sequence[str] | None = None,
    article_ids: Sequence[str] | None = None,
) -> ArticleRankingResponse:
    backend = SentenceTransformerBackend(settings)
    ranking = ArticleRankingService(
        settings,
        database,
        HybridSearchService(
            settings,
            database,
            LexicalSearchService(settings, database),
            VectorSearchService(database, backend, QdrantLocalIndex(settings)),
        ),
    )
    try:
        return ranking.search(
            question,
            query_variants=variants,
            article_count=article_count,
            diversity_mode=diversity_mode,  # type: ignore[arg-type]
            central_concepts=central_concepts,
            exclude_article_ids=excluded_article_ids,
            article_ids=article_ids,
        )
    finally:
        ranking.close()


def discover_bibliographic_records(
    settings: Settings,
    *,
    query: str,
    limit_per_source: int,
) -> BibliographicSearchReport:
    return BibliographicDiscoveryService(settings).search(query, limit_per_source=limit_per_source)


def harvested_bibliographic_statistics(database: Database) -> dict[str, Any]:
    return BibliographicHarvestStore(database).statistics()


def bibliographic_database_filter_options(database: Database) -> dict[str, list[str]]:
    return BibliographicHarvestStore(database).browse_filter_options()


def browse_bibliographic_database(
    database: Database,
    *,
    query: str = "",
    statuses: list[str] | None = None,
    theme: str | None = None,
    source: str | None = None,
    has_abstract: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return BibliographicHarvestStore(database).browse_records(
        query=query,
        statuses=statuses,
        theme=theme,
        source=source,
        has_abstract=has_abstract,
        limit=limit,
        offset=offset,
    )


def search_harvested_abstracts(
    settings: Settings,
    database: Database,
    *,
    query: str,
    limit: int = 20,
) -> BibliographicHybridResponse:
    backend = SentenceTransformerBackend(settings)
    service = BibliographicHybridSearchService(
        settings,
        BibliographicHarvestStore(database),
        backend,
        BibliographicVectorIndex(settings),
    )
    try:
        return service.search(query, limit=limit)
    finally:
        service.close()


def _bibliographic_key(record: BibliographicHybridResult) -> str:
    if record.doi:
        return f"doi:{record.doi.casefold()}"
    return f"title:{' '.join(record.title.casefold().split())}"


def rerank_bibliographic_candidates(
    query: str,
    records: Sequence[BibliographicHybridResult],
    *,
    limit: int,
    intent_override: ScientificIntent | None = None,
) -> list[BibliographicHybridResult]:
    """Rerank article-level metadata by matrix + process + outcome proximity."""

    if not 1 <= limit <= 100:
        raise ValueError("bibliographic reranking limit must be between 1 and 100")
    intent = intent_override or analyze_scientific_intent(query)
    deduplicated: dict[str, BibliographicHybridResult] = {}
    for record in records:
        key = _bibliographic_key(record)
        current = deduplicated.get(key)
        if current is None:
            deduplicated[key] = record
            continue
        current_full_text = current.record_id.startswith(("common:", "private:"))
        candidate_full_text = record.record_id.startswith(("common:", "private:"))
        if candidate_full_text and not current_full_text:
            deduplicated[key] = record

    candidates = list(deduplicated.values())
    if not intent.is_structured:
        return [
            record.model_copy(update={"rank": rank})
            for rank, record in enumerate(candidates[:limit], start=1)
        ]

    assessed: list[
        tuple[
            float,
            int,
            int,
            BibliographicHybridResult,
        ]
    ] = []
    tier_priority = {"exact": 0, "near": 1, "distant": 2, "none": 3}
    for record in candidates:
        relevance = score_scientific_text(
            intent,
            title=record.title,
            text=record.abstract,
        )
        retrieval_signal = 1.0 / (1.0 + 0.08 * max(record.rank - 1, 0))
        combined = min(0.90 * relevance.score + 0.10 * retrieval_signal, 1.0)
        assessed.append(
            (
                combined,
                int(not relevance.causal_match),
                tier_priority[relevance.matrix_tier],
                record,
            )
        )
    assessed.sort(
        key=lambda item: (
            item[1],
            item[2],
            -item[0],
            item[3].rank,
            item[3].record_id,
        )
    )
    return [
        item[3].model_copy(update={"rank": rank, "score": item[0]})
        for rank, item in enumerate(assessed[:limit], start=1)
    ]


def search_common_corpus_abstracts(
    settings: Settings,
    *,
    query: str,
    limit: int = 15,
    search_queries: Sequence[str] = (),
    intent_override: ScientificIntent | None = None,
) -> list[BibliographicHybridResult]:
    """Search full-article metadata and abstract-only notices in the common corpus."""

    if not query.strip():
        raise ValueError("common corpus abstract query cannot be empty")
    if not 1 <= limit <= 100:
        raise ValueError("common corpus abstract limit must be between 1 and 100")
    scoped_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    queries = list(
        dict.fromkeys(" ".join(item.split()) for item in [query, *search_queries] if item.strip())
    )[:8]
    title_rows = []
    seen_title_ids: set[str] = set()
    for search_query in queries:
        for row in database.article_abstracts_by_title(search_query, limit=limit):
            article_id = str(row["id"])
            if article_id in seen_title_ids:
                continue
            seen_title_ids.add(article_id)
            title_rows.append(row)
    candidate_limit = min(max(limit * 10, 50), 500)
    maximum_query_length = scoped_settings.retrieval.lexical_max_query_characters
    lexical_service = LexicalSearchService(scoped_settings, database)
    lexical_by_article: dict[str, Any] = {}
    lexical_scores: dict[str, float] = {}
    for search_query in queries:
        lexical = lexical_service.search(
            expand_cider_query(search_query)[:maximum_query_length],
            limit=candidate_limit,
            mode="any",
        )
        for result in lexical.results:
            lexical_by_article.setdefault(result.article_id, result)
            lexical_scores[result.article_id] = lexical_scores.get(
                result.article_id,
                0.0,
            ) + 1.0 / (60.0 + result.rank)
    ordered_lexical_ids = sorted(
        lexical_by_article,
        key=lambda article_id: (
            -lexical_scores[article_id],
            lexical_by_article[article_id].rank,
            article_id,
        ),
    )
    maximum_lexical_score = max(lexical_scores.values(), default=1.0)
    ordered_ids = list(
        dict.fromkeys(
            [
                *(str(row["id"]) for row in title_rows),
                *ordered_lexical_ids,
            ]
        )
    )
    rows = {str(row["id"]): row for row in title_rows}
    rows.update(database.article_details_by_ids(ordered_ids))
    article_results: list[BibliographicHybridResult] = []
    for article_id in ordered_ids:
        row = rows.get(article_id)
        if row is None or not isinstance(row["abstract"], str) or not row["abstract"].strip():
            continue
        try:
            authors = json.loads(row["authors"] or "[]")
        except json.JSONDecodeError:
            authors = []
        lexical_hit = lexical_by_article.get(article_id)
        doi = str(row["doi"]) if row["doi"] else None
        article_results.append(
            BibliographicHybridResult(
                rank=len(article_results) + 1,
                record_id=f"common:{article_id}",
                title=str(row["title"]),
                abstract=str(row["abstract"]),
                authors=[str(author) for author in authors],
                journal=str(row["journal"]) if row["journal"] else None,
                publication_year=(
                    int(row["publication_year"]) if row["publication_year"] is not None else None
                ),
                doi=doi,
                url=f"https://doi.org/{doi}" if doi else None,
                sources=[str(row["source"])] if row["source"] else [],
                lexical_rank=lexical_hit.rank if lexical_hit is not None else 1,
                vector_rank=None,
                score=(
                    lexical_scores[article_id] / maximum_lexical_score
                    if lexical_hit is not None
                    else 1.0
                ),
            )
        )
        if len(article_results) >= min(max(limit * 2, 30), 100):
            break

    abstract_results: list[BibliographicHybridResult] = []
    abstract_store = BibliographicHarvestStore(database)
    if abstract_store.statistics()["abstracts"]:
        backend = SentenceTransformerBackend(scoped_settings)
        service = BibliographicHybridSearchService(
            scoped_settings,
            abstract_store,
            backend,
            BibliographicVectorIndex(scoped_settings),
        )
        try:
            # The final scientific reranker needs a wider recall set than the
            # UI result count; the service keeps its internal recall bounded.
            collected_abstracts: dict[str, BibliographicHybridResult] = {}
            for search_query in queries:
                response = service.search(
                    search_query,
                    limit=min(max(limit * 2, 30), 60),
                )
                for result in response.results:
                    converted = result.model_copy(
                        update={"record_id": f"common-abstract:{result.record_id}"}
                    )
                    key = _bibliographic_key(converted)
                    current = collected_abstracts.get(key)
                    if current is None or converted.score > current.score:
                        collected_abstracts[key] = converted
            abstract_results = list(collected_abstracts.values())
        finally:
            service.close()

    # A full article wins over an abstract-only notice for the same DOI. Distinct
    # DOI-less notices remain separate because a title match is not proof of identity.
    full_text_dois = {result.doi.casefold() for result in article_results if result.doi is not None}
    filtered_abstracts = [
        result
        for result in abstract_results
        if result.doi is None or result.doi.casefold() not in full_text_dois
    ]
    return rerank_bibliographic_candidates(
        query,
        [*article_results, *filtered_abstracts],
        limit=limit,
        intent_override=intent_override,
    )


def _lexical_full_text_ranking(
    settings: Settings,
    database: Database,
    *,
    query: str,
    variants: Sequence[Any],
    article_count: int,
    central_concepts: Sequence[str] | None,
    article_ids: Sequence[str] | None,
) -> ArticleRankingResponse:
    """Keep a deterministic fallback for tests or a missing local vector model."""

    candidate_limit = max(120, article_count * 12)
    lexical_service = LexicalSearchService(settings, database)
    fused: dict[tuple[str, int], dict[str, Any]] = {}
    for variant_index, variant in enumerate(variants):
        lexical = lexical_service.search(
            variant.text[: settings.retrieval.lexical_max_query_characters],
            limit=candidate_limit,
            mode="any",
            article_ids=article_ids,
        )
        for result in lexical.results:
            if not variant_matches_text(
                variant,
                result.text,
                title=result.article_title,
            ):
                continue
            key = (result.article_id, result.chunk_id)
            candidate = fused.setdefault(
                key,
                {
                    "result": result,
                    "source_ranks": {},
                    "source_contributions": {},
                    "matched_queries": [],
                },
            )
            source = f"lexical:{variant_index}"
            contribution = query_variant_weight(variant) / (settings.retrieval.rrf_k + result.rank)
            candidate["source_ranks"][source] = result.rank
            candidate["source_contributions"][source] = contribution
            if variant.text not in candidate["matched_queries"]:
                candidate["matched_queries"].append(variant.text)
    ordered = sorted(
        fused.values(),
        key=lambda candidate: (
            -sum(candidate["source_contributions"].values()),
            candidate["result"].rank,
            candidate["result"].article_id,
            candidate["result"].chunk_id,
        ),
    )
    chunks: list[HybridChunkResult] = []
    for rank, candidate in enumerate(ordered, start=1):
        result = candidate["result"]
        contributions = candidate["source_contributions"]
        chunks.append(
            HybridChunkResult(
                rank=rank,
                chunk_id=result.chunk_id,
                article_id=result.article_id,
                article_title=result.article_title,
                publication_year=result.publication_year,
                section=result.section,
                page_start=result.page_start,
                page_end=result.page_end,
                text=result.text,
                hybrid_score=sum(contributions.values()),
                lexical_rank=min(candidate["source_ranks"].values()),
                lexical_score=result.relevance_score,
                vector_rank=None,
                vector_score=None,
                source_ranks=candidate["source_ranks"],
                source_contributions=contributions,
                matched_queries=candidate["matched_queries"],
                scope=CorpusScope.COMMON,
            )
        )
    return ArticleRankingService(settings, database).rank_candidates(
        query,
        chunks,
        article_count=article_count,
        diversity_mode="none",
        central_concepts=central_concepts,
    )


def search_common_corpus_full_text_evidence(
    settings: Settings,
    *,
    query: str,
    article_count: int = 6,
    article_ids: Sequence[str] | None = None,
    search_queries: Sequence[str] = (),
    intent_override: ScientificIntent | None = None,
) -> list[ChatEvidenceRecord]:
    """Hybrid-search full articles, causally rerank them, then hydrate passages."""

    if not query.strip():
        raise ValueError("common corpus full-text query cannot be empty")
    if not 1 <= article_count <= 10:
        raise ValueError("chat full-text article count must be between 1 and 10")
    scoped_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    intent = intent_override or analyze_scientific_intent(query)
    fallback_variants = build_bilingual_variants(query, max_variants=3)
    variants = fallback_variants[:1]
    known_variant_texts = {variant.text.casefold() for variant in variants}
    maximum_variant_count = scoped_settings.retrieval.hybrid_max_query_variants
    for planned_query in search_queries:
        cleaned_planned_query = " ".join(planned_query.split())[:2000]
        if (
            len(variants) >= maximum_variant_count
            or len(cleaned_planned_query) < 2
            or cleaned_planned_query.casefold() in known_variant_texts
        ):
            continue
        variants.append(
            QueryVariant(
                text=cleaned_planned_query,
                language="mixed",
                derivation="argo_plan",
                matched_terms=["argo_query_plan"],
                anchor_terms=[],
                scope_tier="strict",
            )
        )
        known_variant_texts.add(cleaned_planned_query.casefold())
    for fallback_variant in fallback_variants[1:]:
        if len(variants) >= maximum_variant_count:
            break
        if fallback_variant.text.casefold() in known_variant_texts:
            continue
        variants.append(fallback_variant)
        known_variant_texts.add(fallback_variant.text.casefold())
    candidate_article_count = min(max(article_count * 5, 40), 100)
    if local_model_path(scoped_settings).is_dir():
        backend = SentenceTransformerBackend(scoped_settings)
        ranking_service = ArticleRankingService(
            scoped_settings,
            database,
            HybridSearchService(
                scoped_settings,
                database,
                LexicalSearchService(scoped_settings, database),
                VectorSearchService(
                    database,
                    backend,
                    QdrantLocalIndex(scoped_settings),
                ),
            ),
        )
        try:
            ranking = ranking_service.search(
                query,
                query_variants=[variant.text for variant in variants],
                article_count=candidate_article_count,
                diversity_mode="none",
                central_concepts=intent.central_concepts() or None,
                article_ids=article_ids,
            )
        finally:
            ranking_service.close()
    else:
        ranking = _lexical_full_text_ranking(
            scoped_settings,
            database,
            query=query,
            variants=variants,
            article_count=candidate_article_count,
            central_concepts=intent.central_concepts() or None,
            article_ids=article_ids,
        )

    chunk_rows = database.chunk_details_by_ids(
        [chunk_id for article in ranking.articles for chunk_id in article.top_chunk_ids]
    )
    relevance_by_article = {}
    reranker_candidates: list[RerankerCandidate] = []
    for article in ranking.articles:
        passage_text = "\n".join(
            str(chunk_rows[chunk_id]["text"])
            for chunk_id in article.top_chunk_ids
            if chunk_id in chunk_rows
        )
        # Prefer article-level metadata for causal reranking. Top chunks are a
        # fallback for records without an abstract because references and
        # background passages can contain misleading matrix/process terms.
        searchable = article.abstract or passage_text
        relevance = score_scientific_text(
            intent,
            title=article.title,
            text=searchable,
        )
        relevance_by_article[article.article_id] = relevance
        structured_score = min(
            0.85 * relevance.score + 0.15 * article.adjusted_score,
            1.0,
        )
        reranker_candidates.append(
            RerankerCandidate(
                candidate_id=article.article_id,
                text=f"{article.title}\n{searchable[:12000]}",
                original_score=structured_score,
            )
        )

    local_model_available = local_reranker_model_path(scoped_settings).is_dir()
    reranker = MultilingualReranker.from_settings(scoped_settings)
    if reranker.enabled and not local_model_available:
        raise RuntimeError("configured local reranker model is unavailable")
    try:
        reranked = reranker.rerank(
            intent.selector_query(),
            reranker_candidates,
            top_k=len(reranker_candidates),
        )
    finally:
        reranker.close()
    reranker_position = {
        result.candidate_id: position for position, result in enumerate(reranked, start=1)
    }
    articles_by_id = {article.article_id: article for article in ranking.articles}
    assessed_articles: list[tuple[float, RankedArticle]] = []
    for article in ranking.articles:
        relevance = relevance_by_article[article.article_id]
        cross_encoder_signal = 1.0 / reranker_position.get(
            article.article_id,
            len(reranker_position) + 1,
        )
        score = min(
            0.80 * relevance.score + 0.15 * article.adjusted_score + 0.05 * cross_encoder_signal,
            1.0,
        )
        assessed_articles.append(
            (
                score,
                articles_by_id[article.article_id].model_copy(update={"adjusted_score": score}),
            )
        )
    tier_priority = {"exact": 0, "near": 1, "distant": 2, "none": 3}
    assessed_articles.sort(
        key=lambda item: (
            int(not relevance_by_article[item[1].article_id].causal_match),
            -item[0],
            tier_priority[relevance_by_article[item[1].article_id].matrix_tier],
            item[1].base_rank,
            item[1].article_id,
        )
    )

    selected_articles: list[RankedArticle] = []
    selected_ids: set[str] = set()
    for facet in intent.facets:
        candidate = next(
            (
                article
                for _score, article in assessed_articles
                if article.article_id not in selected_ids
                and facet.key in relevance_by_article[article.article_id].matched_facets
            ),
            None,
        )
        if candidate is not None:
            selected_articles.append(candidate)
            selected_ids.add(candidate.article_id)
    for _score, article in assessed_articles:
        if len(selected_articles) >= article_count:
            break
        if article.article_id in selected_ids:
            continue
        selected_articles.append(article)
        selected_ids.add(article.article_id)

    selector = EvidencePassageSelector(scoped_settings, database)
    records: list[ChatEvidenceRecord] = []
    passage_count = min(
        scoped_settings.evidence.min_passages_per_article + 1,
        scoped_settings.evidence.passages_per_article,
    )
    selector_query = intent.selector_query()
    for article in selected_articles[:article_count]:
        passages = selector.select(
            query=selector_query,
            article_id=article.article_id,
            ranked_chunk_ids=article.top_chunk_ids,
            passage_count=passage_count,
        )
        if not passages:
            continue
        record_id = f"common:{article.article_id}"
        records.append(
            ChatEvidenceRecord(
                record_id=record_id,
                origin="local_rag",
                evidence_level="full_text",
                scope=CorpusScope.COMMON,
                article_id=article.article_id,
                title=article.title,
                authors=article.authors,
                doi=article.doi,
                journal=article.journal,
                publication_year=article.publication_year,
                providers=[article.source],
                url=f"https://doi.org/{article.doi}" if article.doi else None,
                score=article.adjusted_score,
                matched_facets=relevance_by_article[article.article_id].matched_facets,
                matrix_tier=relevance_by_article[article.article_id].matrix_tier,
                passages=[
                    ChatEvidencePassage(
                        evidence_id=f"{record_id}:chunk:{passage.chunk_id}",
                        chunk_id=passage.chunk_id,
                        section=passage.section,
                        page_start=passage.page_start,
                        page_end=passage.page_end,
                        text=passage.text,
                    )
                    for passage in passages
                ],
            )
        )
    return records


def abstract_candidates_to_chat_evidence(
    records: Sequence[BibliographicHybridResult],
) -> list[ChatEvidenceRecord]:
    """Convert abstract-only candidates without pretending that they are full text."""

    converted: list[ChatEvidenceRecord] = []
    for record in records:
        text = record.abstract.strip()
        if not text:
            continue
        origin = "external_api" if record.record_id.startswith("external:") else "local_rag"
        scope = (
            None
            if origin == "external_api"
            else (
                CorpusScope.PRIVATE
                if record.record_id.startswith("private:")
                else CorpusScope.COMMON
            )
        )
        converted.append(
            ChatEvidenceRecord(
                record_id=record.record_id,
                origin=origin,
                evidence_level="abstract",
                scope=scope,
                title=record.title,
                authors=record.authors,
                doi=record.doi,
                journal=record.journal,
                publication_year=record.publication_year,
                providers=record.sources,
                url=record.url,
                score=record.score,
                passages=[
                    ChatEvidencePassage(
                        evidence_id=f"{record.record_id}:abstract",
                        text=text,
                        section="abstract",
                    )
                ],
            )
        )
    return converted


def merge_chat_evidence(
    full_text_records: Sequence[ChatEvidenceRecord],
    abstract_records: Sequence[ChatEvidenceRecord],
    *,
    query: str | None = None,
    limit: int = 12,
    intent_override: ScientificIntent | None = None,
) -> list[ChatEvidenceRecord]:
    """Select causal, facet-covering evidence regardless of storage level."""

    if not 1 <= limit <= 20:
        raise ValueError("chat evidence limit must be between 1 and 20")
    records = [*full_text_records, *abstract_records]
    if query is None:
        chosen: list[ChatEvidenceRecord] = []
        seen: set[str] = set()
        for record in records:
            key = (
                f"doi:{record.doi.casefold()}"
                if record.doi
                else f"title:{' '.join(record.title.casefold().split())}"
            )
            if key in seen:
                continue
            chosen.append(record)
            seen.add(key)
            if len(chosen) >= limit:
                break
        return chosen

    intent = intent_override or analyze_scientific_intent(query)
    assessed: list[tuple[float, ChatEvidenceRecord]] = []
    for record in records:
        relevance = score_scientific_text(
            intent,
            title=record.title,
            text="\n".join(passage.text for passage in record.passages),
        )
        retrieval_signal = min(max(record.score, 0.0), 1.0)
        level_bonus = 0.03 if record.evidence_level == "full_text" and relevance.score >= 0.3 else 0
        combined = min(0.90 * relevance.score + 0.07 * retrieval_signal + level_bonus, 1.0)
        assessed.append(
            (
                combined,
                record.model_copy(
                    update={
                        "score": combined,
                        "matched_facets": relevance.matched_facets,
                        "matrix_tier": relevance.matrix_tier,
                    }
                ),
            )
        )

    # Prefer full text only when it is at least as relevant as its matching
    # abstract. An irrelevant PDF can no longer evict a direct abstract.
    deduplicated: dict[str, tuple[float, ChatEvidenceRecord]] = {}
    for score, record in assessed:
        key = (
            f"doi:{record.doi.casefold()}"
            if record.doi
            else f"title:{' '.join(record.title.casefold().split())}"
        )
        current = deduplicated.get(key)
        if current is None:
            deduplicated[key] = (score, record)
            continue
        current_score, current_record = current
        candidate_preferred = score > current_score + 1e-9 or (
            abs(score - current_score) <= 1e-9
            and record.evidence_level == "full_text"
            and current_record.evidence_level != "full_text"
        )
        if candidate_preferred:
            deduplicated[key] = (score, record)

    tier_priority = {"exact": 0, "near": 1, "distant": 2, "none": 3}
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            -item[0],
            tier_priority[item[1].matrix_tier],
            -len(item[1].matched_facets),
            int(item[1].evidence_level != "full_text"),
            item[1].record_id,
        ),
    )
    chosen = []
    chosen_ids: set[str] = set()
    for facet in intent.facets:
        candidate = next(
            (
                record
                for _score, record in ordered
                if record.record_id not in chosen_ids and facet.key in record.matched_facets
            ),
            None,
        )
        if candidate is not None:
            chosen.append(candidate)
            chosen_ids.add(candidate.record_id)
    for _score, record in ordered:
        if len(chosen) >= limit:
            break
        if record.record_id in chosen_ids:
            continue
        chosen.append(record)
        chosen_ids.add(record.record_id)
    return chosen[:limit]


def acquire_common_full_text_for_chat(
    settings: Settings,
    candidates: Sequence[BibliographicHybridResult],
    *,
    max_downloads: int = 2,
) -> tuple[list[str], list[str]]:
    """Acquire selected abstract notices and permanently index them in the common corpus."""

    selected_record_ids = list(
        dict.fromkeys(
            record.record_id.removeprefix("common-abstract:")
            for record in candidates
            if record.record_id.startswith("common-abstract:") and record.doi
        )
    )[:max_downloads]
    if not selected_record_ids:
        return [], []
    scoped_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    _audit, harvest = FullTextHarvestService(scoped_settings, database).run(
        include_slow_fallbacks=False,
        max_downloads=max_downloads,
        record_ids=selected_record_ids,
    )
    warnings = [
        (f"Le texte intégral n'a pas pu être acquis pour {error['doi']} ({error['error_type']}).")
        for error in harvest.errors
    ]
    if harvest.article_ids:
        try:
            index_pending_chunks(
                scoped_settings,
                database,
                article_ids=harvest.article_ids,
                retry_failed=True,
            )
        except Exception as exc:
            warnings.append(
                "Les articles acquis ont été conservés dans le corpus commun, mais leur "
                f"indexation vectorielle est différée ({type(exc).__name__})."
            )
    return harvest.article_ids, warnings


def chat_evidence_from_previous_sources(
    settings: Settings,
    *,
    query: str,
    sources: Sequence[ChatbotSource],
) -> list[ChatEvidenceRecord]:
    """Rehydrate persisted full-text chunks and keep legacy abstract cards usable."""

    records: list[ChatEvidenceRecord] = []
    databases: dict[CorpusScope, Database] = {}
    for source in sources[:10]:
        scope = source.scope or CorpusScope.COMMON
        if (
            source.evidence_level == "full_text"
            and source.article_id
            and source.chunk_ids
            and source.scope is not None
        ):
            database = databases.setdefault(
                scope,
                Database(corpus_paths(settings, scope).database_path),
            )
            scoped_settings = settings_for_corpus(settings, scope)
            passages = EvidencePassageSelector(scoped_settings, database).select(
                query=query,
                article_id=source.article_id,
                ranked_chunk_ids=source.chunk_ids,
                passage_count=scoped_settings.evidence.min_passages_per_article,
            )
            if passages:
                records.append(
                    ChatEvidenceRecord(
                        record_id=source.record_id,
                        origin=source.origin,
                        evidence_level="full_text",
                        scope=scope,
                        article_id=source.article_id,
                        title=source.title,
                        authors=source.authors,
                        doi=source.doi,
                        journal=source.journal,
                        publication_year=source.publication_year,
                        providers=source.providers,
                        url=source.url,
                        passages=[
                            ChatEvidencePassage(
                                evidence_id=(f"{source.record_id}:chunk:{passage.chunk_id}"),
                                chunk_id=passage.chunk_id,
                                section=passage.section,
                                page_start=passage.page_start,
                                page_end=passage.page_end,
                                text=passage.text,
                            )
                            for passage in passages
                        ],
                    )
                )
                continue
        if source.snippet.strip():
            records.append(
                ChatEvidenceRecord(
                    record_id=source.record_id,
                    origin=source.origin,
                    evidence_level="abstract",
                    scope=source.scope,
                    title=source.title,
                    authors=source.authors,
                    doi=source.doi,
                    journal=source.journal,
                    publication_year=source.publication_year,
                    providers=source.providers,
                    url=source.url,
                    passages=[
                        ChatEvidencePassage(
                            evidence_id=f"{source.record_id}:abstract",
                            section="abstract",
                            text=source.snippet,
                        )
                    ],
                )
            )
    return records


def answer_from_harvested_abstracts(
    settings: Settings,
    *,
    question: str,
    search_response: BibliographicHybridResponse,
) -> CiderAbstractRagResult:
    with ArgoClient(settings) as llm:
        return CiderAbstractRagService(llm).answer(question, search_response.results)


def answer_chatbot(
    settings: Settings,
    database: Database,
    *,
    message: str,
    history: Sequence[Mapping[str, str]],
    use_external_sources: bool,
    interaction_mode: str = "research",
    previous_sources: Sequence[ChatbotSource] = (),
    on_argo_reserved: Callable[[], None] | None = None,
    on_argo_response: Callable[[], None] | None = None,
) -> ChatbotResult:
    """Answer with local full-text passages, abstract fallback and bounded enrichment."""

    started = perf_counter()
    context = conversation_context(history)
    retrieval_query = contextualize_retrieval_query(message, context)
    reused_evidence = (
        chat_evidence_from_previous_sources(
            settings,
            query=retrieval_query,
            sources=previous_sources,
        )
        if interaction_mode == "conversation"
        else []
    )
    if reused_evidence:
        with ArgoClient(settings) as llm:
            answer = CiderEvidenceRagService(llm).answer(
                message,
                reused_evidence,
                conversation_history=context,
                on_argo_reserved=on_argo_reserved,
                on_argo_response=on_argo_response,
            )
        sources = chatbot_sources_from_evidence(
            reused_evidence,
            answer.cited_evidence_ids,
        )
        return ChatbotResult(
            message=" ".join(message.split()),
            retrieval_query=retrieval_query,
            answer_markdown=answer.answer_markdown,
            sources=sources,
            warnings=[],
            model=answer.model,
            local_result_count=sum(record.origin == "local_rag" for record in reused_evidence),
            external_result_count=sum(
                record.origin == "external_api" for record in reused_evidence
            ),
            external_enrichment_used=any(source.origin == "external_api" for source in sources),
            prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
            duration_seconds=perf_counter() - started,
            interaction_mode="conversation",
            reused_previous_sources=True,
        )
    warnings: list[str] = []
    planning: QueryPlanningResult
    try:
        with ArgoClient(settings) as planning_client:
            planning = ArgoQueryPlanningService(planning_client).plan(
                retrieval_query,
                conversation_history=context,
                on_argo_reserved=on_argo_reserved,
            )
    except ArgoError:
        raise
    except Exception as exc:
        planning = deterministic_query_plan(retrieval_query)
        warnings.append(
            "La compréhension adaptative de la requête est indisponible "
            f"({type(exc).__name__}); utilisation du planificateur local de secours."
        )
    intent = planning.plan.scientific_intent(retrieval_query)
    search_queries = planning.plan.retrieval_queries

    local_results = _serialized_chat_retrieval(
        search_common_corpus_abstracts,
        settings,
        query=retrieval_query,
        limit=15,
        search_queries=search_queries,
        intent_override=intent,
    )
    if not local_results:
        local_results = _serialized_chat_retrieval(
            search_harvested_abstracts,
            settings,
            database,
            query=retrieval_query,
            limit=15,
        ).results
    if use_external_sources and settings.full_text.enabled:
        _acquired_article_ids, acquisition_warnings = _serialized_chat_retrieval(
            acquire_common_full_text_for_chat,
            settings,
            local_results,
            max_downloads=2,
        )
        warnings.extend(acquisition_warnings)

    try:
        full_text_records = _serialized_chat_retrieval(
            search_common_corpus_full_text_evidence,
            settings,
            query=retrieval_query,
            article_count=8,
            search_queries=search_queries,
            intent_override=intent,
        )
    except Exception as exc:
        full_text_records = []
        warnings.append(
            "La recherche dans les textes intégraux est indisponible pour cette réponse "
            f"({type(exc).__name__}); repli sur les abstracts."
        )

    external_report: BibliographicSearchReport | None = None
    if use_external_sources:
        try:
            external_report = discover_bibliographic_records(
                settings,
                query=retrieval_query,
                limit_per_source=4,
            )
        except Exception as exc:
            warnings.append(
                "L'enrichissement bibliographique externe est indisponible pour cette réponse "
                f"({type(exc).__name__})."
            )

    external_records = external_report.records if external_report else []
    candidates, external_count = merge_chatbot_candidates(
        local_results,
        external_records,
        limit=16,
    )
    abstract_evidence = abstract_candidates_to_chat_evidence(candidates)
    evidence = merge_chat_evidence(
        full_text_records,
        abstract_evidence,
        query=retrieval_query,
        intent_override=intent,
    )
    if not evidence:
        raise ChatbotNoSourcesError(
            "aucune preuve qualifiée n'est disponible pour répondre à cette question"
        )
    if external_report:
        warnings.extend(
            f"La source {error.source} n'a pas répondu à cette requête."
            for error in external_report.errors
        )

    with ArgoClient(settings) as llm:
        rag = CiderEvidenceRagService(llm)
        if planning.plan.requires_faceted_answer:
            answer = rag.answer_faceted(
                message,
                evidence,
                facets=intent.facets,
                conversation_history=context,
                on_argo_reserved=on_argo_reserved,
                on_argo_response=on_argo_response,
            )
        else:
            answer = rag.answer(
                message,
                evidence,
                conversation_history=context,
                on_argo_reserved=on_argo_reserved,
                on_argo_response=on_argo_response,
            )
    sources = chatbot_sources_from_evidence(evidence, answer.cited_evidence_ids)
    return ChatbotResult(
        message=" ".join(message.split()),
        retrieval_query=retrieval_query,
        answer_markdown=answer.answer_markdown,
        sources=sources,
        warnings=warnings,
        model=answer.model,
        local_result_count=sum(record.origin == "local_rag" for record in evidence),
        external_result_count=external_count,
        external_enrichment_used=any(source.origin == "external_api" for source in sources),
        prompt_tokens=planning.prompt_tokens + answer.prompt_tokens,
        completion_tokens=planning.completion_tokens + answer.completion_tokens,
        duration_seconds=perf_counter() - started,
        interaction_mode="research",
        reused_previous_sources=False,
        facet_drafts=getattr(answer, "facet_drafts", []),
    )


def extract_ranked_evidence(
    settings: Settings,
    database: Database,
    *,
    question: str,
    articles: Sequence[RankedArticle],
    passage_count: int,
    variants: Sequence[str] | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[str, list[str], list[dict[str, str]]]:
    if not articles:
        raise ValueError("at least one ranked article is required")
    query_id = str(uuid.uuid4())
    article_ids = [article.article_id for article in articles]
    database.create_query(
        query_id=query_id,
        original_query=question.strip(),
        expanded_queries=variants or [],
        selected_article_ids=article_ids,
        model_version=settings.argo.model,
    )
    selector = EvidencePassageSelector(settings, database)
    completed: list[str] = []
    errors: list[dict[str, str]] = []
    total = len(articles)
    with ArgoClient(settings) as llm:
        extractor = ArticleEvidenceExtractor(settings, database, llm)
        for index, article in enumerate(articles, start=1):
            if progress is not None:
                progress(index - 1, total, article.title, "extraction")
            try:
                passages = selector.select(
                    query=question,
                    article_id=article.article_id,
                    ranked_chunk_ids=article.top_chunk_ids,
                    passage_count=passage_count,
                )
                extractor.extract(
                    query=question,
                    article_id=article.article_id,
                    passages=passages,
                    query_id=query_id,
                    resume=True,
                )
                completed.append(article.article_id)
                state = "completed"
            except Exception as exc:
                state = "failed"
                errors.append(
                    {
                        "article_id": article.article_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:1000],
                    }
                )
            if progress is not None:
                progress(index, total, article.title, state)
    return query_id, completed, errors


def synthesize_query(
    settings: Settings,
    database: Database,
    *,
    query_id: str,
    resume: bool = True,
) -> SynthesisExecutionResult:
    with ArgoClient(settings) as llm:
        return HierarchicalSynthesisService(settings, database, llm).synthesize(
            query_id=query_id, resume=resume
        )


class _CompletedSynthesisReader:
    def chat(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("a completed synthesis must not call the generation provider")


def load_completed_synthesis(
    settings: Settings,
    database: Database,
    *,
    query_id: str,
) -> SynthesisResult | None:
    if database.load_final_synthesis(query_id) is None:
        return None
    execution = HierarchicalSynthesisService(
        settings,
        database,
        _CompletedSynthesisReader(),  # type: ignore[arg-type]
    ).synthesize(query_id=query_id, resume=True)
    return execution.result


def delete_article(settings: Settings, database: Database, *, article_id: str) -> dict[str, int]:
    chunk_ids = database.article_chunk_ids(article_id)
    index = QdrantLocalIndex(settings)
    try:
        deleted_points = index.delete_points(chunk_ids)
    finally:
        index.close()
    deleted_queries = database.delete_article(article_id)
    return {
        "deleted_chunks": len(chunk_ids),
        "deleted_vector_points": deleted_points,
        "deleted_queries": deleted_queries,
    }


def reindex_article(
    settings: Settings, database: Database, *, article_id: str
) -> EmbeddingRunReport:
    chunk_ids = database.article_chunk_ids(article_id)
    if not chunk_ids:
        raise ValueError("article has no chunks to reindex")
    index = QdrantLocalIndex(settings)
    try:
        index.delete_points(chunk_ids)
    finally:
        index.close()
    database.reset_article_for_reindex(article_id)
    return index_pending_chunks(settings, database, article_ids=[article_id], retry_failed=True)


def _bibtex_value(value: str) -> str:
    return value.replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}")


def bibliography_to_bibtex(entries: Sequence[BibliographyEntry]) -> str:
    records: list[str] = []
    for entry in entries:
        key = BIBTEX_KEY.sub("-", entry.article_id).strip("-") or "article"
        fields = [f"  title = {{{_bibtex_value(entry.title)}}}"]
        if entry.authors:
            fields.append(
                "  author = {"
                + " and ".join(_bibtex_value(author) for author in entry.authors)
                + "}"
            )
        if entry.journal:
            fields.append(f"  journal = {{{_bibtex_value(entry.journal)}}}")
        if entry.publication_year:
            fields.append(f"  year = {{{entry.publication_year}}}")
        if entry.doi:
            fields.append(f"  doi = {{{_bibtex_value(entry.doi)}}}")
        fields.append(f"  ciderscholar_scope = {{{entry.scope.value}}}")
        fields.append(f"  note = {{{corpus_scope_label(entry.scope)}}}")
        records.append(f"@article{{{key},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(records) + ("\n" if records else "")


def synthesis_to_json(result: SynthesisResult) -> str:
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
