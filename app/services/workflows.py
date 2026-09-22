"""Framework-agnostic application workflows shared by API and scripts."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, BinaryIO, Literal

from app.chat_effort import AnswerEffort, AnswerEffortBudget, answer_effort_budget
from app.config import Settings
from app.corpora import CorpusScope, corpus_paths, corpus_scope_label, settings_for_corpus
from app.database.sqlite import Database
from app.deep_research.query_variants import (
    QueryVariant,
    build_bilingual_variants,
    query_variant_weight,
    variant_matches_text,
)
from app.desktop.model_integrity import MODEL_MANIFEST
from app.ingestion.embeddings import (
    EmbeddingBatchProcessor,
    EmbeddingRunReport,
    SentenceTransformerBackend,
    local_model_path,
)
from app.ingestion.pdf_extractor import PdfExtractor
from app.ingestion.pipeline import IngestionPipeline, IngestionReport
from app.knowledge.wiki import ReasoningWikiError, load_reasoning_wiki
from app.llm.argo_client import (
    ArgoAuthenticationError,
    ArgoAuthorizationError,
    ArgoClient,
    ArgoError,
    ArgoGenerationError,
    ArgoProtocolError,
    ArgoQuotaError,
    ArgoScientificValidationError,
    ScientificValidationReason,
)
from app.llm.article_evidence import ArticleEvidenceExtractor, EvidencePassageSelector
from app.llm.chat_claims import ChatAnswerVerifier, MandatoryVerificationError
from app.llm.claim_verification import ClaimVerifier
from app.llm.figure_analysis import (
    FigureAnalysisUnavailable,
    OllamaFigureAnalysisService,
    figure_references_from_chat_records,
)
from app.llm.final_synthesis import (
    HierarchicalSynthesisService,
    SynthesisExecutionResult,
)
from app.llm.providers import LlmProviderStore, active_llm_model
from app.llm.response_language import question_language
from app.llm.response_style import ResponseStyle, detect_response_style
from app.llm.validation_cache import ValidationCache, client_identity, content_key
from app.memory import MemoryGuard, MemoryLimitError, MemorySnapshot
from app.models.chatbot import (
    ChatbotResult,
    ChatbotRetrievalTrace,
    ChatbotSource,
    ChatbotTiming,
    ChatEvidencePassage,
    ChatEvidenceRecord,
    ScientificGenerationTrace,
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
from app.retrieval.chat_checkpoint import ChatRetrievalCheckpoint
from app.retrieval.evidence_selection import distinct_evidence
from app.retrieval.global_semantic_filter import (
    ArgoGlobalSemanticEvidenceFilter,
    GlobalSemanticFilterResult,
)
from app.retrieval.hybrid_search import HybridChunkResult, HybridSearchService
from app.retrieval.hypothesis_planning import (
    ArgoHypothesisPlanningService as ArgoQueryPlanningService,
)
from app.retrieval.hypothesis_planning import (
    HypothesisPlanningResult,
    coerce_hypothesis_planning_result,
    deterministic_hypothesis_plan,
)
from app.retrieval.index_manifest import (
    assert_index_generation_mutable,
    prepare_index_generation_mutation,
    resume_index_generation,
    write_ready_index_generation_manifest,
)
from app.retrieval.lexical_search import LexicalSearchService
from app.retrieval.query_planning import (
    QueryPlanningProtocolError,
)
from app.retrieval.query_scope import classify_query_scope
from app.retrieval.rehydration import rehydrate_records
from app.retrieval.reranker import (
    MultilingualReranker,
    RerankerCandidate,
    local_reranker_model_path,
)
from app.retrieval.retrieval_cache import RetrievalCacheSignature, RetrievalResultCache
from app.retrieval.scientific_intent import (
    ScientificIntent,
    analyze_scientific_intent,
    score_scientific_text,
)
from app.retrieval.vector_search import QdrantLocalIndex, VectorSearchService
from app.services.argo_quota import ArgoQuotaService
from app.services.chatbot import (
    chatbot_sources_from_evidence,
    contextualize_retrieval_query,
    conversation_context,
    merge_chatbot_candidates,
)
from app.telemetry import measured, timing_scope
from app.updates.full_text import FullTextHarvestService
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicSearchReport
from app.updates.models import verified_normalized_doi as _verified_normalized_doi
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
ChatbotRetrievalCheckpointCallback = Callable[[ChatRetrievalCheckpoint], None]
SAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
BIBTEX_KEY = re.compile(r"[^A-Za-z0-9_:-]+")
_LOCAL_CHAT_RETRIEVAL_LOCK = threading.Lock()
LOGGER = logging.getLogger("ciderscholar.services.workflows")
# Loading the local encoder and opening the embedded Qdrant collection needs a
# material margin, but must remain usable with the 8 GB desktop profile.  The
# process-level guard still runs around the actual vector operations.
_DENSE_CHAT_MINIMUM_AVAILABLE_GB = 4.0


def _dense_chat_retrieval_is_safe(settings: Settings) -> bool:
    """Keep a local semantic search from exhausting an interactive desktop.

    E5 plus the embedded Qdrant generation can temporarily require substantially
    more memory than either component's on-disk size.  Lexical retrieval remains
    SQLite-authoritative and is a valid explicit fallback; it is preferable to
    letting the process starve the desktop before it can return a cited answer.
    """

    snapshot = MemoryGuard(settings.memory).snapshot()
    return snapshot is None or snapshot.system_available_gb >= _DENSE_CHAT_MINIMUM_AVAILABLE_GB


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

    def restore(self, models: Sequence[ChatbotTiming]) -> None:
        """Restore a completed pre-semantic attempt without re-running its work."""

        for model in models:
            self._values[model.stage] = {
                "duration_seconds": model.duration_seconds,
                "count": model.count,
                "prompt_tokens": model.prompt_tokens,
                "completion_tokens": model.completion_tokens,
                "before": (
                    MemorySnapshot(
                        process_rss_gb=model.process_rss_before_gb,
                        system_used_gb=model.system_used_before_gb,
                        system_available_gb=model.system_available_before_gb,
                    )
                    if model.process_rss_before_gb is not None
                    and model.system_used_before_gb is not None
                    and model.system_available_before_gb is not None
                    else None
                ),
                "after": (
                    MemorySnapshot(
                        process_rss_gb=model.process_rss_after_gb,
                        system_used_gb=model.system_used_after_gb,
                        system_available_gb=model.system_available_after_gb,
                    )
                    if model.process_rss_after_gb is not None
                    and model.system_used_after_gb is not None
                    and model.system_available_after_gb is not None
                    else None
                ),
            }

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
        "vector_query_count",
        "cache_hit_count",
        "cache_miss_count",
        "lexical_candidate_count",
        "dense_candidate_count",
        "dense_article_prefilter_article_count",
        "dense_global_query_count",
        "rrf_unique_candidate_count",
        "fused_candidate_count",
        "pre_rerank_candidate_count",
        "post_rerank_candidate_count",
        "selected_article_count",
        "selected_passage_count",
        "selected_full_text_article_count",
        "selected_full_text_passage_count",
        "selected_abstract_article_count",
        "selected_abstract_passage_count",
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

    def restore(self, models: Sequence[ChatbotRetrievalTrace]) -> None:
        """Restore completed retrieval traces exactly once for a resumed attempt."""

        self._values = {model.stage: model for model in models}


@measured("corpus_revision_read")
def _chat_retrieval_corpus_fingerprint(settings: Settings) -> str:
    """Hash retrieval-authoritative identities and revisions without copying source text."""

    path = corpus_paths(settings, CorpusScope.COMMON).database_path.resolve()
    digest = hashlib.sha256(b"ciderscholar-chat-retrieval-corpus-v1\0")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            revision = connection.execute(
                "SELECT revision, token FROM retrieval_revision WHERE id=1"
            ).fetchone()
            if revision is None:
                raise RuntimeError("corpus revision is missing")
            digest.update(str(path).encode())
            digest.update(json.dumps(tuple(revision)).encode())
    except sqlite3.Error as exc:
        raise RuntimeError("chat retrieval cache cannot fingerprint common SQLite") from exc
    return digest.hexdigest()


def _manifest_sha256(path: Path) -> str | None:
    manifest = path / MODEL_MANIFEST
    return hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None


class _ChatRetrievalResources:
    """Lazily reuse heavy local models during one complete chat answer."""

    def __init__(self) -> None:
        self._embedding_backend: SentenceTransformerBackend | None = None
        self._reranker: MultilingualReranker | None = None
        self._result_cache: RetrievalResultCache | None = None
        self._corpus_fingerprints: dict[str, str] = {}

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

    def result_cache(self, settings: Settings) -> RetrievalResultCache:
        if self._result_cache is None:
            self._result_cache = RetrievalResultCache(
                settings.paths.cache_dir / "chat_retrieval_results"
            )
        return self._result_cache

    def corpus_fingerprint(self, settings: Settings) -> str:
        key = str(corpus_paths(settings, CorpusScope.COMMON).database_path.resolve())
        fingerprint = self._corpus_fingerprints.get(key)
        if fingerprint is None:
            fingerprint = _chat_retrieval_corpus_fingerprint(settings)
            self._corpus_fingerprints[key] = fingerprint
        return fingerprint

    def invalidate_corpus_fingerprint(self) -> None:
        self._corpus_fingerprints.clear()

    @contextmanager
    def qdrant_wave(self, settings: Settings) -> Iterable[QdrantLocalIndex]:
        """Own one lazy Qdrant client shared by every collection used in a wave."""

        scoped_settings = settings_for_corpus(settings, CorpusScope.COMMON)
        owner = QdrantLocalIndex(scoped_settings)
        try:
            yield owner
        finally:
            owner.close()

    def close(self) -> None:
        if self._result_cache is not None:
            self._result_cache.close()
            self._result_cache = None
        self._corpus_fingerprints.clear()
        if self._reranker is not None:
            self._reranker.close()
            self._reranker = None
        if self._embedding_backend is not None:
            self._embedding_backend.close()
            self._embedding_backend = None


def _chat_retrieval_cache_signature(
    settings: Settings,
    resources: _ChatRetrievalResources,
    *,
    operation: str,
    query: str,
    variants: Sequence[str],
    filters_limits: Mapping[str, Any],
) -> RetrievalCacheSignature:
    embedding_path = local_model_path(settings, settings.embeddings.model_name)
    reranker_path = local_reranker_model_path(settings)
    return RetrievalCacheSignature.build(
        query=query,
        variants=tuple(variants),
        corpus_fingerprint=resources.corpus_fingerprint(settings),
        scope=CorpusScope.COMMON.value,
        retrieval_config=settings.retrieval.model_dump(mode="json"),
        embedding={
            "config": settings.embeddings.model_dump(mode="json"),
            "manifest_sha256": _manifest_sha256(embedding_path),
        },
        reranker={
            "config": settings.reranker.model_dump(mode="json"),
            "manifest_sha256": _manifest_sha256(reranker_path),
        },
        filters_limits={
            "pipeline_version": "rag-v3-conservative-evidence-selection",
            "article_ranking": settings.article_ranking.model_dump(mode="json"),
            "evidence": settings.evidence.model_dump(mode="json"),
            "operation": operation,
            **dict(filters_limits),
        },
    )


def _evidence_rag_service(
    client: Any,
    answer_effort: AnswerEffort,
    correction_temperature: float,
    max_input_characters: int,
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
    if supports_kwargs or any(parameter.name == "max_input_characters" for parameter in parameters):
        options["max_input_characters"] = max_input_characters
    return CiderEvidenceRagService(client, **options)


def _chat_llm_client(settings: Settings, request_timeout_seconds: float) -> ArgoClient:
    """Keep injected legacy clients compatible while bounding real HTTP calls."""

    parameters = inspect.signature(ArgoClient).parameters.values()
    supports_timeout = any(
        parameter.name == "request_timeout_seconds"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    options = (
        {"request_timeout_seconds": max(1.0, request_timeout_seconds)} if supports_timeout else {}
    )
    return ArgoClient(settings, **options)


def _argo_generation_slot_available(settings: Settings) -> bool:
    """Avoid expensive retrieval when ARGO cannot accept even one generation request."""

    if LlmProviderStore(settings).active_provider() != "argo":
        return True
    return ArgoQuotaService(Database(settings.paths.database_path)).has_capacity()


@contextmanager
def _serialized_chat_retrieval_scope(
    *,
    timing: _ChatTimingCollector | None = None,
) -> Iterable[None]:
    """Acquire the local retrieval lock once for a coherent multi-stage wave."""

    wait_started = perf_counter()
    wait_memory = timing.snapshot() if timing is not None else None
    with _LOCAL_CHAT_RETRIEVAL_LOCK:
        if timing is not None:
            timing.add(
                "retrieval_lock_wait",
                perf_counter() - wait_started,
                before=wait_memory,
            )
        yield


def _timed_chat_retrieval_operation(
    operation: Callable[..., Any],
    *args: Any,
    timing: _ChatTimingCollector | None = None,
    timing_stage: str = "local_retrieval",
    **kwargs: Any,
) -> Any:
    """Measure one operation inside an already acquired retrieval scope."""

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


def _initial_retrieval_candidate_limit(
    settings: Settings,
    effort_budget: AnswerEffortBudget,
) -> int:
    """Size the exact first wave from its requested output, not a generic wide default."""

    return min(
        settings.retrieval.hybrid_candidate_limit,
        max(40, effort_budget.abstract_result_limit * 3),
    )


def _full_text_intermediate_pool_sizes(article_count: int) -> tuple[int, int]:
    """Keep a diverse preselection while bounding costly cold-start reranking."""

    candidate_articles = min(max(article_count * 3, 18), 60)
    axis_candidates = min(max(article_count + 4, 10), 24)
    return candidate_articles, axis_candidates


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
    qdrant_client_owner: QdrantLocalIndex | None = None,
) -> EmbeddingRunReport:
    index = QdrantLocalIndex(settings, client_owner=qdrant_client_owner)
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


def _abstract_search_failure_code(stage: str, error: Exception) -> str:
    if isinstance(error, sqlite3.Error):
        category = "sqlite"
    elif isinstance(error, ValueError):
        category = "invalid_data"
    elif isinstance(error, OSError):
        category = "resource"
    else:
        category = "unexpected"
    return f"abstract_{stage}_{category}"[:80]


def _evidence_level_trace_counts(
    evidence: Sequence[ChatEvidenceRecord],
) -> dict[str, int]:
    full_text = [record for record in evidence if record.evidence_level == "full_text"]
    abstracts = [record for record in evidence if record.evidence_level == "abstract"]
    return {
        "selected_full_text_article_count": len(full_text),
        "selected_full_text_passage_count": sum(len(record.passages) for record in full_text),
        "selected_abstract_article_count": len(abstracts),
        "selected_abstract_passage_count": sum(len(record.passages) for record in abstracts),
    }


def _abstract_route_warning(
    question: str,
    *,
    diagnostics: Sequence[str],
    abstract_result_count: int,
    full_text_records: Sequence[ChatEvidenceRecord],
    supplemental: bool = False,
) -> str | None:
    """Describe an abstract degradation without obscuring successful full-text retrieval."""

    if not diagnostics:
        return None
    full_text_articles = len(full_text_records)
    full_text_passages = sum(len(record.passages) for record in full_text_records)
    scope_fr = "complémentaire " if supplemental else ""
    scope_en = "supplemental " if supplemental else ""
    if question_language(question) == "fr":
        return (
            f"La voie {scope_fr}des résumés bibliographiques a été partiellement dégradée "
            f"({abstract_result_count} résultat(s) valide(s) conservé(s)). La recherche distincte "
            f"dans les textes intégraux a néanmoins retenu {full_text_articles} article(s) et "
            f"{full_text_passages} passage(s) avant la fusion des preuves."
        )
    return (
        f"The {scope_en}bibliographic-abstract route was partially degraded "
        f"({abstract_result_count} valid result(s) retained). The separate full-text search still "
        f"retained {full_text_articles} article(s) and {full_text_passages} passage(s) before "
        "evidence merging."
    )


def search_common_corpus_abstracts(
    settings: Settings,
    *,
    query: str,
    limit: int = 15,
    search_queries: Sequence[str] = (),
    dense_queries: Sequence[str] | None = None,
    intent_override: ScientificIntent | None = None,
    max_query_variants: int | None = None,
    max_vector_query_variants: int | None = None,
    candidate_limit: int | None = None,
    prefix_matching: bool | None = None,
    retrieval_resources: _ChatRetrievalResources | None = None,
    retrieval_trace: _ChatRetrievalTraceCollector | None = None,
    qdrant_client_owner: QdrantLocalIndex | None = None,
    supplemental: bool = False,
    diagnostics: list[str] | None = None,
) -> list[BibliographicHybridResult]:
    """Search full articles and verified abstract-only records in the common corpus."""

    if not query.strip():
        raise ValueError("common corpus abstract query cannot be empty")
    if not 1 <= limit <= 100:
        raise ValueError("common corpus abstract limit must be between 1 and 100")
    scoped_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    if dense_queries is not None:
        scoped_settings = scoped_settings.model_copy(
            update={
                "retrieval": scoped_settings.retrieval.model_copy(
                    update={
                        "hybrid_max_query_variants": max(
                            scoped_settings.retrieval.hybrid_max_query_variants,
                            max_query_variants or 1,
                        )
                    }
                )
            }
        )
    database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)
    query_limit = max_query_variants or scoped_settings.retrieval.hybrid_max_query_variants
    if not 1 <= query_limit <= scoped_settings.retrieval.hybrid_max_query_variants:
        raise ValueError("abstract query variant limit is outside configured bounds")
    if max_vector_query_variants is not None and not 0 <= max_vector_query_variants <= query_limit:
        raise ValueError("abstract vector query variant limit is outside configured bounds")
    queries = list(
        dict.fromkeys(" ".join(item.split()) for item in [query, *search_queries] if item.strip())
    )[:query_limit]
    trace_stage = "supplemental_abstract_search" if supplemental else "abstract_search"
    failure_counts: dict[str, int] = {}

    def record_failure(stage: str, error: Exception) -> None:
        code = _abstract_search_failure_code(stage, error)
        failure_counts[code] = failure_counts.get(code, 0) + 1
        if diagnostics is not None and code not in diagnostics:
            diagnostics.append(code)
        LOGGER.warning(
            "abstract_search_degraded stage=%s error_type=%s",
            stage,
            type(error).__name__,
        )

    cache_signature: RetrievalCacheSignature | None = None
    if retrieval_resources is not None:
        try:
            cache_signature = _chat_retrieval_cache_signature(
                scoped_settings,
                retrieval_resources,
                operation=trace_stage,
                query=query,
                variants=queries,
                filters_limits={
                    "limit": limit,
                    "max_vector_query_variants": max_vector_query_variants,
                    "dense_queries": list(dense_queries) if dense_queries is not None else None,
                    "candidate_limit": candidate_limit,
                    "prefix_matching": prefix_matching,
                    "intent": (
                        intent_override.model_dump(mode="json")
                        if intent_override is not None
                        else None
                    ),
                },
            )
            cached = retrieval_resources.result_cache(scoped_settings).get_typed(
                cache_signature,
                list[BibliographicHybridResult],
            )
        except Exception as exc:
            record_failure("cache", exc)
            cache_signature = None
        else:
            if cached is not None:
                hydrated = rehydrate_records(settings, abstract_candidates_to_chat_evidence(cached))
                by_id = {record.record_id: record for record in hydrated}
                cached = (
                    [
                        record.model_copy(
                            update={
                                "abstract": by_id[record.record_id].passages[0].text,
                                "title": by_id[record.record_id].title,
                                "authors": by_id[record.record_id].authors,
                                "doi": by_id[record.record_id].doi,
                                "journal": by_id[record.record_id].journal,
                                "publication_year": by_id[record.record_id].publication_year,
                            }
                        )
                        for record in cached
                    ]
                    if len(by_id) == len(cached)
                    else None
                )
            if cached is not None:
                if retrieval_trace is not None:
                    retrieval_trace.add(
                        trace_stage,
                        query_variant_count=len(queries),
                        cache_hit_count=1,
                        selected_article_count=len(cached),
                        selected_abstract_article_count=len(cached),
                    )
                return cached
            if retrieval_trace is not None:
                retrieval_trace.add(trace_stage, cache_miss_count=1)
    title_rows = []
    seen_title_ids: set[str] = set()
    for search_query in queries:
        try:
            matched_title_rows = database.article_abstracts_by_title(search_query, limit=limit)
        except Exception as exc:
            record_failure("title_query", exc)
            continue
        for row in matched_title_rows:
            article_id = str(row["id"])
            if article_id in seen_title_ids:
                continue
            seen_title_ids.add(article_id)
            title_rows.append(row)
    lexical_retrieval_limit = (
        min(max(limit * 10, 50), 500)
        if candidate_limit is None
        else min(max(candidate_limit, limit), 500)
    )
    maximum_query_length = scoped_settings.retrieval.lexical_max_query_characters
    lexical_service = LexicalSearchService(scoped_settings, database)
    lexical_by_article: dict[str, Any] = {}
    lexical_scores: dict[str, float] = {}
    lexical_candidate_count = 0
    try:
        with lexical_service.read_session() as lexical_session:
            for search_query in queries:
                try:
                    lexical = lexical_session.search(
                        expand_cider_query(search_query)[:maximum_query_length],
                        limit=lexical_retrieval_limit,
                        mode="any",
                        prefix_matching=prefix_matching,
                    )
                except Exception as exc:
                    record_failure("article_fts_query", exc)
                    continue
                lexical_candidate_count += len(lexical.results)
                for result in lexical.results:
                    lexical_by_article.setdefault(result.article_id, result)
                    lexical_scores[result.article_id] = lexical_scores.get(
                        result.article_id,
                        0.0,
                    ) + 1.0 / (60.0 + result.rank)
    except Exception as exc:
        record_failure("article_fts_session", exc)
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
    try:
        rows.update(database.article_details_by_ids(ordered_ids))
    except Exception as exc:
        record_failure("article_details", exc)
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
        try:
            article_results.append(
                BibliographicHybridResult(
                    rank=len(article_results) + 1,
                    record_id=f"common:{article_id}",
                    title=str(row["title"]),
                    abstract=str(row["abstract"]),
                    authors=[str(author) for author in authors],
                    journal=str(row["journal"]) if row["journal"] else None,
                    publication_year=(
                        int(row["publication_year"])
                        if row["publication_year"] is not None
                        else None
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
        except (TypeError, ValueError) as exc:
            record_failure("article_conversion", exc)
            continue
        if len(article_results) >= min(max(limit * 2, 30), 100):
            break

    abstract_results: list[BibliographicHybridResult] = []
    abstract_lexical_candidates = 0
    abstract_dense_candidates = 0
    abstract_rrf_candidates = 0
    abstract_vector_query_count = 0
    abstract_vector_search_degraded = False
    unverified_abstract_count = 0
    abstract_store = BibliographicHarvestStore(database)
    try:
        has_harvested_abstracts = bool(abstract_store.statistics()["abstracts"])
    except Exception as exc:
        record_failure("harvest_statistics", exc)
        has_harvested_abstracts = False
    if has_harvested_abstracts:
        service: BibliographicHybridSearchService | None = None
        try:
            backend = (
                retrieval_resources.embedding_backend(scoped_settings)
                if retrieval_resources is not None
                else SentenceTransformerBackend(scoped_settings)
            )
            service = BibliographicHybridSearchService(
                scoped_settings,
                abstract_store,
                backend,
                BibliographicVectorIndex(
                    scoped_settings,
                    qdrant_client_owner=qdrant_client_owner,
                ),
            )
        except Exception as exc:
            record_failure("hybrid_setup", exc)
        if service is not None:
            collected_abstracts: dict[str, BibliographicHybridResult] = {}
            vector_query_limit = (
                len(queries) if max_vector_query_variants is None else max_vector_query_variants
            )
            response_entries: list[tuple[int, BibliographicHybridResponse]] = []
            hybrid_limit = min(max(limit * 2, 30), 60)
            hybrid_candidate_limit = (
                min(lexical_retrieval_limit, 200) if candidate_limit is not None else None
            )
            try:
                response_entries = list(
                    enumerate(
                        service.search_many(
                            queries,
                            **(
                                {"dense_queries": dense_queries}
                                if dense_queries is not None
                                else {}
                            ),
                            limit=hybrid_limit,
                            vector_query_limit=vector_query_limit,
                            prefix_matching=prefix_matching,
                            candidate_limit=hybrid_candidate_limit,
                        )
                    )
                )
            except Exception as exc:
                record_failure("grouped_hybrid", exc)
                for query_index, search_query in enumerate(queries):
                    try:
                        response_entries.append(
                            (
                                query_index,
                                service.search(
                                    search_query,
                                    limit=hybrid_limit,
                                    vector_enabled=query_index < vector_query_limit,
                                    prefix_matching=prefix_matching,
                                    candidate_limit=hybrid_candidate_limit,
                                ),
                            )
                        )
                    except Exception as query_exc:
                        record_failure("hybrid_query", query_exc)
            for query_index, response in response_entries:
                for code in response.degradation_codes:
                    failure_counts[code] = failure_counts.get(code, 0) + 1
                    if diagnostics is not None and code not in diagnostics:
                        diagnostics.append(code)
                abstract_lexical_candidates += response.lexical_candidate_count
                abstract_dense_candidates += response.dense_candidate_count
                abstract_rrf_candidates += response.rrf_unique_candidate_count
                abstract_vector_search_degraded = (
                    abstract_vector_search_degraded or response.vector_search_degraded
                )
                abstract_vector_query_count += int(
                    query_index < vector_query_limit and not response.vector_search_degraded
                )
                for result in response.results:
                    try:
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
                    except (TypeError, ValueError) as exc:
                        record_failure("harvest_conversion", exc)
                        continue
                    current = collected_abstracts.get(key)
                    if current is None or converted.score > current.score:
                        collected_abstracts[key] = converted
            abstract_results = list(collected_abstracts.values())
            try:
                if retrieval_resources is None:
                    service.close()
                else:
                    service.index.close()
            except Exception as exc:
                record_failure("hybrid_close", exc)

    # An article's presence in SQLite does not imply that this search found its
    # abstract or a usable full-text passage. Deduplicate actual candidates here,
    # then compare available full-text/abstract evidence in merge_chat_evidence.
    reranker_input_count = len(article_results) + len(abstract_results)
    reranker_candidates = [*article_results, *abstract_results]
    try:
        ranked = rerank_bibliographic_candidates(
            query,
            reranker_candidates,
            limit=limit,
            intent_override=intent_override,
        )
    except Exception as exc:
        record_failure("reranking", exc)
        ranked = [
            result.model_copy(update={"rank": rank})
            for rank, result in enumerate(
                sorted(reranker_candidates, key=lambda item: (-item.score, item.record_id))[:limit],
                start=1,
            )
        ]
    if retrieval_trace is not None:
        retrieval_trace.add(
            trace_stage,
            query_variant_count=len(queries),
            vector_query_count=abstract_vector_query_count,
            vector_search_degraded=abstract_vector_search_degraded,
            lexical_candidate_count=(lexical_candidate_count + abstract_lexical_candidates),
            dense_candidate_count=abstract_dense_candidates,
            rrf_unique_candidate_count=abstract_rrf_candidates,
            fused_candidate_count=len(article_results) + len(abstract_results),
            pre_rerank_candidate_count=reranker_input_count,
            post_rerank_candidate_count=len(ranked),
            selected_article_count=len(ranked),
            selected_abstract_article_count=len(ranked),
            rejection_counts={
                "abstract_without_verified_doi": unverified_abstract_count,
                "not_selected_after_reranking": max(0, reranker_input_count - len(ranked)),
                **failure_counts,
            },
        )
    if retrieval_resources is not None and cache_signature is not None:
        with suppress(Exception):
            retrieval_resources.result_cache(scoped_settings).put(
                cache_signature,
                [result.model_dump(mode="json") for result in ranked],
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
    candidate_limit: int | None = None,
    prefix_matching: bool | None = None,
) -> ArticleRankingResponse:
    """Keep a deterministic fallback for tests or a missing local vector model."""

    resolved_candidate_limit = max(candidate_limit or 120, article_count * 12)
    lexical_service = LexicalSearchService(settings, database)
    fused: dict[tuple[str, int], dict[str, Any]] = {}
    lexical_candidate_count = 0
    with lexical_service.read_session() as lexical_session:
        for variant_index, variant in enumerate(variants):
            lexical = lexical_session.search(
                variant.text[: settings.retrieval.lexical_max_query_characters],
                limit=resolved_candidate_limit,
                mode="any",
                prefix_matching=prefix_matching,
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
                contribution = query_variant_weight(variant) / (
                    settings.retrieval.rrf_k + result.rank
                )
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
    dense_queries: Sequence[str] | None = None,
    axis_queries: Mapping[str, Sequence[str]] | None = None,
    intent_override: ScientificIntent | None = None,
    max_query_variants: int | None = None,
    max_vector_query_variants: int | None = None,
    candidate_limit: int | None = None,
    prefix_matching: bool | None = None,
    include_fallback_variants: bool = True,
    passage_count: int | None = None,
    candidate_chunks_per_article: int | None = None,
    context_radius: int = 1,
    retrieval_resources: _ChatRetrievalResources | None = None,
    retrieval_trace: _ChatRetrievalTraceCollector | None = None,
    qdrant_client_owner: QdrantLocalIndex | None = None,
    supplemental: bool = False,
) -> list[ChatEvidenceRecord]:
    """Hybrid-search full articles, causally rerank them, then hydrate passages."""

    if not query.strip():
        raise ValueError("common corpus full-text query cannot be empty")
    if not 1 <= article_count <= 20:
        raise ValueError("chat full-text article count must be between 1 and 20")
    scoped_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    if dense_queries is not None:
        scoped_settings = scoped_settings.model_copy(
            update={
                "retrieval": scoped_settings.retrieval.model_copy(
                    update={
                        "hybrid_max_query_variants": max(
                            scoped_settings.retrieval.hybrid_max_query_variants,
                            max_query_variants or 1,
                        )
                    }
                )
            }
        )
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
    if (
        max_vector_query_variants is not None
        and not 0 <= max_vector_query_variants <= maximum_variant_count
    ):
        raise ValueError("full-text vector query variant limit is outside configured bounds")
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
    if include_fallback_variants:
        for fallback_variant in fallback_variants[1:]:
            if len(variants) >= maximum_variant_count:
                break
            if fallback_variant.text.casefold() in known_variant_texts:
                continue
            variants.append(fallback_variant)
            known_variant_texts.add(fallback_variant.text.casefold())
    candidate_article_count, axis_candidate_count = _full_text_intermediate_pool_sizes(
        article_count
    )
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
    trace_stage = "supplemental_full_text_search" if supplemental else "full_text_search"
    cache_signature: RetrievalCacheSignature | None = None
    if retrieval_resources is not None:
        try:
            cache_signature = _chat_retrieval_cache_signature(
                scoped_settings,
                retrieval_resources,
                operation=trace_stage,
                query=query,
                variants=[
                    *(variant.text for variant in variants),
                    *(
                        f"{axis_key}:{axis_query}"
                        for axis_key, queries in cleaned_axis_queries.items()
                        for axis_query in queries
                    ),
                ],
                filters_limits={
                    "article_count": article_count,
                    "article_ids": list(article_ids or ()),
                    "max_query_variants": maximum_variant_count,
                    "max_vector_query_variants": max_vector_query_variants,
                    "dense_queries": list(dense_queries) if dense_queries is not None else None,
                    "candidate_limit": candidate_limit,
                    "prefix_matching": prefix_matching,
                    "include_fallback_variants": include_fallback_variants,
                    "passage_count": passage_count,
                    "candidate_chunks_per_article": candidate_chunks_per_article,
                    "context_radius": context_radius,
                    "intent": intent.model_dump(mode="json"),
                },
            )
            cached = retrieval_resources.result_cache(scoped_settings).get_typed(
                cache_signature,
                list[ChatEvidenceRecord],
            )
        except Exception:
            cache_signature = None
        else:
            if cached is not None:
                hydrated = rehydrate_records(settings, cached)
                cached = hydrated if len(hydrated) == len(cached) else None
            if cached is not None:
                if retrieval_trace is not None:
                    retrieval_trace.add(
                        trace_stage,
                        query_variant_count=len(variants),
                        cache_hit_count=1,
                        selected_article_count=len(cached),
                        selected_passage_count=sum(len(record.passages) for record in cached),
                        **_evidence_level_trace_counts(cached),
                    )
                return cached
            if retrieval_trace is not None:
                retrieval_trace.add(trace_stage, cache_miss_count=1)
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
                    QdrantLocalIndex(
                        scoped_settings,
                        client_owner=qdrant_client_owner,
                    ),
                    close_backend=retrieval_resources is None,
                ),
            ),
        )
        try:
            ranking = ranking_service.search(
                query,
                **({"dense_queries": dense_queries} if dense_queries is not None else {}),
                query_variants=[variant.text for variant in variants],
                article_count=candidate_article_count,
                diversity_mode="none",
                central_concepts=intent.central_concepts() or None,
                article_ids=article_ids,
                max_vector_query_variants=max_vector_query_variants,
                candidate_limit=candidate_limit,
                prefix_matching=prefix_matching,
            )
            for axis_key, queries in cleaned_axis_queries.items():
                axis_query = queries[0]
                axis_ranking = ranking_service.search(
                    axis_query,
                    query_variants=queries,
                    article_count=axis_candidate_count,
                    diversity_mode="none",
                    article_ids=article_ids,
                    max_vector_query_variants=0,
                    candidate_limit=candidate_limit,
                    prefix_matching=prefix_matching,
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
                        vector_query_count=axis_ranking.vector_query_count,
                        lexical_candidate_count=axis_ranking.lexical_candidate_count,
                        dense_candidate_count=axis_ranking.dense_candidate_count,
                        dense_article_prefilter_article_count=(
                            axis_ranking.dense_article_prefilter_article_count
                        ),
                        dense_global_query_count=axis_ranking.dense_global_query_count,
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
            candidate_limit=candidate_limit,
            prefix_matching=prefix_matching,
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
                candidate_limit=candidate_limit,
                prefix_matching=prefix_matching,
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
                    vector_query_count=axis_ranking.vector_query_count,
                    lexical_candidate_count=axis_ranking.lexical_candidate_count,
                    dense_candidate_count=axis_ranking.dense_candidate_count,
                    dense_article_prefilter_article_count=(
                        axis_ranking.dense_article_prefilter_article_count
                    ),
                    dense_global_query_count=axis_ranking.dense_global_query_count,
                    rrf_unique_candidate_count=axis_ranking.rrf_unique_candidate_count,
                    fused_candidate_count=axis_ranking.hybrid_candidate_count,
                    selected_article_count=axis_ranking.selected_article_count,
                    vector_search_degraded=axis_ranking.vector_search_degraded,
                )

    if retrieval_trace is not None:
        retrieval_trace.add(
            trace_stage,
            query_variant_count=ranking.query_variant_count,
            vector_query_count=ranking.vector_query_count,
            lexical_candidate_count=ranking.lexical_candidate_count,
            dense_candidate_count=ranking.dense_candidate_count,
            dense_article_prefilter_article_count=ranking.dense_article_prefilter_article_count,
            dense_global_query_count=ranking.dense_global_query_count,
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
            **_evidence_level_trace_counts(records),
            rejection_counts={
                "not_selected_after_scientific_ranking": max(
                    0, len(assessed_articles) - len(selected_articles[:article_count])
                ),
                "no_passage_selected": max(
                    0, len(selected_articles[:article_count]) - len(records)
                ),
            },
        )
    if retrieval_resources is not None and cache_signature is not None:
        with suppress(Exception):
            retrieval_resources.result_cache(scoped_settings).put(
                cache_signature,
                [record.model_dump(mode="json") for record in records],
            )
    return records


def abstract_candidates_to_chat_evidence(
    records: Sequence[BibliographicHybridResult],
) -> list[ChatEvidenceRecord]:
    """Convert abstract-only candidates without pretending that they are full text."""

    converted: list[ChatEvidenceRecord] = []
    for record in records:
        # Bibliographic providers occasionally return a full document in the
        # abstract field.  Evidence passages are deliberately bounded, so a
        # malformed or unusually long record must not abort the whole answer.
        text = record.abstract.strip()[:12_000]
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

    if not 1 <= limit <= 48:
        raise ValueError("chat evidence limit must be between 1 and 48")
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
    qdrant_client_owner: QdrantLocalIndex | None = None,
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
                qdrant_client_owner=qdrant_client_owner,
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
    answer_effort: AnswerEffort = AnswerEffort.BALANCED,
) -> list[ChatEvidenceRecord]:
    """Rehydrate persisted full-text chunks and keep legacy abstract cards usable."""

    records: list[ChatEvidenceRecord] = []
    databases: dict[CorpusScope, Database] = {}
    budget = answer_effort_budget(answer_effort)
    for source in sources[: budget.evidence_record_limit]:
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
                passage_count=budget.passages_per_article,
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
        if source.origin == "local_rag" and source.scope is not None:
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
                            text="Pending SQLite rehydration",
                        )
                    ],
                )
            )
    return rehydrate_records(settings, records)


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


def _argo_diagnostic_codes(error: ArgoError) -> list[str]:
    if isinstance(error, ArgoScientificValidationError):
        return [reason.value for reason in error.reasons]
    return [_argo_diagnostic_code(error)]


def _argo_failed_generation_usage(
    error: ArgoError,
) -> tuple[int, int, list[ScientificGenerationTrace]]:
    if not isinstance(error, ArgoScientificValidationError):
        return 0, 0, []
    traces = [
        trace for trace in error.generation_traces if isinstance(trace, ScientificGenerationTrace)
    ]
    return error.prompt_tokens, error.completion_tokens, traces


def _generation_quality_warnings(question: str, codes: Sequence[str]) -> list[str]:
    unique = list(dict.fromkeys(code for code in codes if code))
    if not unique:
        return []
    missing_coverage = "missing_required_evidence" in unique
    if question_language(question) == "fr":
        if missing_coverage:
            return [
                "La réponse affichée est scientifiquement sûre mais partielle : certains passages "
                "pertinents retrouvés n'ont pas pu être intégrés après les repasses de correction."
            ]
        return [
            "La réponse affichée conserve un avertissement de forme ou de niveau de détail après "
            "les repasses de correction ; ses affirmations citées restent utilisables."
        ]
    if missing_coverage:
        return [
            "The displayed answer is scientifically safe but partial: some relevant retrieved "
            "passages could not be integrated after the correction passes."
        ]
    return [
        "The displayed answer retains a form or detail warning after the correction passes; its "
        "cited claims remain usable."
    ]


def _query_planning_diagnostic_code(error: ArgoError) -> str:
    """Expose only the stable validation location, never generated plan content."""

    if not isinstance(error, QueryPlanningProtocolError):
        return _argo_diagnostic_code(error)
    diagnostic = error.diagnostic
    parts = ["argo_protocol", diagnostic.category]
    if diagnostic.pydantic_path:
        parts.extend(str(item) for item in diagnostic.pydantic_path)
    if diagnostic.pydantic_type:
        parts.append(diagnostic.pydantic_type)
    return ".".join(parts)[:100]


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
    generation_traces: Sequence[ScientificGenerationTrace] = (),
    diagnostic_codes: Sequence[str] = (),
) -> ChatbotResult:
    """Return the normal answer shape without presenting raw candidates as a synthesis."""

    selected = list(evidence)
    is_french = question_language(message) == "fr"
    abstained = diagnostic_code == "semantic_filter_empty"
    scientific_validation_failed = diagnostic_code in {
        reason.value for reason in ScientificValidationReason
    }
    status: Literal["abstained", "diagnostic_only", "validation_failed"] = (
        "abstained"
        if abstained
        else "validation_failed"
        if scientific_validation_failed
        else "diagnostic_only"
    )
    expected_style = detect_response_style(message)
    context_trace = next(
        (trace for trace in reversed(retrieval_traces) if trace.stage == "llm_context"),
        None,
    )
    full_text_articles = (
        context_trace.selected_full_text_article_count if context_trace is not None else 0
    )
    full_text_passages = (
        context_trace.selected_full_text_passage_count if context_trace is not None else 0
    )
    abstract_articles = (
        context_trace.selected_abstract_article_count if context_trace is not None else 0
    )
    if is_french:
        direct = (
            "Les preuves sélectionnées ne permettent pas d'établir une réponse scientifique "
            "suffisamment étayée."
            if abstained
            else "Aucune réponse scientifique validée ne peut être fournie pour cette exécution."
        )
        limitation = (
            "Les passages pertinents ont été trouvés, mais la synthèse n'a pas satisfait tous "
            "les contrôles scientifiques bloquants après les repasses de correction autorisées. "
            "Les affirmations non validées ne sont pas affichées."
            if scientific_validation_failed
            else "Aucune affirmation n'est présentée, car elle ne pourrait pas être reliée à une "
            "preuve validée. Une nouvelle recherche ou une relance peut être nécessaire."
        )
        no_effect = "Aucun effet directement documenté ne peut être affirmé."
        no_reference = "Aucune référence n'est citée."
        retrieved_not_cited = (
            f"Le contexte de synthèse contenait {full_text_articles} article(s) en texte intégral "
            f"({full_text_passages} passage(s)) et {abstract_articles} notice(s) sur abstract. "
            "Ces documents ont été retrouvés, mais ne sont pas présentés comme références citées "
            "faute de synthèse scientifiquement validée."
            if context_trace is not None
            else ""
        )
    else:
        direct = (
            "The selected evidence does not support a sufficiently grounded scientific answer."
            if abstained
            else "No validated scientific answer can be provided for this execution."
        )
        limitation = (
            "Relevant passages were found, but the synthesis did not satisfy every completeness, "
            "writing, and citation check after the allowed correction passes. Unvalidated claims "
            "are not displayed."
            if scientific_validation_failed
            else "No claim is presented because it could not be linked to validated evidence. "
            "A new search or retry may be required."
        )
        no_effect = "No directly documented effect can be stated."
        no_reference = "No reference is cited."
        retrieved_not_cited = (
            f"The synthesis context contained {full_text_articles} full-text article(s) "
            f"({full_text_passages} passage(s)) and {abstract_articles} abstract-only record(s). "
            "These documents were retrieved but are not presented as cited references because no "
            "scientifically validated synthesis was produced."
            if context_trace is not None
            else ""
        )
    rendered_direct = f"- {direct}" if expected_style is ResponseStyle.BULLET_LIST else direct
    lines = [rendered_direct, no_effect, limitation]
    if retrieved_not_cited:
        lines.append(retrieved_not_cited)
    lines.extend(["Références :" if is_french else "References:", no_reference])
    sources: list[ChatbotSource] = []
    model = "deterministic-structured-fallback"
    return ChatbotResult(
        message=" ".join(message.split()),
        retrieval_query=retrieval_query,
        answer_markdown="\n".join(lines),
        sources=sources,
        # ``limitation`` is already rendered in the answer body. Repeating it
        # in ``warnings`` produces a second, misleading bullet below the answer.
        warnings=list(warnings),
        model=model,
        local_result_count=sum(record.origin == "local_rag" for record in selected),
        external_result_count=external_result_count,
        external_enrichment_used=any(source.origin == "external_api" for source in sources),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        duration_seconds=perf_counter() - started,
        generation_status=status,
        diagnostic_code=diagnostic_code,
        diagnostic_codes=list(diagnostic_codes or [diagnostic_code]),
        interaction_mode=interaction_mode,
        reused_previous_sources=reused_previous_sources,
        figure_analysis_requested=figure_analysis_requested,
        figure_analysis_count=figure_analysis_count,
        figure_analysis_duration_seconds=figure_analysis_duration_seconds,
        figure_analysis_model=figure_analysis_model,
        answer_effort=answer_effort,
        timings=list(timings),
        retrieval_traces=list(retrieval_traces),
        generation_traces=list(generation_traces),
    )


def _out_of_scope_chatbot_result(
    *,
    message: str,
    retrieval_query: str,
    started: float,
    interaction_mode: Literal["research", "conversation"],
    answer_effort: AnswerEffort,
) -> ChatbotResult:
    """Return a user-visible refusal without opening any RAG resource."""

    is_french = question_language(message) == "fr"
    answer = (
        "Cette question est hors du périmètre de CiderScholar. L'assistant traite les "
        "questions scientifiques et techniques liées au cidre, à la pomme, aux produits "
        "cidricoles et aux procédés transférables à cette filière."
        if is_french
        else "This question is outside CiderScholar's scope. The assistant handles scientific "
        "and technical questions about cider, apples, cider-derived products, and processes "
        "transferable to that field."
    )
    limitation = (
        "Aucune recherche dans le corpus n'a été exécutée."
        if is_french
        else "No corpus search was run."
    )
    return ChatbotResult(
        message=" ".join(message.split()),
        retrieval_query=retrieval_query,
        answer_markdown=f"{answer}\n\n{limitation}",
        sources=[],
        warnings=[answer],
        model="deterministic-scope-guard",
        local_result_count=0,
        external_result_count=0,
        external_enrichment_used=False,
        prompt_tokens=0,
        completion_tokens=0,
        duration_seconds=perf_counter() - started,
        generation_status="abstained",
        diagnostic_code="out_of_scope",
        diagnostic_codes=["out_of_scope"],
        interaction_mode=interaction_mode,
        answer_effort=answer_effort,
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
    retrieval_checkpoint: ChatRetrievalCheckpoint | None = None,
    on_retrieval_checkpoint: ChatbotRetrievalCheckpointCallback | None = None,
    experimental_profile: Literal["p0", "p1", "p2"] | None = None,
    answer_effort: AnswerEffort = AnswerEffort.BALANCED,
) -> ChatbotResult:
    """Own and explicitly release heavy resources for one complete chat answer."""

    resources = _ChatRetrievalResources()
    timings = _ChatTimingCollector(settings)
    retrieval_traces = _ChatRetrievalTraceCollector()
    with timing_scope(timings.add):
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
                retrieval_checkpoint=retrieval_checkpoint,
                on_retrieval_checkpoint=on_retrieval_checkpoint,
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
    retrieval_checkpoint: ChatRetrievalCheckpoint | None = None,
    on_retrieval_checkpoint: ChatbotRetrievalCheckpointCallback | None = None,
    experimental_profile: Literal["p0", "p1", "p2"] | None = None,
    answer_effort: AnswerEffort = AnswerEffort.BALANCED,
    retrieval_resources: _ChatRetrievalResources,
    timings: _ChatTimingCollector,
    retrieval_traces: _ChatRetrievalTraceCollector,
) -> ChatbotResult:
    """Run the hypothesis-guided, single-wave, SQLite-authoritative chat pipeline."""

    del database  # The chat database is distinct from the common corpus authority.
    started = perf_counter()
    effort_budget = answer_effort_budget(answer_effort)
    active_experimental_profile = experimental_profile or settings.app.experimental_chat_profile
    context = conversation_context(history)
    retrieval_query = contextualize_retrieval_query(message, context)

    # This guard must remain before source reuse, query planning, SQLite, Qdrant,
    # external discovery, and figure analysis. A refusal is intentionally a
    # deterministic local response, never a no-result RAG response.
    if not classify_query_scope(message, context).accepted:
        return _out_of_scope_chatbot_result(
            message=message,
            retrieval_query=retrieval_query,
            started=started,
            interaction_mode=interaction_mode,
            answer_effort=answer_effort,
        )

    if not _argo_generation_slot_available(settings):
        return _fallback_chatbot_result(
            message=message,
            retrieval_query=retrieval_query,
            evidence=(),
            warnings=[
                "La fenêtre de requêtes ARGO est saturée ; la recherche n'a pas été démarrée."
            ],
            diagnostic_code="provider_quota_before_retrieval",
            started=started,
            interaction_mode=interaction_mode,
            answer_effort=answer_effort,
        )

    warnings: list[str] = []
    try:
        reasoning_wiki = load_reasoning_wiki(retrieval_query)
    except (OSError, UnicodeError, ReasoningWikiError) as exc:
        reasoning_wiki = None
        warnings.append(
            "Le wiki de raisonnement local est indisponible "
            f"({type(exc).__name__}); la recherche reste fondée sur le corpus scientifique."
        )
    source_database = Database(corpus_paths(settings, CorpusScope.COMMON).database_path)

    def reserve_llm_request() -> None:
        if on_argo_reserved is not None:
            on_argo_reserved()

    def llm_request_timeout_seconds() -> float:
        return float(settings.argo.request_timeout_seconds)

    def publish_progress(stage: ChatbotProgressStage) -> None:
        if on_progress is not None:
            on_progress(stage)

    def generation_result(
        evidence: Sequence[ChatEvidenceRecord],
        *,
        planning: HypothesisPlanningResult,
        semantic_prompt_tokens: int,
        semantic_completion_tokens: int,
        external_result_count: int,
        reused_previous_sources: bool,
        figure_analysis_count: int = 0,
        figure_analysis_duration: float = 0.0,
        figure_analysis_model: str | None = None,
    ) -> ChatbotResult:
        evidence, redundant_count = distinct_evidence(evidence, message)
        retrieval_traces.add(
            "llm_context",
            selected_article_count=len(evidence),
            selected_passage_count=sum(len(record.passages) for record in evidence),
            rejection_counts={"exact_redundant_passage": redundant_count},
            **_evidence_level_trace_counts(evidence),
        )
        publish_progress("generation")
        generation_started = perf_counter()
        generation_memory = timings.snapshot()
        try:
            with _chat_llm_client(settings, llm_request_timeout_seconds()) as llm:
                rag = _evidence_rag_service(
                    llm,
                    answer_effort,
                    settings.argo.scientific_correction_temperature,
                    settings.argo.max_input_characters,
                )
                rag.experimental_profile = active_experimental_profile
                rag.organizational_reasoning_context = (
                    reasoning_wiki.content if reasoning_wiki is not None else ""
                )
                rag.semantic_verifier = ChatAnswerVerifier(
                    ClaimVerifier(
                        llm,
                        cache=ValidationCache(settings.paths.cache_dir / "claim_validation"),
                        max_input_characters=settings.argo.max_input_characters,
                    )
                )
                answer_options = {
                    "conversation_history": context,
                    # Compatibility only: no coverage controller populates this field.
                    "coverage_notes": (),
                    "concept_definition": (
                        planning.plan.concept_definition or planning.plan.interpreted_question
                    ),
                    "ambiguities": planning.plan.ambiguities,
                    "excluded_concepts": planning.plan.excluded_concepts,
                    "on_argo_reserved": reserve_llm_request,
                    "on_argo_response": on_argo_response,
                }
                # A large evidence table makes the all-in-one JSON request markedly less
                # reliable with the local provider.  The faceted entry point uses its
                # compact, independently grounded draft path for zero- and single-axis
                # questions, while preserving the same citations and mandatory verifier.
                if len(evidence) > 8 or sum(len(record.passages) for record in evidence) > 16:
                    answer = rag.answer_faceted(
                        message,
                        evidence,
                        facets=intent.facets,
                        **answer_options,
                    )
                else:
                    answer = rag.answer(message, evidence, **answer_options)
        except MandatoryVerificationError:
            raise
        except ArgoQuotaError:
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
                diagnostic_code="provider_quota_after_retrieval",
                started=started,
                external_result_count=external_result_count,
                prompt_tokens=planning.prompt_tokens + semantic_prompt_tokens,
                completion_tokens=planning.completion_tokens + semantic_completion_tokens,
                interaction_mode=interaction_mode,
                reused_previous_sources=reused_previous_sources,
                figure_analysis_requested=analyze_figures,
                figure_analysis_count=figure_analysis_count,
                figure_analysis_duration_seconds=figure_analysis_duration,
                figure_analysis_model=figure_analysis_model,
                answer_effort=answer_effort,
                timings=timings.models(),
                retrieval_traces=retrieval_traces.models(),
            )
        except MemoryLimitError:
            timings.add(
                "argo_generation",
                perf_counter() - generation_started,
                before=generation_memory,
            )
            return _fallback_chatbot_result(
                message=message,
                retrieval_query=retrieval_query,
                evidence=evidence,
                warnings=[
                    *warnings,
                    "La génération a été arrêtée avant saturation de la mémoire locale ; "
                    "les preuves retrouvées restent disponibles pour une reprise.",
                ],
                diagnostic_code="memory_pressure_after_retrieval",
                started=started,
                external_result_count=external_result_count,
                prompt_tokens=planning.prompt_tokens + semantic_prompt_tokens,
                completion_tokens=planning.completion_tokens + semantic_completion_tokens,
                interaction_mode=interaction_mode,
                reused_previous_sources=reused_previous_sources,
                figure_analysis_requested=analyze_figures,
                figure_analysis_count=figure_analysis_count,
                figure_analysis_duration_seconds=figure_analysis_duration,
                figure_analysis_model=figure_analysis_model,
                answer_effort=answer_effort,
                timings=timings.models(),
                retrieval_traces=retrieval_traces.models(),
            )
        except ArgoError as exc:
            timings.add(
                "argo_generation",
                perf_counter() - generation_started,
                before=generation_memory,
            )
            failed_prompt_tokens, failed_completion_tokens, failed_traces = (
                _argo_failed_generation_usage(exc)
            )
            timings.add_tokens(
                "argo_generation",
                prompt_tokens=failed_prompt_tokens,
                completion_tokens=failed_completion_tokens,
            )
            return _fallback_chatbot_result(
                message=message,
                retrieval_query=retrieval_query,
                evidence=evidence,
                warnings=warnings,
                diagnostic_code=_argo_diagnostic_code(exc),
                started=started,
                external_result_count=external_result_count,
                prompt_tokens=(
                    planning.prompt_tokens + semantic_prompt_tokens + failed_prompt_tokens
                ),
                completion_tokens=(
                    planning.completion_tokens
                    + semantic_completion_tokens
                    + failed_completion_tokens
                ),
                interaction_mode=interaction_mode,
                reused_previous_sources=reused_previous_sources,
                figure_analysis_requested=analyze_figures,
                figure_analysis_count=figure_analysis_count,
                figure_analysis_duration_seconds=figure_analysis_duration,
                figure_analysis_model=figure_analysis_model,
                answer_effort=answer_effort,
                timings=timings.models(),
                retrieval_traces=retrieval_traces.models(),
                generation_traces=failed_traces,
                diagnostic_codes=_argo_diagnostic_codes(exc),
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
            evidence, answer.cited_evidence_ids, source_database
        )
        return ChatbotResult(
            message=" ".join(message.split()),
            retrieval_query=retrieval_query,
            answer_markdown=answer.answer_markdown,
            sources=sources,
            warnings=[
                *warnings,
                *_generation_quality_warnings(
                    message,
                    getattr(answer, "validation_warning_codes", []),
                ),
            ],
            model=answer.model,
            local_result_count=len(evidence),
            external_result_count=external_result_count,
            external_enrichment_used=False,
            prompt_tokens=(planning.prompt_tokens + semantic_prompt_tokens + answer.prompt_tokens),
            completion_tokens=(
                planning.completion_tokens + semantic_completion_tokens + answer.completion_tokens
            ),
            duration_seconds=perf_counter() - started,
            interaction_mode=interaction_mode,
            reused_previous_sources=reused_previous_sources,
            facet_drafts=[],
            figure_analysis_requested=analyze_figures,
            figure_analysis_count=figure_analysis_count,
            figure_analysis_duration_seconds=figure_analysis_duration,
            figure_analysis_model=figure_analysis_model,
            generation_status=getattr(answer, "generation_status", "generated"),
            diagnostic_codes=getattr(answer, "validation_warning_codes", []),
            answer_effort=answer_effort,
            timings=timings.models(),
            retrieval_traces=retrieval_traces.models(),
            generation_traces=getattr(answer, "generation_traces", []),
        )

    def complete_retrieved_evidence(
        retrieved_evidence: Sequence[ChatEvidenceRecord],
        *,
        planning: HypothesisPlanningResult,
        external_result_count: int,
        reused_previous_sources: bool,
    ) -> ChatbotResult:
        """Resume the provider-bound stages from one SQLite-authoritative evidence set."""

        evidence = list(retrieved_evidence)
        semantic_prompt_tokens = 0
        semantic_completion_tokens = 0
        semantic_unassessed_count = 0
        publish_progress("evidence_selection")
        semantic_started = perf_counter()
        semantic_memory = timings.snapshot()
        try:
            with _chat_llm_client(settings, llm_request_timeout_seconds()) as semantic_client:
                semantic_filter: GlobalSemanticFilterResult | None = (
                    ArgoGlobalSemanticEvidenceFilter(
                        semantic_client,
                        cache=ValidationCache(settings.paths.cache_dir / "semantic_validation"),
                        max_input_characters=settings.argo.max_input_characters,
                    ).filter_records(
                        retrieval_query,
                        planning.plan.verification_needs,
                        evidence,
                        on_argo_reserved=reserve_llm_request,
                    )
                )
        except ArgoError as error:
            raise MandatoryVerificationError("mandatory_semantic_filter_incomplete") from error
        finally:
            timings.add(
                "argo_semantic_filter",
                perf_counter() - semantic_started,
                before=semantic_memory,
            )
        if semantic_filter is not None:
            semantic_prompt_tokens = semantic_filter.prompt_tokens
            semantic_completion_tokens = semantic_filter.completion_tokens
            warnings.extend(
                warning
                for warning in semantic_filter.warnings
                if warning.startswith("La validation sémantique globale")
            )
            timings.add_tokens(
                "argo_semantic_filter",
                prompt_tokens=semantic_prompt_tokens,
                completion_tokens=semantic_completion_tokens,
            )
            semantic_unassessed_count = sum(
                decision.relevance == "unassessed" for decision in semantic_filter.decisions
            )
            evidence = semantic_filter.selected_records(evidence)
        else:
            semantic_unassessed_count = len(retrieved_evidence)
        semantic_trace_input_count = len(retrieved_evidence)
        retrieval_traces.add(
            "semantic_filter",
            pre_rerank_candidate_count=semantic_trace_input_count,
            post_rerank_candidate_count=len(evidence),
            selected_article_count=len(evidence),
            selected_passage_count=sum(len(record.passages) for record in evidence),
            **_evidence_level_trace_counts(evidence),
            rejection_counts={
                "global_semantic_grade_c_or_d": max(
                    0,
                    semantic_trace_input_count - len(evidence),
                ),
                **(
                    {"global_semantic_unassessed_retained": semantic_unassessed_count}
                    if semantic_unassessed_count
                    else {}
                ),
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
                external_result_count=external_result_count,
                prompt_tokens=planning.prompt_tokens + semantic_prompt_tokens,
                completion_tokens=planning.completion_tokens + semantic_completion_tokens,
                figure_analysis_requested=analyze_figures,
                answer_effort=answer_effort,
                timings=timings.models(),
                retrieval_traces=retrieval_traces.models(),
            )

        # Every semantically relevant record retained by the RAG reaches generation.
        # The generation service preserves all of their presented evidence identities
        # and only shortens passage text when required by the provider input contract.
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
                    "L'analyse locale des figures est indisponible "
                    f"({type(exc).__name__}); la réponse reste fondée sur les passages SQLite."
                )
            else:
                warnings.extend(figure_batch.warnings)
                figure_analysis_count = len(figure_batch.admitted)
                figure_analysis_duration = figure_batch.duration_seconds
                figure_analysis_model = figure_batch.model_name
                if figure_batch.admitted:
                    warnings.append(
                        "Les observations visuelles ont été analysées et persistées pour revue, "
                        "mais ne sont pas utilisées dans cette synthèse : seuls les passages "
                        "originaux SQLite font autorité."
                    )
            finally:
                timings.add(
                    "figure_analysis",
                    perf_counter() - figure_started,
                    before=figure_memory,
                )

        return generation_result(
            evidence,
            planning=planning,
            semantic_prompt_tokens=semantic_prompt_tokens,
            semantic_completion_tokens=semantic_completion_tokens,
            external_result_count=external_result_count,
            reused_previous_sources=reused_previous_sources,
            figure_analysis_count=figure_analysis_count,
            figure_analysis_duration=figure_analysis_duration,
            figure_analysis_model=figure_analysis_model,
        )

    if retrieval_checkpoint is not None:
        try:
            checkpoint_matches = (
                retrieval_checkpoint.retrieval_query == retrieval_query
                and retrieval_checkpoint.corpus_fingerprint
                == retrieval_resources.corpus_fingerprint(settings)
            )
            resumed_evidence = (
                retrieval_checkpoint.rehydrate(settings) if checkpoint_matches else []
            )
        except (OSError, RuntimeError, sqlite3.Error, ValueError):
            resumed_evidence = []
        if len(resumed_evidence) == len(retrieval_checkpoint.evidence):
            timings.restore(retrieval_checkpoint.timings)
            retrieval_traces.restore(retrieval_checkpoint.retrieval_traces)
            warnings[:] = list(dict.fromkeys([*retrieval_checkpoint.warnings, *warnings]))
            return complete_retrieved_evidence(
                resumed_evidence,
                planning=retrieval_checkpoint.planning,
                external_result_count=retrieval_checkpoint.external_result_count,
                reused_previous_sources=False,
            )
        warnings.append(
            "Le point de reprise des preuves n'est plus cohérent avec SQLite ; "
            "une nouvelle recherche locale est exécutée."
        )

    # Conversation cards carry identities only. Both abstracts and full-text sources
    # are rehydrated and revalidated against the new question before synthesis.
    if interaction_mode == "conversation":
        try:
            reused_evidence = [
                record
                for record in chat_evidence_from_previous_sources(
                    settings,
                    query=retrieval_query,
                    sources=previous_sources,
                    answer_effort=answer_effort,
                )
                if record.origin == "local_rag" and record.scope is not None
            ]
        except Exception as exc:
            reused_evidence = []
            warnings.append(
                "Les passages SQLite de la conversation n'ont pas pu être rechargés "
                f"({type(exc).__name__}); une nouvelle recherche locale est exécutée."
            )
        if reused_evidence:
            planning = deterministic_hypothesis_plan(retrieval_query, effort=answer_effort)
            publish_progress("evidence_selection")
            with _chat_llm_client(settings, llm_request_timeout_seconds()) as semantic_client:
                reuse_filter = ArgoGlobalSemanticEvidenceFilter(
                    semantic_client,
                    cache=ValidationCache(settings.paths.cache_dir / "semantic_validation"),
                    max_input_characters=settings.argo.max_input_characters,
                ).filter_records(
                    retrieval_query,
                    planning.plan.verification_needs,
                    reused_evidence,
                    on_argo_reserved=reserve_llm_request,
                )
            reused_evidence = reuse_filter.selected_records(reused_evidence)
            if not reused_evidence:
                return _fallback_chatbot_result(
                    message=message,
                    retrieval_query=retrieval_query,
                    evidence=[],
                    warnings=warnings,
                    diagnostic_code="semantic_filter_empty",
                    started=started,
                    answer_effort=answer_effort,
                )
            return generation_result(
                reused_evidence,
                planning=planning,
                semantic_prompt_tokens=reuse_filter.prompt_tokens,
                semantic_completion_tokens=reuse_filter.completion_tokens,
                external_result_count=0,
                reused_previous_sources=True,
            )

    publish_progress("planning")
    planning_started = perf_counter()
    planning_memory = timings.snapshot()
    try:
        with _chat_llm_client(settings, llm_request_timeout_seconds()) as planning_client:
            planner = ArgoQueryPlanningService(planning_client)
            plan_parameters = inspect.signature(planner.plan).parameters.values()
            supports_kwargs = any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in plan_parameters
            )
            planning_options: dict[str, Any] = {}
            if supports_kwargs or any(parameter.name == "effort" for parameter in plan_parameters):
                planning_options["effort"] = answer_effort
            elif any(parameter.name == "deep" for parameter in plan_parameters):
                planning_options["deep"] = answer_effort is AnswerEffort.DEEP
            if supports_kwargs or any(
                parameter.name == "conversation_history" for parameter in plan_parameters
            ):
                planning_options["conversation_history"] = context
            if supports_kwargs or any(
                parameter.name == "reasoning_context" for parameter in plan_parameters
            ):
                planning_options["reasoning_context"] = (
                    reasoning_wiki.content if reasoning_wiki is not None else ""
                )
            if supports_kwargs or any(
                parameter.name == "on_argo_reserved" for parameter in plan_parameters
            ):
                planning_options["on_argo_reserved"] = reserve_llm_request
            plan_cache = ValidationCache(settings.paths.cache_dir / "hypothesis_plans")
            plan_key = content_key(
                {
                    "version": "hypothesis-v3-wiki",
                    "client": client_identity(planning_client),
                    "question": retrieval_query,
                    "history": context,
                    "effort": answer_effort.value,
                    "reasoning_wiki_sha256": (
                        reasoning_wiki.manifest_sha256 if reasoning_wiki is not None else None
                    ),
                }
            )
            planning = plan_cache.get(plan_key, HypothesisPlanningResult)
            if planning is None:
                raw_planning = planner.plan(retrieval_query, **planning_options)
                planning = coerce_hypothesis_planning_result(
                    raw_planning, retrieval_query, effort=answer_effort
                )
                plan_cache.put(plan_key, planning)
            else:
                planning = planning.model_copy(update={"prompt_tokens": 0, "completion_tokens": 0})
    except ArgoQuotaError:
        raise
    except ArgoError as exc:
        planning = deterministic_hypothesis_plan(retrieval_query, effort=answer_effort)
        warnings.append(
            "La préparation hypothétique Argo est indisponible "
            f"({_query_planning_diagnostic_code(exc)}); utilisation du plan local prudent."
        )
    except Exception as exc:
        planning = deterministic_hypothesis_plan(retrieval_query, effort=answer_effort)
        warnings.append(
            "La préparation hypothétique de la recherche est indisponible "
            f"({type(exc).__name__}); utilisation du plan local prudent."
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

    intent = planning.plan.scientific_intent(
        retrieval_query,
        deep=answer_effort is AnswerEffort.DEEP,
    )
    grouped_queries = planning.plan.lexical_queries(retrieval_query)
    dense_queries = planning.plan.dense_queries(retrieval_query)
    maximum_variants = max(2, len(grouped_queries))
    grouped_expansions = grouped_queries[1:]
    candidate_limit = _initial_retrieval_candidate_limit(settings, effort_budget)
    dense_retrieval_enabled = _dense_chat_retrieval_is_safe(settings)
    vector_query_variant_limit = (
        min(effort_budget.max_vector_query_variants, len(grouped_queries))
        if dense_retrieval_enabled
        else 0
    )
    if not dense_retrieval_enabled:
        warnings.append(
            "La recherche vectorielle locale est reportée faute de marge mémoire suffisante ; "
            "la réponse utilise la recherche lexicale traçable du corpus SQLite."
        )

    publish_progress("search")
    retrieval_failed = False
    abstract_diagnostics: list[str] = []
    with (
        _serialized_chat_retrieval_scope(timing=timings),
        retrieval_resources.qdrant_wave(settings) as qdrant_client_owner,
    ):
        try:
            local_results = _timed_chat_retrieval_operation(
                search_common_corpus_abstracts,
                settings,
                query=retrieval_query,
                limit=effort_budget.abstract_result_limit,
                search_queries=grouped_expansions,
                dense_queries=dense_queries if dense_retrieval_enabled else (),
                intent_override=intent,
                max_query_variants=maximum_variants,
                max_vector_query_variants=vector_query_variant_limit,
                candidate_limit=candidate_limit,
                prefix_matching=False,
                retrieval_resources=retrieval_resources,
                retrieval_trace=retrieval_traces,
                qdrant_client_owner=qdrant_client_owner,
                diagnostics=abstract_diagnostics,
                timing=timings,
                timing_stage="abstract_search",
            )
        except Exception as exc:
            retrieval_failed = True
            local_results = []
            code = _abstract_search_failure_code("unhandled", exc)
            abstract_diagnostics.append(code)
            retrieval_traces.add("abstract_search", rejection_counts={code: 1})
            LOGGER.warning(
                "abstract_search_unhandled error_type=%s",
                type(exc).__name__,
            )

        if use_external_sources:
            publish_progress("enrichment")
        if use_external_sources and settings.full_text.enabled:
            acquired_article_ids, acquisition_warnings = _timed_chat_retrieval_operation(
                acquire_common_full_text_for_chat,
                settings,
                local_results,
                max_downloads=2,
                qdrant_client_owner=qdrant_client_owner,
                timing=timings,
                timing_stage="full_text_acquisition",
            )
            warnings.extend(acquisition_warnings)
            if acquired_article_ids:
                retrieval_resources.invalidate_corpus_fingerprint()

        try:
            full_text_records = _timed_chat_retrieval_operation(
                search_common_corpus_full_text_evidence,
                settings,
                query=retrieval_query,
                article_count=effort_budget.article_count,
                search_queries=grouped_expansions,
                dense_queries=dense_queries if dense_retrieval_enabled else (),
                axis_queries=None,
                intent_override=intent,
                max_query_variants=maximum_variants,
                max_vector_query_variants=vector_query_variant_limit,
                candidate_limit=candidate_limit,
                prefix_matching=False,
                include_fallback_variants=False,
                passage_count=effort_budget.passages_per_article,
                candidate_chunks_per_article=effort_budget.candidate_chunks_per_article,
                context_radius=effort_budget.context_radius,
                retrieval_resources=retrieval_resources,
                retrieval_trace=retrieval_traces,
                qdrant_client_owner=qdrant_client_owner,
                timing=timings,
                timing_stage="full_text_search",
            )
        except Exception as exc:
            retrieval_failed = True
            full_text_records = []
            warnings.append(
                "La recherche groupée dans les textes intégraux SQLite est indisponible "
                f"({type(exc).__name__}); repli sur les abstracts persistés."
            )

    if abstract_warning := _abstract_route_warning(
        message,
        diagnostics=abstract_diagnostics,
        abstract_result_count=len(local_results),
        full_text_records=full_text_records,
    ):
        warnings.append(abstract_warning)

    external_result_count = 0
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
                f"L'enrichissement bibliographique externe est indisponible ({type(exc).__name__})."
            )
        else:
            external_result_count = len(external_report.records)
            warnings.extend(
                f"La source {error.source} n'a pas répondu à cette requête."
                for error in external_report.errors
            )
            if external_report.records:
                warnings.append(
                    "Les notices externes découvertes ne sont pas utilisées comme preuves avant "
                    "leur ingestion et leur validation dans SQLite."
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
    local_candidates, _ignored_external_count = merge_chatbot_candidates(
        local_results,
        [],
        limit=effort_budget.abstract_result_limit,
    )
    abstract_evidence = abstract_candidates_to_chat_evidence(local_candidates)
    evidence = merge_chat_evidence(
        full_text_records,
        abstract_evidence,
        query=retrieval_query,
        limit=effort_budget.evidence_record_limit,
        intent_override=intent,
    )
    # Defense in depth: generated or unpersisted external snippets cannot reach
    # semantic validation or final synthesis.
    evidence = [
        record for record in evidence if record.origin == "local_rag" and record.scope is not None
    ]
    timings.add("evidence_merge", perf_counter() - merge_started, before=merge_memory)
    merge_input_count = len(full_text_records) + len(abstract_evidence)
    retrieval_traces.add(
        "evidence_merge",
        pre_rerank_candidate_count=merge_input_count,
        post_rerank_candidate_count=len(evidence),
        selected_article_count=len(evidence),
        selected_passage_count=sum(len(record.passages) for record in evidence),
        **_evidence_level_trace_counts(evidence),
        rejection_counts={
            "duplicate_unpersisted_or_not_selected": max(
                0,
                merge_input_count - len(evidence),
            )
        },
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
            external_result_count=external_result_count,
            prompt_tokens=planning.prompt_tokens,
            completion_tokens=planning.completion_tokens,
            figure_analysis_requested=analyze_figures,
            answer_effort=answer_effort,
            timings=timings.models(),
            retrieval_traces=retrieval_traces.models(),
        )

    if on_retrieval_checkpoint is not None:
        on_retrieval_checkpoint(
            ChatRetrievalCheckpoint.capture(
                retrieval_query=retrieval_query,
                corpus_fingerprint=retrieval_resources.corpus_fingerprint(settings),
                planning=planning,
                evidence=evidence,
                external_result_count=external_result_count,
                warnings=warnings,
                timings=timings.models(),
                retrieval_traces=retrieval_traces.models(),
            )
        )
    return complete_retrieved_evidence(
        evidence,
        planning=planning,
        external_result_count=external_result_count,
        reused_previous_sources=False,
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
        model_version=active_llm_model(settings),
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


def exclude_unidentifiable_local_sources(
    settings: Settings,
    database: Database,
    *,
    article_ids: Sequence[str],
) -> dict[str, int]:
    """Remove no-title local sources from retrieval without deleting their audit record."""

    unique_ids = tuple(dict.fromkeys(article_ids))
    chunk_ids = [
        chunk_id for article_id in unique_ids for chunk_id in database.article_chunk_ids(article_id)
    ]
    if not unique_ids:
        return {
            "excluded_articles": 0,
            "retained_chunks": 0,
            "deleted_vector_points": 0,
        }
    index = QdrantLocalIndex(settings)
    try:
        index_manifest = prepare_index_generation_mutation(index)
        excluded_articles = database.exclude_unidentifiable_local_articles(
            unique_ids,
            reason="Titre principal non identifiable après audit OCR: fichier local",
        )
        deleted_points = index.delete_points(chunk_ids)
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
        "excluded_articles": excluded_articles,
        "retained_chunks": len(chunk_ids),
        "deleted_vector_points": deleted_points,
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
