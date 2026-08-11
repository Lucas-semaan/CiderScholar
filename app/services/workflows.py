"""Framework-agnostic application workflows shared by API and scripts."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, BinaryIO, Literal

from app.chat_effort import AnswerEffort, answer_effort_budget
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
from app.ingestion.pdf_extractor import PdfExtractor
from app.ingestion.pipeline import IngestionPipeline, IngestionReport
from app.llm.argo_client import (
    ArgoAuthenticationError,
    ArgoAuthorizationError,
    ArgoClient,
    ArgoError,
    ArgoGenerationError,
    ArgoProtocolError,
    ArgoQuotaError,
    ArgoScientificValidationError,
)
from app.llm.article_evidence import ArticleEvidenceExtractor, EvidencePassageSelector
from app.llm.figure_analysis import (
    FigureAnalysisUnavailable,
    OllamaFigureAnalysisService,
    attach_figure_evidence,
    figure_references_from_chat_records,
)
from app.llm.final_synthesis import (
    HierarchicalSynthesisService,
    SynthesisExecutionResult,
)
from app.llm.response_language import question_language
from app.llm.response_style import ResponseStyle, detect_response_style
from app.memory import MemoryGuard, MemorySnapshot
from app.models.chatbot import (
    ChatbotResult,
    ChatbotRetrievalTrace,
    ChatbotSource,
    ChatbotTiming,
    ChatEvidencePassage,
    ChatEvidenceRecord,
)
from app.models.synthesis import BibliographyEntry, SynthesisResult
from app.retrieval.article_ranking import (
    ArticleRankingResponse,
    ArticleRankingService,
    RankedArticle,
)
from app.retrieval.axis_coverage import (
    canonical_article_key,
    merge_axis_rankings,
    select_with_axis_coverage,
)
from app.retrieval.coverage_assessment import (
    ArgoEvidenceCoverageAssessor,
    CoverageAssessmentResult,
)
from app.retrieval.hybrid_search import HybridChunkResult, HybridSearchService
from app.retrieval.index_manifest import (
    assert_index_generation_mutable,
    prepare_index_generation_mutation,
    resume_index_generation,
    write_ready_index_generation_manifest,
)
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.query_planning import (
    ArgoQueryPlanningService,
    QueryPlanningResult,
    ResearchAxis,
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
from app.retrieval.semantic_filter import (
    ArgoSemanticEvidenceFilter,
    SemanticFilterResult,
)
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService
from app.services.chatbot import (
    chatbot_sources_from_evidence,
    contextualize_retrieval_query,
    conversation_context,
    merge_chatbot_candidates,
)
from app.updates.full_text import FullTextHarvestService
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicSearchReport, normalize_doi
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
ChatbotProgressStage = Literal[
    "planning",
    "search",
    "enrichment",
    "reranking",
    "evidence_selection",
    "coverage",
    "figure_analysis",
    "generation",
]
ChatbotProgressCallback = Callable[[ChatbotProgressStage], None]
SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
BIBTEX_KEY = re.compile(r"[^A-Za-z0-9_:-]+")
_LOCAL_CHAT_RETRIEVAL_LOCK = threading.Lock()


class _ChatTimingCollector:
    """Accumulate content-free stage resource totals without affecting execution."""

    def __init__(self, settings: Settings) -> None:
        self._memory = MemoryGuard(settings.memory)
        self._values: dict[str, dict[str, Any]] = {}

    def snapshot(self) -> MemorySnapshot | None:
        try:
            return self._memory.snapshot()
        except Exception:
            return None

    def add(
        self,
        stage: str,
        duration_seconds: float,
        *,
        before: MemorySnapshot | None = None,
    ) -> None:
        after = self.snapshot()
        value = self._values.setdefault(
            stage,
            {
                "duration_seconds": 0.0,
                "count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "before": before or after,
                "after": after,
            },
        )
        value["duration_seconds"] += max(0.0, duration_seconds)
        value["count"] += 1
        if value["before"] is None:
            value["before"] = before or after
        value["after"] = after or value["after"]

    def add_tokens(self, stage: str, *, prompt_tokens: int, completion_tokens: int) -> None:
        value = self._values.get(stage)
        if value is None:
            return
        value["prompt_tokens"] += max(0, prompt_tokens)
        value["completion_tokens"] += max(0, completion_tokens)

    def models(self) -> list[ChatbotTiming]:
        models: list[ChatbotTiming] = []
        for stage, value in self._values.items():
            before = value["before"]
            after = value["after"]
            models.append(
                ChatbotTiming(
                    stage=stage,
                    duration_seconds=value["duration_seconds"],
                    count=value["count"],
                    prompt_tokens=value["prompt_tokens"],
                    completion_tokens=value["completion_tokens"],
                    process_rss_before_gb=(None if before is None else before.process_rss_gb),
                    process_rss_after_gb=(None if after is None else after.process_rss_gb),
                    system_used_before_gb=(None if before is None else before.system_used_gb),
                    system_used_after_gb=(None if after is None else after.system_used_gb),
                    system_available_before_gb=(
                        None if before is None else before.system_available_gb
                    ),
                    system_available_after_gb=(
                        None if after is None else after.system_available_gb
                    ),
                )
            )
        return models


class _ChatRetrievalTraceCollector:
    """Aggregate bounded candidate-flow counters without retaining source identities."""

    _COUNT_FIELDS = (
        "query_variant_count",
        "lexical_candidate_count",
        "dense_candidate_count",
        "rrf_unique_candidate_count",
        "fused_candidate_count",
        "pre_rerank_candidate_count",
        "post_rerank_candidate_count",
        "selected_article_count",
        "selected_passage_count",
    )

    def __init__(self) -> None:
        self._values: dict[str, ChatbotRetrievalTrace] = {}

    def add(self, stage: str, **measurements: Any) -> None:
        current = self._values.get(stage)
        if current is None:
            self._values[stage] = ChatbotRetrievalTrace(stage=stage, **measurements)
            return
        update = {
            field: getattr(current, field) + int(measurements.get(field, 0))
            for field in self._COUNT_FIELDS
        }
        rejections = dict(current.rejection_counts)
        for reason, count in measurements.get("rejection_counts", {}).items():
            rejections[reason] = rejections.get(reason, 0) + int(count)
        update["rejection_counts"] = rejections
        update["vector_search_degraded"] = current.vector_search_degraded or bool(
            measurements.get("vector_search_degraded", False)
        )
        self._values[stage] = current.model_copy(update=update)

    def models(self) -> list[ChatbotRetrievalTrace]:
        return list(self._values.values())


class _ChatRetrievalResources:
    """Lazily reuse heavy local models during one complete chat answer."""

    def __init__(self) -> None:
        self._embedding_backend: SentenceTransformerBackend | None = None
        self._reranker: MultilingualReranker | None = None

    def embedding_backend(self, settings: Settings) -> SentenceTransformerBackend:
        if self._embedding_backend is None:
            self._embedding_backend = SentenceTransformerBackend(settings)
        elif self._embedding_backend.model_name != settings.embeddings.model_name:
            raise RuntimeError("chat retrieval cannot mix embedding models")
        return self._embedding_backend

    def reranker(self, settings: Settings) -> MultilingualReranker:
        if self._reranker is None:
            self._reranker = MultilingualReranker.from_settings(settings)
        return self._reranker

    def close(self) -> None:
        if self._reranker is not None:
            self._reranker.close()
            self._reranker = None
        if self._embedding_backend is not None:
            self._embedding_backend.close()
            self._embedding_backend = None


def _evidence_rag_service(
    client: Any,
    answer_effort: AnswerEffort,
    correction_temperature: float,
) -> CiderEvidenceRagService:
    """Keep injected legacy test/adaptor factories compatible with the new option."""

    parameters = inspect.signature(CiderEvidenceRagService).parameters.values()
    supports_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    options: dict[str, Any] = {}
    if supports_kwargs or any(parameter.name == "answer_effort" for parameter in parameters):
        options["answer_effort"] = answer_effort
    if supports_kwargs or any(
        parameter.name == "correction_temperature" for parameter in parameters
    ):
        options["correction_temperature"] = correction_temperature
    return CiderEvidenceRagService(client, **options)


def _serialized_chat_retrieval(
    operation: Callable[..., Any],
    *args: Any,
    timing: _ChatTimingCollector | None = None,
    timing_stage: str = "local_retrieval",
    **kwargs: Any,
) -> Any:
    """Keep local model/index access exclusive without serializing ARGO generation."""

    wait_started = perf_counter()
    wait_memory = timing.snapshot() if timing is not None else None
    with _LOCAL_CHAT_RETRIEVAL_LOCK:
        if timing is not None:
            timing.add(
                "retrieval_lock_wait",
                perf_counter() - wait_started,
                before=wait_memory,
            )
        operation_started = perf_counter()
        operation_memory = timing.snapshot() if timing is not None else None
        try:
            return operation(*args, **kwargs)
        finally:
            if timing is not None:
                timing.add(
                    timing_stage,
                    perf_counter() - operation_started,
                    before=operation_memory,
                )


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
    iterator = (
        (
            Path(current_root) / name
            for current_root, _directories, names in os.walk(root)
            for name in names
        )
        if recursive
        else root.iterdir()
    )
    candidates = (
        _windows_extended_path(path) for path in iterator if path.suffix.casefold() == ".pdf"
    )
    yield from sorted(path for path in candidates if path.is_file())


def _windows_extended_path(path: Path) -> Path:
    """Keep existing Windows files addressable past the legacy MAX_PATH limit."""

    raw_path = str(path)
    if os.name != "nt" or raw_path.startswith("\\\\?\\") or len(raw_path) < 260:
        return path
    if raw_path.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{raw_path[2:]}")
    return Path(f"\\\\?\\{raw_path}")


def ingest_paths(
    settings: Settings,
    database: Database,
    paths: Sequence[Path],
    *,
    progress: ProgressCallback | None = None,
    ocr_extractor: PdfExtractor | None = None,
    stop_on_error: bool = False,
    precomputed_sha256: Mapping[Path, str] | None = None,
    memory_retry_attempts: int = 0,
    memory_retry_delay_seconds: float = 10.0,
) -> list[IngestionReport]:
    pipeline = IngestionPipeline(settings, database)
    ocr_pipeline = (
        IngestionPipeline(
            settings,
            database,
            extractor=ocr_extractor,
            refresh_ocr_cache=True,
        )
        if ocr_extractor is not None
        else None
    )
    reports: list[IngestionReport] = []
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        if progress is not None:
            progress(index - 1, total, path.name, "ingestion")
        sha256 = precomputed_sha256.get(path) if precomputed_sha256 is not None else None
        ingestion_options = {"precomputed_sha256": sha256} if sha256 is not None else {}
        report = pipeline.ingest_file(path, **ingestion_options)
        memory_attempt = 0
        while (
            report.status == "failed"
            and report.error_type == "MemoryLimitError"
            and memory_attempt < memory_retry_attempts
        ):
            memory_attempt += 1
            if progress is not None:
                progress(index - 1, total, path.name, "waiting_memory")
            sleep(memory_retry_delay_seconds)
            report = pipeline.ingest_file(path, **ingestion_options)
        if report.status == "ocr_required" and ocr_pipeline is not None:
            if progress is not None:
                progress(index - 1, total, path.name, "ocr")
            report = ocr_pipeline.ingest_file(path, **ingestion_options)
        reports.append(report)
        if progress is not None:
            progress(index, total, path.name, report.status)
        if report.status == "failed" and stop_on_error:
            break
    return reports


def ingest_and_index_paths(
    settings: Settings,
    database: Database,
    paths: Sequence[Path],
    **ingestion_options: Any,
) -> tuple[list[IngestionReport], EmbeddingRunReport | None]:
    """Persist PDFs, then index only their successfully resolved article ids.

    This intentionally owns the embedding resources only after ingestion has
    committed its SQLite transaction.  Callers must treat a non-successful
    embedding report as a failed addition, even though the durable ingestion
    can be resumed later from its pending chunks.
    """

    reports = ingest_paths(settings, database, paths, **ingestion_options)
    resolved_article_ids = [
        report.article_id
        for report in reports
        if report.article_id is not None and report.status in {"chunks_ready", "duplicate"}
    ]
    article_ids = list(dict.fromkeys(resolved_article_ids))
    if not article_ids or not database.chunks_for_embedding(
        limit=1,
        retry_failed=True,
        article_ids=article_ids,
    ):
        return reports, None
    indexing = index_pending_chunks(
        settings,
        database,
        article_ids=article_ids,
        retry_failed=True,
    )
    if database.chunks_for_embedding(
        limit=1,
        retry_failed=True,
        article_ids=article_ids,
    ):
        raise RuntimeError(
            "L’ingestion est persistée, mais l’indexation automatique reste incomplète."
        )
    return reports, indexing


def index_pending_chunks(
    settings: Settings,
    database: Database,
    *,
    article_ids: Sequence[str] | None = None,
    retry_failed: bool = False,
    _manifest_is_building: bool = False,
) -> EmbeddingRunReport:
    index = QdrantLocalIndex(settings)
    backend: SentenceTransformerBackend | None = None
    try:
        has_pending_chunks = bool(
            database.chunks_for_embedding(
                limit=1,
                retry_failed=retry_failed,
                article_ids=article_ids,
            )
        )
        has_recoverable_processing = bool(database.embedding_status_counts().get("processing", 0))
        resumed_manifest = resume_index_generation(index)
        managed_manifest = _manifest_is_building or resumed_manifest is not None
        index_manifest = resumed_manifest
        if _manifest_is_building:
            index_manifest = assert_index_generation_mutable(index)
            if index_manifest is None:
                raise RuntimeError("expected a building index generation manifest")
        if (has_pending_chunks or has_recoverable_processing) and not managed_manifest:
            index_manifest = prepare_index_generation_mutation(index)
            managed_manifest = index_manifest is not None
        backend = SentenceTransformerBackend(
            settings,
            require_model_manifest=managed_manifest,
        )
        report = EmbeddingBatchProcessor(settings, database, backend).run(
            index,
            retry_failed=retry_failed,
            stop_on_error=True,
            close_backend=True,
            article_ids=article_ids,
        )
        if (
            managed_manifest
            and report.error_type is None
            and report.chunks_failed == 0
            and index.collection_exists()
        ):
            write_ready_index_generation_manifest(
                database,
                index,
                generation_id=index_manifest.generation_id,
                created_at=index_manifest.created_at,
            )
        return report
    finally:
        if backend is not None:
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


def _verified_normalized_doi(value: object) -> str | None:
    """Return a DOI only when the persisted value is complete and already normalized."""

    if not isinstance(value, str):
        return None
    cleaned = value.strip().casefold()
    normalized = normalize_doi(cleaned)
    if normalized is None or normalized != cleaned:
        return None
    return normalized


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
        current_full_text = current.record_id.startswith("common:")
        candidate_full_text = record.record_id.startswith("common:")
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
            int,
            BibliographicHybridResult,
        ]
    ] = []
    tier_priority = {"exact": 0, "near": 1, "distant": 2, "none": 3}
    grade_priority = {"A": 0, "B": 1, "C": 2, "D": 3, "unassessed": 4}
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
                grade_priority[relevance.evidence_grade],
                int(not relevance.causal_match),
                tier_priority[relevance.matrix_tier],
                record,
            )
        )
    assessed.sort(
        key=lambda item: (
            item[1],
            item[2],
            item[3],
            -item[0],
            item[4].rank,
            item[4].record_id,
        )
    )
    return [
        item[4].model_copy(update={"rank": rank, "score": item[0]})
        for rank, item in enumerate(assessed[:limit], start=1)
    ]


def search_common_corpus_abstracts(
    settings: Settings,
    *,
    query: str,
    limit: int = 15,
    search_queries: Sequence[str] = (),
    intent_override: ScientificIntent | None = None,
    max_query_variants: int | None = None,
    retrieval_resources: _ChatRetrievalResources | None = None,
    retrieval_trace: _ChatRetrievalTraceCollector | None = None,
    supplemental: bool = False,
) -> list[BibliographicHybridResult]:
    """Search full articles and verified abstract-only records in the common corpus."""

    if not query.strip():
        raise ValueError("common corpus abstract query cannot be empty")
    if not 1 <= limit <= 100:
        raise ValueError("common corpus abstract limit must be between 1 and 100")
    scoped_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    query_limit = max_query_variants or scoped_settings.retrieval.hybrid_max_query_variants
    if not 1 <= query_limit <= scoped_settings.retrieval.hybrid_max_query_variants:
        raise ValueError("abstract query variant limit is outside configured bounds")
    queries = list(
        dict.fromkeys(" ".join(item.split()) for item in [query, *search_queries] if item.strip())
    )[:query_limit]
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
    lexical_candidate_count = 0
    for search_query in queries:
        lexical = lexical_service.search(
            expand_cider_query(search_query)[:maximum_query_length],
            limit=candidate_limit,
            mode="any",
        )
        lexical_candidate_count += len(lexical.results)
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
        doi = _verified_normalized_doi(row["doi"])
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
    abstract_lexical_candidates = 0
    abstract_dense_candidates = 0
    abstract_rrf_candidates = 0
    unverified_abstract_count = 0
    abstract_store = BibliographicHarvestStore(database)
    if abstract_store.statistics()["abstracts"]:
        backend = (
            retrieval_resources.embedding_backend(scoped_settings)
            if retrieval_resources is not None
            else SentenceTransformerBackend(scoped_settings)
        )
        service = BibliographicHybridSearchService(
            scoped_settings,
            abstract_store,
            backend,
            BibliographicVectorIndex(scoped_settings),
        )
        try:
            collected_abstracts: dict[str, BibliographicHybridResult] = {}
            for search_query in queries:
                response = service.search(
                    search_query,
                    limit=min(max(limit * 2, 30), 60),
                )
                abstract_lexical_candidates += response.lexical_candidate_count
                abstract_dense_candidates += response.dense_candidate_count
                abstract_rrf_candidates += response.rrf_unique_candidate_count
                for result in response.results:
                    doi = _verified_normalized_doi(result.doi)
                    if doi is None:
                        unverified_abstract_count += 1
                        continue
                    converted = result.model_copy(
                        update={
                            "record_id": f"common-abstract:{result.record_id}",
                            "doi": doi,
                            "url": f"https://doi.org/{doi}",
                        }
                    )
                    key = _bibliographic_key(converted)
                    current = collected_abstracts.get(key)
                    if current is None or converted.score > current.score:
                        collected_abstracts[key] = converted
            abstract_results = list(collected_abstracts.values())
        finally:
            if retrieval_resources is None:
                service.close()
            else:
                service.index.close()

    full_text_dois = {
        doi
        for row in database.list_articles()
        if (doi := _verified_normalized_doi(row["doi"])) is not None
    }
    filtered_abstracts = [result for result in abstract_results if result.doi not in full_text_dois]
    reranker_input_count = len(article_results) + len(filtered_abstracts)
    ranked = rerank_bibliographic_candidates(
        query,
        [*article_results, *filtered_abstracts],
        limit=limit,
        intent_override=intent_override,
    )
    if retrieval_trace is not None:
        retrieval_trace.add(
            "supplemental_abstract_search" if supplemental else "abstract_search",
            query_variant_count=len(queries),
            lexical_candidate_count=(lexical_candidate_count + abstract_lexical_candidates),
            dense_candidate_count=abstract_dense_candidates,
            rrf_unique_candidate_count=abstract_rrf_candidates,
            fused_candidate_count=len(article_results) + len(abstract_results),
            pre_rerank_candidate_count=reranker_input_count,
            post_rerank_candidate_count=len(ranked),
            selected_article_count=len(ranked),
            rejection_counts={
                "abstract_without_verified_doi": unverified_abstract_count,
                "duplicate_of_full_text": len(abstract_results) - len(filtered_abstracts),
                "not_selected_after_reranking": max(0, reranker_input_count - len(ranked)),
            },
        )
    return ranked


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
    lexical_candidate_count = 0
    for variant_index, variant in enumerate(variants):
        lexical = lexical_service.search(
            variant.text[: settings.retrieval.lexical_max_query_characters],
            limit=candidate_limit,
            mode="any",
            article_ids=article_ids,
        )
        lexical_candidate_count += len(lexical.results)
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
    response = ArticleRankingService(settings, database).rank_candidates(
        query,
        chunks,
        article_count=article_count,
        diversity_mode="none",
        central_concepts=central_concepts,
    )
    return response.model_copy(
        update={
            "query_variant_count": len(variants),
            "lexical_candidate_count": lexical_candidate_count,
            "dense_candidate_count": 0,
            "rrf_unique_candidate_count": len(fused),
        }
    )


def search_common_corpus_full_text_evidence(
    settings: Settings,
    *,
    query: str,
    article_count: int = 6,
    article_ids: Sequence[str] | None = None,
    search_queries: Sequence[str] = (),
    axis_queries: Mapping[str, Sequence[str]] | None = None,
    intent_override: ScientificIntent | None = None,
    max_query_variants: int | None = None,
    passage_count: int | None = None,
    candidate_chunks_per_article: int | None = None,
    context_radius: int = 1,
    retrieval_resources: _ChatRetrievalResources | None = None,
    retrieval_trace: _ChatRetrievalTraceCollector | None = None,
    supplemental: bool = False,
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
    maximum_variant_count = (
        max_query_variants or scoped_settings.retrieval.hybrid_max_query_variants
    )
    if not 1 <= maximum_variant_count <= scoped_settings.retrieval.hybrid_max_query_variants:
        raise ValueError("full-text query variant limit is outside configured bounds")
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
    cleaned_axis_queries = {
        axis_key.strip(): list(
            dict.fromkeys(
                " ".join(axis_query.split())[:2000]
                for axis_query in queries
                if len(" ".join(axis_query.split())) >= 2
            )
        )[: min(2, maximum_variant_count)]
        for axis_key, queries in (axis_queries or {}).items()
        if axis_key.strip()
    }
    cleaned_axis_queries = dict(
        list((axis_key, queries) for axis_key, queries in cleaned_axis_queries.items() if queries)[
            :4
        ]
    )
    # One axis does not need a separate pool: the global search already covers it.
    if len(cleaned_axis_queries) < 2:
        cleaned_axis_queries = {}
    axis_candidate_count = min(max(article_count * 2, 12), 30)
    axis_rankings: dict[str, Sequence[RankedArticle]] = {}
    if local_model_path(scoped_settings).is_dir():
        backend = (
            retrieval_resources.embedding_backend(scoped_settings)
            if retrieval_resources is not None
            else SentenceTransformerBackend(scoped_settings)
        )
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
                    close_backend=retrieval_resources is None,
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
            for axis_key, queries in cleaned_axis_queries.items():
                axis_query = queries[0]
                axis_ranking = ranking_service.search(
                    axis_query,
                    query_variants=queries,
                    article_count=axis_candidate_count,
                    diversity_mode="none",
                    article_ids=article_ids,
                )
                axis_rankings[axis_key] = axis_ranking.articles
                if retrieval_trace is not None:
                    retrieval_trace.add(
                        (
                            "supplemental_full_text_axis_search"
                            if supplemental
                            else "full_text_axis_search"
                        ),
                        query_variant_count=axis_ranking.query_variant_count,
                        lexical_candidate_count=axis_ranking.lexical_candidate_count,
                        dense_candidate_count=axis_ranking.dense_candidate_count,
                        rrf_unique_candidate_count=axis_ranking.rrf_unique_candidate_count,
                        fused_candidate_count=axis_ranking.hybrid_candidate_count,
                        selected_article_count=axis_ranking.selected_article_count,
                        vector_search_degraded=axis_ranking.vector_search_degraded,
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
        for axis_key, queries in cleaned_axis_queries.items():
            axis_variants = [
                QueryVariant(
                    text=axis_query,
                    language="mixed",
                    derivation="argo_plan",
                    matched_terms=["argo_axis_query"],
                    anchor_terms=[],
                    scope_tier="strict",
                )
                for axis_query in queries
            ]
            axis_ranking = _lexical_full_text_ranking(
                scoped_settings,
                database,
                query=queries[0],
                variants=axis_variants,
                article_count=axis_candidate_count,
                central_concepts=None,
                article_ids=article_ids,
            )
            axis_rankings[axis_key] = axis_ranking.articles
            if retrieval_trace is not None:
                retrieval_trace.add(
                    (
                        "supplemental_full_text_axis_search"
                        if supplemental
                        else "full_text_axis_search"
                    ),
                    query_variant_count=axis_ranking.query_variant_count,
                    lexical_candidate_count=axis_ranking.lexical_candidate_count,
                    dense_candidate_count=axis_ranking.dense_candidate_count,
                    rrf_unique_candidate_count=axis_ranking.rrf_unique_candidate_count,
                    fused_candidate_count=axis_ranking.hybrid_candidate_count,
                    selected_article_count=axis_ranking.selected_article_count,
                    vector_search_degraded=axis_ranking.vector_search_degraded,
                )

    if retrieval_trace is not None:
        retrieval_trace.add(
            "supplemental_full_text_search" if supplemental else "full_text_search",
            query_variant_count=ranking.query_variant_count,
            lexical_candidate_count=ranking.lexical_candidate_count,
            dense_candidate_count=ranking.dense_candidate_count,
            rrf_unique_candidate_count=ranking.rrf_unique_candidate_count,
            fused_candidate_count=ranking.hybrid_candidate_count,
            selected_article_count=ranking.selected_article_count,
            vector_search_degraded=ranking.vector_search_degraded,
        )

    coverage_pool = merge_axis_rankings(ranking.articles, axis_rankings)
    if retrieval_trace is not None:
        pool_input_count = len(ranking.articles) + sum(
            len(articles) for articles in axis_rankings.values()
        )
        retrieval_trace.add(
            ("supplemental_full_text_pool_merge" if supplemental else "full_text_pool_merge"),
            pre_rerank_candidate_count=pool_input_count,
            post_rerank_candidate_count=len(coverage_pool.articles),
            selected_article_count=len(coverage_pool.articles),
            rejection_counts={
                "duplicate_across_query_pools": max(
                    0, pool_input_count - len(coverage_pool.articles)
                )
            },
        )

    chunk_rows = database.chunk_details_by_ids(
        [chunk_id for article in coverage_pool.articles for chunk_id in article.top_chunk_ids]
    )
    relevance_by_article = {}
    reranker_candidates: list[RerankerCandidate] = []
    for article in coverage_pool.articles:
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
    reranker = (
        retrieval_resources.reranker(scoped_settings)
        if retrieval_resources is not None
        else MultilingualReranker.from_settings(scoped_settings)
    )
    if reranker.enabled and not local_model_available:
        raise RuntimeError("configured local reranker model is unavailable")
    try:
        reranked = reranker.rerank(
            intent.selector_query(),
            reranker_candidates,
            top_k=len(reranker_candidates),
        )
    finally:
        if retrieval_resources is None:
            reranker.close()
    if retrieval_trace is not None:
        retrieval_trace.add(
            ("supplemental_full_text_reranking" if supplemental else "full_text_reranking"),
            pre_rerank_candidate_count=len(reranker_candidates),
            post_rerank_candidate_count=len(reranked),
            rejection_counts={
                "not_returned_by_reranker": max(0, len(reranker_candidates) - len(reranked))
            },
        )
    reranker_position = {
        result.candidate_id: position for position, result in enumerate(reranked, start=1)
    }
    articles_by_id = {article.article_id: article for article in coverage_pool.articles}
    assessed_articles: list[tuple[float, RankedArticle]] = []
    for article in coverage_pool.articles:
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
    grade_priority = {"A": 0, "B": 1, "C": 2, "D": 3, "unassessed": 4}
    assessed_articles.sort(
        key=lambda item: (
            grade_priority[relevance_by_article[item[1].article_id].evidence_grade],
            int(not relevance_by_article[item[1].article_id].causal_match),
            -item[0],
            tier_priority[relevance_by_article[item[1].article_id].matrix_tier],
            item[1].base_rank,
            item[1].article_id,
        )
    )

    selection_axis_ranks = dict(coverage_pool.axis_ranks)
    for facet in intent.facets:
        if facet.key in selection_axis_ranks:
            continue
        fallback_ranks = {
            canonical_article_key(article): rank
            for rank, (_score, article) in enumerate(assessed_articles, start=1)
            if facet.key in relevance_by_article[article.article_id].matched_facets
        }
        if fallback_ranks:
            selection_axis_ranks[facet.key] = fallback_ranks

    selected_articles = select_with_axis_coverage(
        assessed_articles,
        article_count=article_count,
        axis_ranks=selection_axis_ranks,
    )

    selector = EvidencePassageSelector(scoped_settings, database)
    records: list[ChatEvidenceRecord] = []
    selected_passage_count = (
        min(
            scoped_settings.evidence.min_passages_per_article + 1,
            scoped_settings.evidence.passages_per_article,
        )
        if passage_count is None
        else passage_count
    )
    selector_query = intent.selector_query()
    for article in selected_articles[:article_count]:
        passages = selector.select(
            query=selector_query,
            article_id=article.article_id,
            ranked_chunk_ids=article.top_chunk_ids,
            passage_count=selected_passage_count,
            expand_intra_article_context=(
                relevance_by_article[article.article_id].evidence_grade in {"A", "B"}
            ),
            max_candidate_chunks=candidate_chunks_per_article,
            neighborhood_radius=context_radius,
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
                evidence_grade=relevance_by_article[article.article_id].evidence_grade,
                passages=[
                    ChatEvidencePassage(
                        evidence_id=f"{record_id}:chunk:{passage.chunk_id}",
                        chunk_id=passage.chunk_id,
                        section=passage.section,
                        context_role=(
                            "anchor"
                            if passage.chunk_id in article.top_chunk_ids
                            else passage.context_role or "other"
                        ),
                        page_start=passage.page_start,
                        page_end=passage.page_end,
                        text=passage.text,
                    )
                    for passage in passages
                ],
            )
        )
    if retrieval_trace is not None:
        retrieval_trace.add(
            (
                "supplemental_full_text_evidence_selection"
                if supplemental
                else "full_text_evidence_selection"
            ),
            pre_rerank_candidate_count=len(assessed_articles),
            post_rerank_candidate_count=len(selected_articles[:article_count]),
            selected_article_count=len(records),
            selected_passage_count=sum(len(record.passages) for record in records),
            rejection_counts={
                "not_selected_after_scientific_ranking": max(
                    0, len(assessed_articles) - len(selected_articles[:article_count])
                ),
                "no_passage_selected": max(
                    0, len(selected_articles[:article_count]) - len(records)
                ),
            },
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
        scope = None if origin == "external_api" else CorpusScope.COMMON
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
                        "evidence_grade": relevance.evidence_grade,
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
    grade_priority = {"A": 0, "B": 1, "C": 2, "D": 3, "unassessed": 4}
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            grade_priority[item[1].evidence_grade],
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
        return CiderAbstractRagService(
            llm,
            correction_temperature=settings.argo.scientific_correction_temperature,
        ).answer(question, search_response.results)


def _semantic_filter_and_coverage(
    settings: Settings,
    *,
    question: str,
    axes: Sequence[ResearchAxis],
    evidence: Sequence[ChatEvidenceRecord],
    on_argo_reserved: Callable[[], None] | None,
    on_coverage_started: Callable[[], None] | None = None,
    timings: _ChatTimingCollector | None = None,
) -> tuple[SemanticFilterResult, CoverageAssessmentResult]:
    """Filter evidence by scientific meaning, then assess each planned axis."""

    with ArgoClient(settings) as llm:
        semantic_started = perf_counter()
        semantic_memory = timings.snapshot() if timings is not None else None
        try:
            semantic_filter = ArgoSemanticEvidenceFilter(llm).filter_records(
                question,
                axes,
                evidence,
                on_argo_reserved=on_argo_reserved,
            )
        finally:
            if timings is not None:
                timings.add(
                    "argo_semantic_filter",
                    perf_counter() - semantic_started,
                    before=semantic_memory,
                )
        if timings is not None:
            timings.add_tokens(
                "argo_semantic_filter",
                prompt_tokens=semantic_filter.prompt_tokens,
                completion_tokens=semantic_filter.completion_tokens,
            )
        if on_coverage_started is not None:
            on_coverage_started()
        coverage_started = perf_counter()
        coverage_memory = timings.snapshot() if timings is not None else None
        try:
            coverage = ArgoEvidenceCoverageAssessor(llm).assess(
                question,
                axes,
                evidence,
                semantic_filter,
                on_argo_reserved=on_argo_reserved,
            )
        finally:
            if timings is not None:
                timings.add(
                    "argo_coverage",
                    perf_counter() - coverage_started,
                    before=coverage_memory,
                )
        if timings is not None:
            timings.add_tokens(
                "argo_coverage",
                prompt_tokens=coverage.prompt_tokens,
                completion_tokens=coverage.completion_tokens,
            )
    return semantic_filter, coverage


def _run_semantic_filter_and_coverage(
    settings: Settings,
    *,
    question: str,
    axes: Sequence[ResearchAxis],
    evidence: Sequence[ChatEvidenceRecord],
    on_argo_reserved: Callable[[], None] | None,
    on_coverage_started: Callable[[], None] | None,
    timings: _ChatTimingCollector,
) -> tuple[SemanticFilterResult, CoverageAssessmentResult]:
    """Supply latency telemetry when supported by an injected semantic adaptor."""

    parameters = inspect.signature(_semantic_filter_and_coverage).parameters.values()
    timing_options = (
        {"timings": timings}
        if any(
            parameter.name == "timings" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        else {}
    )
    return _semantic_filter_and_coverage(
        settings,
        question=question,
        axes=axes,
        evidence=evidence,
        on_argo_reserved=on_argo_reserved,
        on_coverage_started=on_coverage_started,
        **timing_options,
    )


def _coverage_follow_up_queries(
    axes: Sequence[ResearchAxis],
    coverage: CoverageAssessmentResult,
    *,
    include_all_axes: bool = False,
    exclude_queries: Sequence[str] = (),
) -> dict[str, list[str]]:
    """Return bounded follow-up queries only for axes not proven covered."""

    coverage_by_key = {assessment.axis_key: assessment for assessment in coverage.axes}
    excluded = {" ".join(query.split()).casefold() for query in exclude_queries}
    follow_up: dict[str, list[str]] = {}
    for axis in axes:
        assessment = coverage_by_key.get(axis.key)
        if not include_all_axes and assessment is not None and assessment.status == "covered":
            continue
        generated = [] if assessment is None else assessment.suggested_queries
        queries = list(
            dict.fromkeys(
                cleaned
                for query in [*generated, *axis.search_queries]
                if len(cleaned := " ".join(query.split())[:600]) >= 2
                and cleaned.casefold() not in excluded
            )
        )[:4]
        if queries:
            follow_up[axis.key] = queries
    return follow_up


def _coverage_notes(
    axes: Sequence[ResearchAxis],
    coverage: CoverageAssessmentResult | None,
) -> list[str]:
    """Translate a reliable incomplete-coverage assessment into synthesis constraints."""

    if coverage is None or coverage.used_fallback:
        return []
    labels = {axis.key: axis.label for axis in axes}
    notes: list[str] = []
    for assessment in coverage.axes:
        if assessment.status == "covered":
            continue
        missing = "; ".join(assessment.missing_information[:3])
        note = (
            f"Axe « {labels.get(assessment.axis_key, assessment.axis_key)} » : "
            f"couverture documentaire {assessment.status}."
        )
        if missing:
            note += f" Informations encore non documentées : {missing}."
        notes.append(note[:700])
    return notes


def _argo_diagnostic_code(error: ArgoError) -> str:
    if isinstance(error, ArgoScientificValidationError):
        return error.reason.value
    if isinstance(error, ArgoAuthenticationError):
        return "argo_authentication"
    if isinstance(error, ArgoAuthorizationError):
        return "argo_authorization"
    if isinstance(error, ArgoProtocolError):
        return "argo_protocol"
    if isinstance(error, ArgoGenerationError):
        return "argo_generation"
    return "argo_unavailable"


def _fallback_chatbot_result(
    *,
    message: str,
    retrieval_query: str,
    evidence: Sequence[ChatEvidenceRecord],
    warnings: Sequence[str],
    diagnostic_code: str,
    started: float,
    external_result_count: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    interaction_mode: Literal["research", "conversation"] = "research",
    reused_previous_sources: bool = False,
    figure_analysis_requested: bool = False,
    figure_analysis_count: int = 0,
    figure_analysis_duration_seconds: float = 0.0,
    figure_analysis_model: str | None = None,
    answer_effort: AnswerEffort = AnswerEffort.BALANCED,
    timings: Sequence[ChatbotTiming] = (),
    retrieval_traces: Sequence[ChatbotRetrievalTrace] = (),
) -> ChatbotResult:
    """Return the normal answer shape without presenting raw candidates as a synthesis."""

    selected = list(evidence[:5])
    is_french = question_language(message) == "fr"
    abstained = diagnostic_code == "semantic_filter_empty"
    status: Literal["abstained", "diagnostic_only"] = (
        "abstained" if abstained else "diagnostic_only"
    )
    expected_style = detect_response_style(message)
    if is_french:
        direct = (
            "Les preuves sélectionnées ne permettent pas d'établir une réponse scientifique "
            "suffisamment étayée."
            if abstained
            else "Aucune réponse scientifique validée ne peut être fournie pour cette exécution."
        )
        limitation = (
            "Aucune affirmation n'est présentée, car elle ne pourrait pas être reliée à une "
            "preuve validée. Une nouvelle recherche ou une relance peut être nécessaire."
        )
        headings = (
            "Réponse synthétique",
            "Effets documentés",
            "Limites des preuves",
            "Références",
        )
        no_effect = "Aucun effet directement documenté ne peut être affirmé."
        no_reference = "Aucune référence n'est citée."
    else:
        direct = (
            "The selected evidence does not support a sufficiently grounded scientific answer."
            if abstained
            else "No validated scientific answer can be provided for this execution."
        )
        limitation = (
            "No claim is presented because it could not be linked to validated evidence. "
            "A new search or retry may be required."
        )
        headings = ("Summary answer", "Documented effects", "Evidence limitations", "References")
        no_effect = "No directly documented effect can be stated."
        no_reference = "No reference is cited."
    rendered_direct = f"- {direct}" if expected_style is ResponseStyle.BULLET_LIST else direct
    lines = [
        f"## {headings[0]}",
        "",
        rendered_direct,
        "",
        f"## {headings[1]}",
        "",
        no_effect,
        "",
        f"## {headings[2]}",
        "",
        limitation,
        "",
        f"## {headings[3]}",
        "",
        no_reference,
    ]
    sources: list[ChatbotSource] = []
    model = "deterministic-structured-fallback"
    return ChatbotResult(
        message=" ".join(message.split()),
        retrieval_query=retrieval_query,
        answer_markdown="\n".join(lines),
        sources=sources,
        warnings=[
            *warnings,
            (
                f"Sortie de secours activée ({diagnostic_code})."
                if is_french
                else f"Fallback output enabled ({diagnostic_code})."
            ),
        ],
        model=model,
        local_result_count=sum(record.origin == "local_rag" for record in selected),
        external_result_count=external_result_count,
        external_enrichment_used=any(source.origin == "external_api" for source in sources),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        duration_seconds=perf_counter() - started,
        generation_status=status,
        diagnostic_code=diagnostic_code,
        interaction_mode=interaction_mode,
        reused_previous_sources=reused_previous_sources,
        figure_analysis_requested=figure_analysis_requested,
        figure_analysis_count=figure_analysis_count,
        figure_analysis_duration_seconds=figure_analysis_duration_seconds,
        figure_analysis_model=figure_analysis_model,
        answer_effort=answer_effort,
        timings=list(timings),
        retrieval_traces=list(retrieval_traces),
    )


def answer_chatbot(
    settings: Settings,
    database: Database,
    *,
    message: str,
    history: Sequence[Mapping[str, str]],
    use_external_sources: bool,
    analyze_figures: bool = False,
    interaction_mode: str = "research",
    previous_sources: Sequence[ChatbotSource] = (),
    on_figure_analysis: Callable[[], None] | None = None,
    on_argo_reserved: Callable[[], None] | None = None,
    on_argo_response: Callable[[], None] | None = None,
    on_progress: ChatbotProgressCallback | None = None,
    experimental_profile: Literal["p0", "p1", "p2"] | None = None,
    answer_effort: AnswerEffort = AnswerEffort.BALANCED,
) -> ChatbotResult:
    """Own and explicitly release heavy resources for one complete chat answer."""

    resources = _ChatRetrievalResources()
    timings = _ChatTimingCollector(settings)
    retrieval_traces = _ChatRetrievalTraceCollector()
    try:
        return _answer_chatbot(
            settings,
            database,
            message=message,
            history=history,
            use_external_sources=use_external_sources,
            analyze_figures=analyze_figures,
            interaction_mode=interaction_mode,
            previous_sources=previous_sources,
            on_figure_analysis=on_figure_analysis,
            on_argo_reserved=on_argo_reserved,
            on_argo_response=on_argo_response,
            on_progress=on_progress,
            experimental_profile=experimental_profile,
            answer_effort=answer_effort,
            retrieval_resources=resources,
            timings=timings,
            retrieval_traces=retrieval_traces,
        )
    finally:
        resources.close()


def _answer_chatbot(
    settings: Settings,
    database: Database,
    *,
    message: str,
    history: Sequence[Mapping[str, str]],
    use_external_sources: bool,
    analyze_figures: bool = False,
    interaction_mode: str = "research",
    previous_sources: Sequence[ChatbotSource] = (),
    on_figure_analysis: Callable[[], None] | None = None,
    on_argo_reserved: Callable[[], None] | None = None,
    on_argo_response: Callable[[], None] | None = None,
    on_progress: ChatbotProgressCallback | None = None,
    experimental_profile: Literal["p0", "p1", "p2"] | None = None,
    answer_effort: AnswerEffort = AnswerEffort.BALANCED,
    retrieval_resources: _ChatRetrievalResources,
    timings: _ChatTimingCollector,
    retrieval_traces: _ChatRetrievalTraceCollector,
) -> ChatbotResult:
    """Answer with local full-text passages, abstract fallback and bounded enrichment."""

    started = perf_counter()
    effort_budget = answer_effort_budget(answer_effort)

    def publish_progress(stage: ChatbotProgressStage) -> None:
        if on_progress is not None:
            on_progress(stage)

    publish_progress("planning")
    active_experimental_profile = experimental_profile or settings.app.experimental_chat_profile
    context = conversation_context(history)
    retrieval_query = contextualize_retrieval_query(message, context)
    warnings: list[str] = []
    try:
        reused_evidence = (
            chat_evidence_from_previous_sources(
                settings,
                query=retrieval_query,
                sources=previous_sources,
            )
            if interaction_mode == "conversation"
            else []
        )
    except Exception as exc:
        reused_evidence = []
        warnings.append(
            "Les preuves de la conversation n'ont pas pu être rechargées "
            f"({type(exc).__name__}); une nouvelle recherche locale est exécutée."
        )
    if reused_evidence:
        retrieval_traces.add(
            "llm_context",
            selected_article_count=len(reused_evidence),
            selected_passage_count=sum(len(record.passages) for record in reused_evidence),
        )
        publish_progress("generation")
        generation_started = perf_counter()
        generation_memory = timings.snapshot()
        try:
            with ArgoClient(settings) as llm:
                rag = _evidence_rag_service(
                    llm,
                    answer_effort,
                    settings.argo.scientific_correction_temperature,
                )
                rag.experimental_profile = active_experimental_profile
                answer = rag.answer(
                    message,
                    reused_evidence,
                    conversation_history=context,
                    on_argo_reserved=on_argo_reserved,
                    on_argo_response=on_argo_response,
                )
        except ArgoQuotaError:
            timings.add(
                "argo_generation",
                perf_counter() - generation_started,
                before=generation_memory,
            )
            raise
        except ArgoError as exc:
            timings.add(
                "argo_generation",
                perf_counter() - generation_started,
                before=generation_memory,
            )
            return _fallback_chatbot_result(
                message=message,
                retrieval_query=retrieval_query,
                evidence=reused_evidence,
                warnings=warnings,
                diagnostic_code=_argo_diagnostic_code(exc),
                started=started,
                external_result_count=sum(
                    record.origin == "external_api" for record in reused_evidence
                ),
                interaction_mode="conversation",
                reused_previous_sources=True,
                figure_analysis_requested=analyze_figures,
                answer_effort=answer_effort,
                timings=timings.models(),
                retrieval_traces=retrieval_traces.models(),
            )
        timings.add(
            "argo_generation",
            perf_counter() - generation_started,
            before=generation_memory,
        )
        timings.add_tokens(
            "argo_generation",
            prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
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
            figure_analysis_requested=analyze_figures,
            generation_status=getattr(answer, "generation_status", "generated"),
            answer_effort=answer_effort,
            timings=timings.models(),
            retrieval_traces=retrieval_traces.models(),
            generation_traces=getattr(answer, "generation_traces", []),
        )
    planning: QueryPlanningResult
    planning_started = perf_counter()
    planning_memory = timings.snapshot()
    try:
        with ArgoClient(settings) as planning_client:
            planning = ArgoQueryPlanningService(planning_client).plan(
                retrieval_query,
                conversation_history=context,
                on_argo_reserved=on_argo_reserved,
            )
    except ArgoQuotaError:
        raise
    except ArgoError as exc:
        planning = deterministic_query_plan(retrieval_query)
        warnings.append(
            "La planification ARGO est indisponible "
            f"({_argo_diagnostic_code(exc)}); utilisation du planificateur local de secours."
        )
    except Exception as exc:
        planning = deterministic_query_plan(retrieval_query)
        warnings.append(
            "La compréhension adaptative de la requête est indisponible "
            f"({type(exc).__name__}); utilisation du planificateur local de secours."
        )
    finally:
        timings.add(
            "argo_planning",
            perf_counter() - planning_started,
            before=planning_memory,
        )
    timings.add_tokens(
        "argo_planning",
        prompt_tokens=planning.prompt_tokens,
        completion_tokens=planning.completion_tokens,
    )
    intent = planning.plan.scientific_intent(retrieval_query)
    search_queries = planning.plan.retrieval_queries

    publish_progress("search")
    retrieval_failed = False
    try:
        local_results = _serialized_chat_retrieval(
            search_common_corpus_abstracts,
            settings,
            query=retrieval_query,
            limit=effort_budget.abstract_result_limit,
            search_queries=search_queries,
            intent_override=intent,
            max_query_variants=effort_budget.max_query_variants,
            retrieval_resources=retrieval_resources,
            retrieval_trace=retrieval_traces,
            timing=timings,
            timing_stage="abstract_search",
        )
    except Exception as exc:
        retrieval_failed = True
        local_results = []
        warnings.append(
            "La recherche dans les abstracts du corpus commun est indisponible "
            f"({type(exc).__name__})."
        )
    if use_external_sources:
        publish_progress("enrichment")
    if use_external_sources and settings.full_text.enabled:
        _acquired_article_ids, acquisition_warnings = _serialized_chat_retrieval(
            acquire_common_full_text_for_chat,
            settings,
            local_results,
            max_downloads=2,
            timing=timings,
            timing_stage="full_text_acquisition",
        )
        warnings.extend(acquisition_warnings)

    try:
        full_text_records = _serialized_chat_retrieval(
            search_common_corpus_full_text_evidence,
            settings,
            query=retrieval_query,
            article_count=effort_budget.article_count,
            search_queries=search_queries,
            axis_queries={axis.key: axis.search_queries for axis in planning.plan.axes},
            intent_override=intent,
            max_query_variants=effort_budget.max_query_variants,
            passage_count=effort_budget.passages_per_article,
            candidate_chunks_per_article=effort_budget.candidate_chunks_per_article,
            context_radius=effort_budget.context_radius,
            retrieval_resources=retrieval_resources,
            retrieval_trace=retrieval_traces,
            timing=timings,
            timing_stage="full_text_search",
        )
    except Exception as exc:
        retrieval_failed = True
        full_text_records = []
        warnings.append(
            "La recherche dans les textes intégraux est indisponible pour cette réponse "
            f"({type(exc).__name__}); repli sur les abstracts."
        )

    external_report: BibliographicSearchReport | None = None
    if use_external_sources:
        enrichment_started = perf_counter()
        enrichment_memory = timings.snapshot()
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
        finally:
            timings.add(
                "external_enrichment",
                perf_counter() - enrichment_started,
                before=enrichment_memory,
            )

    publish_progress("reranking")
    merge_started = perf_counter()
    merge_memory = timings.snapshot()
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
        limit=effort_budget.evidence_record_limit,
        intent_override=intent,
    )
    timings.add("evidence_merge", perf_counter() - merge_started, before=merge_memory)
    merge_input_count = len(full_text_records) + len(abstract_evidence)
    retrieval_traces.add(
        "evidence_merge",
        pre_rerank_candidate_count=merge_input_count,
        post_rerank_candidate_count=len(evidence),
        selected_article_count=len(evidence),
        selected_passage_count=sum(len(record.passages) for record in evidence),
        rejection_counts={"duplicate_or_not_selected": max(0, merge_input_count - len(evidence))},
    )
    if not evidence:
        return _fallback_chatbot_result(
            message=message,
            retrieval_query=retrieval_query,
            evidence=[],
            warnings=warnings,
            diagnostic_code=(
                "retrieval_unavailable" if retrieval_failed else "retrieval_no_qualified_evidence"
            ),
            started=started,
            external_result_count=external_count,
            prompt_tokens=planning.prompt_tokens,
            completion_tokens=planning.completion_tokens,
            figure_analysis_requested=analyze_figures,
            answer_effort=answer_effort,
            timings=timings.models(),
            retrieval_traces=retrieval_traces.models(),
        )
    if external_report:
        warnings.extend(
            f"La source {error.source} n'a pas répondu à cette requête."
            for error in external_report.errors
        )

    semantic_prompt_tokens = 0
    semantic_completion_tokens = 0
    coverage_prompt_tokens = 0
    coverage_completion_tokens = 0
    semantic_filter: SemanticFilterResult | None = None
    coverage: CoverageAssessmentResult | None = None
    publish_progress("evidence_selection")
    try:
        semantic_filter, coverage = _run_semantic_filter_and_coverage(
            settings,
            question=retrieval_query,
            axes=planning.plan.axes,
            evidence=evidence,
            on_argo_reserved=on_argo_reserved,
            on_coverage_started=lambda: publish_progress("coverage"),
            timings=timings,
        )
    except ArgoQuotaError:
        raise
    except Exception as exc:
        warnings.append(
            "Le contrôle sémantique des preuves est indisponible "
            f"({type(exc).__name__}); les candidats classés localement sont conservés."
        )
    else:
        semantic_prompt_tokens += semantic_filter.prompt_tokens
        semantic_completion_tokens += semantic_filter.completion_tokens
        coverage_prompt_tokens += coverage.prompt_tokens
        coverage_completion_tokens += coverage.completion_tokens
        if semantic_filter.used_fallback:
            warnings.append(
                "Le filtrage sémantique multilingue est partiellement indisponible ; "
                "les candidats concernés ont été conservés par prudence."
            )
        if coverage.used_fallback:
            warnings.append(
                "La couverture documentaire n'a pas pu être vérifiée par l'API ; "
                "la synthèse conserve le classement scientifique local."
            )

    retrieved_evidence = evidence
    semantic_trace_input_count = len(evidence)
    filtered_evidence = (
        evidence if semantic_filter is None else semantic_filter.selected_records(evidence)
    )
    if intent.is_structured and not filtered_evidence:
        recall_preserving_evidence = [
            record for record in evidence if record.evidence_grade in {"A", "B", "unassessed"}
        ]
        if recall_preserving_evidence:
            filtered_evidence = recall_preserving_evidence
            warnings.append(
                "Le filtrage sémantique n'a retenu aucun candidat ; conservation prudente "
                "des preuves locales A/B qualifiées."
            )
    if intent.is_structured and (semantic_filter is None or semantic_filter.used_fallback):
        locally_eligible = [
            record
            for record in filtered_evidence
            if record.evidence_grade in {"A", "B", "unassessed"}
        ]
        # Keep C/D only when no better candidate exists so the synthesis layer can
        # return a precise, topic-aware abstention instead of fabricating an answer.
        if locally_eligible:
            filtered_evidence = locally_eligible
    needs_follow_up = (
        semantic_filter is not None
        and coverage is not None
        and (
            not filtered_evidence
            or (
                effort_budget.follow_up_incomplete_axes
                and not coverage.used_fallback
                and any(assessment.status != "covered" for assessment in coverage.axes)
            )
        )
    )
    if needs_follow_up:
        follow_up_by_axis = _coverage_follow_up_queries(
            planning.plan.axes,
            coverage,
            include_all_axes=not filtered_evidence,
            exclude_queries=[retrieval_query, *search_queries],
        )
        follow_up_queries = list(
            dict.fromkeys(query for queries in follow_up_by_axis.values() for query in queries)
        )[: effort_budget.follow_up_query_limit]
        if follow_up_queries:
            try:
                supplemental_abstracts = _serialized_chat_retrieval(
                    search_common_corpus_abstracts,
                    settings,
                    query=follow_up_queries[0],
                    limit=effort_budget.abstract_result_limit,
                    search_queries=follow_up_queries,
                    intent_override=intent,
                    max_query_variants=effort_budget.max_query_variants,
                    retrieval_resources=retrieval_resources,
                    retrieval_trace=retrieval_traces,
                    supplemental=True,
                    timing=timings,
                    timing_stage="supplemental_abstract_search",
                )
            except Exception as exc:
                supplemental_abstracts = []
                warnings.append(
                    "La recherche complémentaire dans les abstracts est indisponible "
                    f"({type(exc).__name__}); conservation de la première sélection."
                )
            try:
                supplemental_full_text = _serialized_chat_retrieval(
                    search_common_corpus_full_text_evidence,
                    settings,
                    query=follow_up_queries[0],
                    article_count=effort_budget.article_count,
                    search_queries=follow_up_queries,
                    axis_queries=follow_up_by_axis,
                    intent_override=intent,
                    max_query_variants=effort_budget.max_query_variants,
                    passage_count=effort_budget.passages_per_article,
                    candidate_chunks_per_article=(effort_budget.candidate_chunks_per_article),
                    context_radius=effort_budget.context_radius,
                    retrieval_resources=retrieval_resources,
                    retrieval_trace=retrieval_traces,
                    supplemental=True,
                    timing=timings,
                    timing_stage="supplemental_full_text_search",
                )
            except Exception as exc:
                supplemental_full_text = []
                warnings.append(
                    "La recherche complémentaire dans les textes intégraux est indisponible "
                    f"({type(exc).__name__}); les abstracts complémentaires sont conservés."
                )
            expanded_evidence = merge_chat_evidence(
                [
                    *(record for record in evidence if record.evidence_level == "full_text"),
                    *supplemental_full_text,
                ],
                [
                    *(record for record in evidence if record.evidence_level == "abstract"),
                    *abstract_candidates_to_chat_evidence(supplemental_abstracts),
                ],
                query=retrieval_query,
                limit=effort_budget.evidence_record_limit,
                intent_override=intent,
            )
            expanded_input_count = (
                sum(record.evidence_level == "full_text" for record in evidence)
                + len(supplemental_full_text)
                + sum(record.evidence_level == "abstract" for record in evidence)
                + len(supplemental_abstracts)
            )
            retrieval_traces.add(
                "evidence_merge",
                pre_rerank_candidate_count=expanded_input_count,
                post_rerank_candidate_count=len(expanded_evidence),
                selected_article_count=len(expanded_evidence),
                selected_passage_count=sum(len(record.passages) for record in expanded_evidence),
                rejection_counts={
                    "duplicate_or_not_selected": max(
                        0, expanded_input_count - len(expanded_evidence)
                    )
                },
            )
            original_signature = [(record.record_id, record.evidence_level) for record in evidence]
            expanded_signature = [
                (record.record_id, record.evidence_level) for record in expanded_evidence
            ]
            if expanded_signature != original_signature:
                previous_coverage = coverage
                try:
                    second_semantic_filter, second_coverage = _run_semantic_filter_and_coverage(
                        settings,
                        question=retrieval_query,
                        axes=planning.plan.axes,
                        evidence=expanded_evidence,
                        on_argo_reserved=on_argo_reserved,
                        on_coverage_started=lambda: publish_progress("coverage"),
                        timings=timings,
                    )
                except ArgoQuotaError:
                    raise
                except Exception as exc:
                    warnings.append(
                        "Le second contrôle sémantique est indisponible "
                        f"({type(exc).__name__}); conservation de la première sélection."
                    )
                else:
                    semantic_prompt_tokens += second_semantic_filter.prompt_tokens
                    semantic_completion_tokens += second_semantic_filter.completion_tokens
                    coverage_prompt_tokens += second_coverage.prompt_tokens
                    coverage_completion_tokens += second_coverage.completion_tokens
                    if second_semantic_filter.used_fallback:
                        warnings.append(
                            "Le second filtrage sémantique n'a pas pu qualifier les nouveaux "
                            "candidats ; conservation de la première sélection validée."
                        )
                    else:
                        second_filtered_evidence = second_semantic_filter.selected_records(
                            expanded_evidence
                        )
                        if second_filtered_evidence:
                            semantic_filter = second_semantic_filter
                            filtered_evidence = second_filtered_evidence
                            semantic_trace_input_count = len(expanded_evidence)
                            coverage = (
                                previous_coverage
                                if second_coverage.used_fallback
                                else second_coverage
                            )
                        else:
                            warnings.append(
                                "Le second filtrage sémantique n'a retenu aucun nouveau candidat ; "
                                "conservation de la première sélection validée."
                            )

    evidence = filtered_evidence
    retrieval_traces.add(
        "semantic_filter",
        pre_rerank_candidate_count=semantic_trace_input_count,
        post_rerank_candidate_count=len(evidence),
        selected_article_count=len(evidence),
        selected_passage_count=sum(len(record.passages) for record in evidence),
        rejection_counts={
            "semantic_or_scientific_grade_rejected": max(
                0, semantic_trace_input_count - len(evidence)
            )
        },
    )
    if not evidence:
        return _fallback_chatbot_result(
            message=message,
            retrieval_query=retrieval_query,
            evidence=retrieved_evidence,
            warnings=warnings,
            diagnostic_code="semantic_filter_empty",
            started=started,
            external_result_count=external_count,
            prompt_tokens=(
                planning.prompt_tokens + semantic_prompt_tokens + coverage_prompt_tokens
            ),
            completion_tokens=(
                planning.completion_tokens + semantic_completion_tokens + coverage_completion_tokens
            ),
            figure_analysis_requested=analyze_figures,
            answer_effort=answer_effort,
            timings=timings.models(),
            retrieval_traces=retrieval_traces.models(),
        )
    coverage_notes = _coverage_notes(planning.plan.axes, coverage)
    if coverage_notes:
        warnings.append(
            "La couverture documentaire reste partielle pour : "
            + ", ".join(
                axis.label
                for axis, assessment in zip(planning.plan.axes, coverage.axes, strict=True)
                if assessment.status != "covered"
            )
            + "."
        )

    figure_analysis_count = 0
    figure_analysis_duration = 0.0
    figure_analysis_model: str | None = None
    if analyze_figures:
        figure_started = perf_counter()
        figure_memory = timings.snapshot()

        def publish_figure_analysis() -> None:
            publish_progress("figure_analysis")
            if on_figure_analysis is not None:
                on_figure_analysis()

        try:
            with OllamaFigureAnalysisService(settings) as figure_service:
                figure_batch = figure_service.analyze(
                    retrieval_query,
                    figure_references_from_chat_records(evidence),
                    on_analysis_started=publish_figure_analysis,
                )
        except FigureAnalysisUnavailable as exc:
            warnings.append(str(exc))
        except Exception as exc:
            warnings.append(
                "L’analyse locale des figures est indisponible "
                f"({type(exc).__name__}); la réponse reste fondée sur le texte."
            )
        else:
            evidence = attach_figure_evidence(evidence, figure_batch.admitted)
            warnings.extend(figure_batch.warnings)
            figure_analysis_count = len(figure_batch.admitted)
            figure_analysis_duration = figure_batch.duration_seconds
            figure_analysis_model = figure_batch.model_name
        finally:
            timings.add(
                "figure_analysis",
                perf_counter() - figure_started,
                before=figure_memory,
            )

    retrieval_traces.add(
        "llm_context",
        selected_article_count=len(evidence),
        selected_passage_count=sum(len(record.passages) for record in evidence),
    )

    publish_progress("generation")
    generation_started = perf_counter()
    generation_memory = timings.snapshot()
    try:
        with ArgoClient(settings) as llm:
            rag = _evidence_rag_service(
                llm,
                answer_effort,
                settings.argo.scientific_correction_temperature,
            )
            rag.experimental_profile = active_experimental_profile
            if planning.plan.requires_faceted_answer:
                answer = rag.answer_faceted(
                    message,
                    evidence,
                    facets=intent.facets,
                    conversation_history=context,
                    coverage_notes=coverage_notes,
                    concept_definition=(
                        planning.plan.concept_definition or planning.plan.interpreted_question
                    ),
                    ambiguities=planning.plan.ambiguities,
                    excluded_concepts=planning.plan.excluded_concepts,
                    on_argo_reserved=on_argo_reserved,
                    on_argo_response=on_argo_response,
                )
            else:
                answer = rag.answer(
                    message,
                    evidence,
                    conversation_history=context,
                    coverage_notes=coverage_notes,
                    concept_definition=(
                        planning.plan.concept_definition or planning.plan.interpreted_question
                    ),
                    ambiguities=planning.plan.ambiguities,
                    excluded_concepts=planning.plan.excluded_concepts,
                    on_argo_reserved=on_argo_reserved,
                    on_argo_response=on_argo_response,
                )
    except ArgoQuotaError:
        timings.add(
            "argo_generation",
            perf_counter() - generation_started,
            before=generation_memory,
        )
        raise
    except ArgoError as exc:
        timings.add(
            "argo_generation",
            perf_counter() - generation_started,
            before=generation_memory,
        )
        return _fallback_chatbot_result(
            message=message,
            retrieval_query=retrieval_query,
            evidence=evidence,
            warnings=warnings,
            diagnostic_code=_argo_diagnostic_code(exc),
            started=started,
            external_result_count=external_count,
            prompt_tokens=(
                planning.prompt_tokens + semantic_prompt_tokens + coverage_prompt_tokens
            ),
            completion_tokens=(
                planning.completion_tokens + semantic_completion_tokens + coverage_completion_tokens
            ),
            figure_analysis_requested=analyze_figures,
            figure_analysis_count=figure_analysis_count,
            figure_analysis_duration_seconds=figure_analysis_duration,
            figure_analysis_model=figure_analysis_model,
            answer_effort=answer_effort,
            timings=timings.models(),
            retrieval_traces=retrieval_traces.models(),
        )
    timings.add(
        "argo_generation",
        perf_counter() - generation_started,
        before=generation_memory,
    )
    timings.add_tokens(
        "argo_generation",
        prompt_tokens=answer.prompt_tokens,
        completion_tokens=answer.completion_tokens,
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
        prompt_tokens=(
            planning.prompt_tokens
            + semantic_prompt_tokens
            + coverage_prompt_tokens
            + answer.prompt_tokens
        ),
        completion_tokens=(
            planning.completion_tokens
            + semantic_completion_tokens
            + coverage_completion_tokens
            + answer.completion_tokens
        ),
        duration_seconds=perf_counter() - started,
        interaction_mode="research",
        reused_previous_sources=False,
        facet_drafts=getattr(answer, "facet_drafts", []),
        figure_analysis_requested=analyze_figures,
        figure_analysis_count=figure_analysis_count,
        figure_analysis_duration_seconds=figure_analysis_duration,
        figure_analysis_model=figure_analysis_model,
        generation_status=getattr(answer, "generation_status", "generated"),
        answer_effort=answer_effort,
        timings=timings.models(),
        retrieval_traces=retrieval_traces.models(),
        generation_traces=getattr(answer, "generation_traces", []),
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
        index_manifest = prepare_index_generation_mutation(index)
        deleted_points = index.delete_points(chunk_ids)
        deleted_queries = database.delete_article(article_id)
        if index_manifest is not None:
            write_ready_index_generation_manifest(
                database,
                index,
                generation_id=index_manifest.generation_id,
                created_at=index_manifest.created_at,
            )
    finally:
        index.close()
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
        index_manifest = prepare_index_generation_mutation(index)
        index.delete_points(chunk_ids)
    finally:
        index.close()
    database.reset_article_for_reindex(article_id)
    return index_pending_chunks(
        settings,
        database,
        article_ids=[article_id],
        retry_failed=True,
        _manifest_is_building=index_manifest is not None,
    )


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
