"""Pre-activation operations for the durable deep-research worker."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths
from app.database.sqlite import Database
from app.deep_research.abstention import DeepResearchAbstentionStage
from app.deep_research.admission import ClaimAdmissionStage
from app.deep_research.cache import (
    DeepResearchCacheEntry,
    DeepResearchCacheSignature,
    DeepResearchResponseCache,
)
from app.deep_research.citations import (
    CitationSourceFragment,
    CitationTraversalStage,
    SQLiteCitationTargetResolver,
)
from app.deep_research.claims import AtomicClaimExtractionStage
from app.deep_research.contextual_summary import (
    ContextualSummarizer,
    SummarisableFragment,
    build_contextual_evidence,
)
from app.deep_research.epistemic import EpistemicAssessmentStage
from app.deep_research.iteration import (
    ArgoResearchGapAssessor,
    ResearchGapAssessor,
    ResearchLoopStore,
)
from app.deep_research.rendering import SQLiteDeepResearchRenderer
from app.deep_research.retrieval import (
    DeepResearchFragmentHit,
    DeepResearchRetrievalStage,
    FragmentIdentity,
    LexicalBackend,
    VectorBackend,
)
from app.deep_research.verification import SemanticClaimVerificationStage
from app.jobs.contracts import DeepResearchPayload
from app.llm.argo_client import ArgoClient
from app.llm.figure_analysis import (
    FigureAnalysisBatch,
    FigureAnalysisUnavailable,
    FigureSourceReference,
    OllamaFigureAnalysisService,
)
from app.retrieval.multi_corpus import (
    MultiCorpusLexicalSearchService,
    MultiCorpusVectorSearchService,
)
from app.retrieval.reranker import MultilingualReranker


@dataclass(frozen=True, slots=True)
class SQLiteFragmentTextLoader:
    """Rehydrate retained text from the authoritative database for each scope."""

    settings: Settings

    def load(self, hits: list[DeepResearchFragmentHit]) -> dict[FragmentIdentity, str]:
        loaded: dict[FragmentIdentity, str] = {}
        for scope in (CorpusScope.COMMON, CorpusScope.PRIVATE):
            scoped_hits = [hit for hit in hits if hit.scope is scope]
            if not scoped_hits:
                continue
            rows = Database(corpus_paths(self.settings, scope).database_path).chunks_by_ids(
                [hit.chunk_id for hit in scoped_hits]
            )
            for hit in scoped_hits:
                row = rows.get(hit.chunk_id)
                if row is None:
                    raise RuntimeError("retained deep-research chunk is missing from SQLite")
                text = str(row["text"])
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if (
                    str(row["article_id"]) != hit.article_id
                    or int(row["page_start"]) != hit.page_start
                    or int(row["page_end"]) != hit.page_end
                    or digest != hit.text_sha256
                ):
                    raise RuntimeError("retained deep-research SQLite provenance changed")
                loaded[(scope, hit.article_id, hit.chunk_id)] = text
        return loaded


@dataclass(slots=True)
class DeepResearchPreparationOperations:
    """Run safe local preparation while the public deep mode remains gated."""

    retrieval: DeepResearchRetrievalStage
    contextual_summarizer: ContextualSummarizer
    research_loop: ResearchLoopStore
    citation_traversal: CitationTraversalStage
    claim_extraction: AtomicClaimExtractionStage
    claim_verification: SemanticClaimVerificationStage
    epistemic_assessment: EpistemicAssessmentStage
    claim_admission: ClaimAdmissionStage
    abstention: DeepResearchAbstentionStage
    renderer: SQLiteDeepResearchRenderer
    response_cache: DeepResearchResponseCache | None
    gap_assessor: ResearchGapAssessor | None = None
    figure_analyzer: OllamaFigureAnalysisService | None = None
    _cache_resolution: dict[
        tuple[str, str],
        tuple[DeepResearchCacheSignature | None, DeepResearchCacheEntry | None],
    ] = field(default_factory=dict, init=False, repr=False)
    _figure_batches: dict[tuple[str, str], FigureAnalysisBatch] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @staticmethod
    def _run_key(payload: DeepResearchPayload) -> tuple[str, str]:
        return (str(payload.conversation_id), str(payload.client_request_id))

    def _resolve_cache(
        self,
        payload: DeepResearchPayload,
    ) -> tuple[DeepResearchCacheSignature | None, DeepResearchCacheEntry | None]:
        key = self._run_key(payload)
        resolved = self._cache_resolution.get(key)
        if resolved is None:
            if self.response_cache is None or payload.analyze_figures:
                resolved = (None, None)
            else:
                signature = self.response_cache.signature(payload.message)
                resolved = (signature, self.response_cache.get(signature))
            self._cache_resolution[key] = resolved
        return resolved

    def search(self, payload: DeepResearchPayload) -> None:
        if self._resolve_cache(payload)[1] is not None:
            return
        if not self.retrieval.exists(payload):
            self.retrieval.search(payload)
        self.research_loop.load_or_create(payload)

    def confirm_reranking(self, payload: DeepResearchPayload) -> None:
        if self._resolve_cache(payload)[1] is not None:
            return
        snapshot = self.retrieval.load(payload)
        if snapshot.cross_encoder_candidate_count < len(snapshot.hits):
            raise RuntimeError("deep-research reranking retained an invalid candidate count")
        if (
            self.retrieval.reranker is not None
            and self.retrieval.reranker.enabled
            and snapshot.hits
            and not snapshot.reranker_enabled
        ):
            raise RuntimeError("deep-research reranking checkpoint is incomplete")

    def _extract_iteration(self, payload: DeepResearchPayload, iteration: int) -> None:
        snapshot = self.retrieval.load(payload, iteration=iteration)
        if snapshot.contextual_evidence is not None:
            return
        if snapshot.scopes != [CorpusScope.COMMON, CorpusScope.PRIVATE]:
            raise RuntimeError("deep-research snapshot has incomplete corpus scopes")
        fragments = [
            SummarisableFragment.from_hit_and_text(hit, text)
            for hit, text in self.retrieval.retained_fragments(
                payload,
                iteration=iteration,
            )
        ]
        summaries = self.contextual_summarizer.summarize_batch(
            snapshot.query,
            fragments,
        )
        evidence = build_contextual_evidence(
            summaries,
            threshold=self.contextual_summarizer.relevance_threshold,
        )
        self.retrieval.save_contextual_summaries(
            payload,
            summaries,
            attempted=bool(self.contextual_summarizer.client and fragments),
            evidence=evidence,
            iteration=iteration,
        )

    def extract_evidence(self, payload: DeepResearchPayload) -> None:
        if self._resolve_cache(payload)[1] is not None:
            return
        self._extract_iteration(payload, 1)
        loop = self.research_loop.load_or_create(payload)
        if len(loop.iterations) == 1 and loop.stop_reason is None:
            if self.gap_assessor is None:
                loop = self.research_loop.stop(payload, "gap_assessment_disabled")
            else:
                assessment = self.gap_assessor.assess(
                    payload.message,
                    self.retrieval.contextual_evidence(payload, iteration=1),
                )
                if assessment is None:
                    loop = self.research_loop.stop(payload, "no_valid_gap")
                elif assessment.sufficient:
                    loop = self.research_loop.stop(payload, "sufficient_evidence")
                else:
                    loop = self.research_loop.plan_follow_up(payload, assessment)
        if len(loop.iterations) == 2:
            follow_up = loop.iterations[1]
            if follow_up.gap is None:
                raise RuntimeError("deep-research follow-up gap is missing")
            if not self.retrieval.exists(payload, iteration=2):
                self.retrieval.search(
                    payload,
                    query=follow_up.query,
                    iteration=2,
                    explicit_gap_id=follow_up.gap.gap_id,
                    explicit_gap_description=follow_up.gap.description,
                )
            self._extract_iteration(payload, 2)
            self.research_loop.stop(payload, "maximum_iterations")
        loop = self.research_loop.load_or_create(payload)
        citation_fragments = [
            CitationSourceFragment(
                scope=hit.scope,
                article_id=hit.article_id,
                chunk_id=hit.chunk_id,
                page_start=hit.page_start,
                page_end=hit.page_end,
                text=text,
            )
            for record in loop.iterations
            for hit, text in self.retrieval.retained_fragments(
                payload,
                iteration=record.index,
            )
        ]
        self.citation_traversal.traverse(payload, citation_fragments)
        figure_fragments: list[CitationSourceFragment] = []
        if payload.analyze_figures and self.figure_analyzer is not None:
            references = [
                FigureSourceReference(
                    scope=fragment.scope,
                    article_id=fragment.article_id,
                    chunk_id=fragment.chunk_id,
                    page_start=fragment.page_start,
                    page_end=fragment.page_end,
                    rank=index,
                )
                for index, fragment in enumerate(citation_fragments)
            ]
            try:
                batch = self.figure_analyzer.analyze(payload.message, references)
            except FigureAnalysisUnavailable:
                batch = FigureAnalysisBatch(
                    processed_count=0,
                    admitted=[],
                    warnings=["L’analyse locale des figures est indisponible."],
                    duration_seconds=0.0,
                    model_name=self.figure_analyzer.config.model,
                )
            self._figure_batches[self._run_key(payload)] = batch
            figure_fragments = [
                CitationSourceFragment(
                    evidence_kind="figure",
                    scope=figure.scope,
                    article_id=figure.article_id,
                    chunk_id=figure.related_chunk_id,
                    page_start=figure.page_number,
                    page_end=figure.page_number,
                    text=figure.observation_text,
                    figure_analysis_id=figure.analysis_id,
                    figure_label=figure.figure_label,
                )
                for figure in batch.admitted
            ]
        text_limit = max(0, 24 - len(figure_fragments))
        self.claim_extraction.extract(
            payload,
            [*figure_fragments, *citation_fragments[:text_limit]],
        )

    def verify(self, payload: DeepResearchPayload) -> None:
        if self._resolve_cache(payload)[1] is not None:
            return
        loop = self.research_loop.load_or_create(payload)
        if loop.stop_reason is None:
            raise RuntimeError("deep-research search loop has no stop decision")
        self.citation_traversal.load(payload)
        claims = self.claim_extraction.load(payload)
        verifications = self.claim_verification.verify(payload, claims)
        epistemic = self.epistemic_assessment.assess(payload, claims, verifications)
        admission = self.claim_admission.decide(
            payload,
            claims,
            verifications,
            epistemic,
        )
        self.abstention.decide(payload, loop, admission)
        allowed_scopes = {CorpusScope.COMMON, CorpusScope.PRIVATE}
        for record in loop.iterations:
            snapshot = self.retrieval.load(payload, iteration=record.index)
            self.retrieval.contextual_evidence(payload, iteration=record.index)
            if snapshot.query != record.query:
                raise RuntimeError("deep-research iteration query changed after planning")
            if any(hit.scope not in allowed_scopes for hit in snapshot.hits):
                raise RuntimeError("deep-research snapshot contains an unknown corpus scope")
            if any(hit.page_end < hit.page_start for hit in snapshot.hits):
                raise RuntimeError("deep-research snapshot contains invalid page provenance")

    def synthesize(self, payload: DeepResearchPayload) -> str:
        signature, cached = self._resolve_cache(payload)
        if cached is not None:
            return cached.answer_markdown
        loop = self.research_loop.load_or_create(payload)
        if loop.stop_reason is None:
            raise RuntimeError("deep-research search loop has no stop decision")
        for record in loop.iterations:
            self.retrieval.contextual_evidence(payload, iteration=record.index)
        self.claim_verification.load(payload)
        self.epistemic_assessment.load(payload)
        self.claim_admission.load(payload)
        readiness = self.abstention.load(payload)
        if readiness.outcome == "abstain":
            if readiness.abstention_markdown is None:
                raise RuntimeError("deep-research abstention text is missing")
            answer_markdown = readiness.abstention_markdown
        else:
            claims = self.claim_extraction.load(payload)
            admission = self.claim_admission.load(payload)
            answer_markdown = self.renderer.render(
                payload,
                claims,
                admission,
            ).answer_markdown
        if self.response_cache is not None and signature is not None:
            self.response_cache.put(
                signature,
                answer_markdown=answer_markdown,
                details=self._uncached_response_details(payload),
            )
        return answer_markdown

    def response_details(self, payload: DeepResearchPayload) -> dict[str, object]:
        signature, cached = self._resolve_cache(payload)
        if cached is not None:
            if signature is None:
                raise RuntimeError("deep-research cache hit has no signature")
            return {
                **cached.details,
                "cache": {
                    "hit": True,
                    "key_sha256": signature.cache_key_sha256,
                },
            }
        details = {
            **self._uncached_response_details(payload),
            "cache": {
                "hit": False,
                "key_sha256": signature.cache_key_sha256 if signature is not None else None,
            },
        }
        return details

    def _uncached_response_details(self, payload: DeepResearchPayload) -> dict[str, object]:
        claims = self.claim_extraction.load(payload)
        admission = self.claim_admission.load(payload)
        readiness = self.abstention.load(payload)
        rendered = self.renderer.load(payload) if readiness.outcome == "answerable" else None
        return {
            "epistemic_claims": self.epistemic_assessment.public_details(
                payload,
                claims,
            ),
            "claim_admission": [
                {
                    "claim_id": item.claim_id,
                    "status": item.status,
                    "reason": item.reason,
                }
                for item in admission.decisions
            ],
            "readiness": {
                "outcome": readiness.outcome,
                "admitted_claim_count": readiness.admitted_claim_count,
                "gap_descriptions": readiness.gap_descriptions,
            },
            "citations": (
                [item.model_dump(mode="json") for item in rendered.citations]
                if rendered is not None
                else []
            ),
            "figure_analysis": self._figure_analysis_details(payload),
        }

    def _figure_analysis_details(self, payload: DeepResearchPayload) -> dict[str, object]:
        batch = self._figure_batches.get(self._run_key(payload))
        if batch is None:
            return {
                "requested": payload.analyze_figures,
                "processed_count": 0,
                "admitted_count": 0,
                "duration_seconds": 0.0,
                "model": self.figure_analyzer.config.model if self.figure_analyzer else None,
                "warnings": [],
            }
        return {
            "requested": payload.analyze_figures,
            "processed_count": batch.processed_count,
            "admitted_count": len(batch.admitted),
            "duration_seconds": batch.duration_seconds,
            "model": batch.model_name,
            "warnings": batch.warnings,
        }

    def close(self) -> None:
        client = self.contextual_summarizer.client
        close_client = getattr(client, "close", None)
        if callable(close_client):
            close_client()
        reranker = self.retrieval.reranker
        if reranker is not None:
            reranker.close()
        if self.figure_analyzer is not None:
            self.figure_analyzer.close()


def build_deep_research_operations(
    settings: Settings,
    *,
    lexical: LexicalBackend | None = None,
    vector: VectorBackend | None = None,
    contextual_summarizer: ContextualSummarizer | None = None,
    gap_assessor: ResearchGapAssessor | None = None,
    enable_response_cache: bool = True,
) -> DeepResearchPreparationOperations:
    """Build the production-scoped search stage without enabling public submission."""

    checkpoint_root = settings.paths.cache_dir / "deep_research"
    summarizer = contextual_summarizer or ContextualSummarizer(
        ArgoClient(settings) if settings.deep_research.contextual_summary_enabled else None,
        top_k=settings.deep_research.contextual_summary_top_k,
        relevance_threshold=settings.deep_research.contextual_relevance_threshold,
    )
    assessor = gap_assessor
    if (
        assessor is None
        and settings.deep_research.contextual_summary_enabled
        and summarizer.client is not None
    ):
        assessor = ArgoResearchGapAssessor(summarizer.client)
    return DeepResearchPreparationOperations(
        retrieval=DeepResearchRetrievalStage(
            lexical or MultiCorpusLexicalSearchService.from_settings(settings),
            vector or MultiCorpusVectorSearchService.from_settings(settings),
            checkpoint_root,
            limit_per_scope=40,
            reranker=MultilingualReranker.from_settings(settings),
            rrf_k=settings.deep_research.rrf_k,
            rrf_candidate_limit=settings.deep_research.rrf_candidate_limit,
            cross_encoder_candidate_limit=settings.deep_research.cross_encoder_candidate_limit,
            retained_fragment_limit=settings.deep_research.retained_fragment_limit,
            text_loader=SQLiteFragmentTextLoader(settings),
        ),
        contextual_summarizer=summarizer,
        research_loop=ResearchLoopStore(checkpoint_root),
        citation_traversal=CitationTraversalStage(
            SQLiteCitationTargetResolver(settings),
            checkpoint_root,
        ),
        claim_extraction=AtomicClaimExtractionStage(
            summarizer.client if settings.deep_research.contextual_summary_enabled else None,
            checkpoint_root,
        ),
        claim_verification=SemanticClaimVerificationStage(
            summarizer.client if settings.deep_research.contextual_summary_enabled else None,
            checkpoint_root,
        ),
        epistemic_assessment=EpistemicAssessmentStage(checkpoint_root),
        claim_admission=ClaimAdmissionStage(checkpoint_root),
        abstention=DeepResearchAbstentionStage(checkpoint_root),
        renderer=SQLiteDeepResearchRenderer(settings, checkpoint_root),
        response_cache=(
            DeepResearchResponseCache(
                settings,
                settings.paths.cache_dir / "deep_research_responses",
            )
            if enable_response_cache
            else None
        ),
        gap_assessor=assessor,
        figure_analyzer=(
            OllamaFigureAnalysisService(settings) if settings.figure_analysis.enabled else None
        ),
    )
