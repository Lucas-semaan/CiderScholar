"""Durable chat handler delegating to the existing scientific workflow."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from typing import Protocol
from uuid import UUID, uuid4

from app.chat_effort import AnswerEffort
from app.config import Settings
from app.corpora import CorpusScope, LocalProfile, load_local_profile, settings_for_corpus
from app.database.migrations import CURRENT_SCHEMA_VERSION
from app.database.sqlite import Database
from app.jobs.contracts import JOB_STEP_ORDER, JobStep
from app.jobs.repository import JobRecord
from app.jobs.worker import JobHandlerResult, JobProgressContext
from app.knowledge.repository import KnowledgeRepository, KnowledgeRepositoryError
from app.knowledge.routing import preview_routing
from app.knowledge.trace import (
    ExpertRunManifest,
    TraceBudget,
    TraceCandidate,
    TraceClaimLink,
    TraceCost,
    TraceEvidence,
    TraceIdentity,
    TraceOutput,
    TraceRoutingDecision,
)
from app.llm.argo_client import ArgoScientificValidationError, ScientificValidationReason
from app.models.chatbot import ChatbotEvaluationTrace, ChatbotResult, ChatbotSource
from app.retrieval.chat_checkpoint import (
    ChatRetrievalCheckpoint,
    ChatRetrievalCheckpointStore,
    FigureAnalysisCheckpoint,
    FigureAnalysisCheckpointStore,
)
from app.retrieval.index_manifest import index_generation_manifest_path
from app.services.chatbot import latest_chatbot_sources, resolve_chat_interaction_mode
from app.services.workflows import ChatbotProgressStage, answer_chatbot

LOGGER = logging.getLogger("ciderscholar.jobs.chat_handler")


def _trace_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _installed_code_revision() -> str:
    try:
        release = version("local-science-rag")
    except PackageNotFoundError:
        release = "0.2.11"
    return f"local-science-rag@{release}"


def _index_fingerprints(settings: Settings) -> tuple[str, ...]:
    """Hash available index sidecars without loading vectors or source text."""

    scoped = settings_for_corpus(settings, CorpusScope.COMMON)
    path = index_generation_manifest_path(scoped)
    try:
        if not path.is_file():
            return ()
        return (sha256(path.read_bytes()).hexdigest(),)
    except OSError:
        return ()


def _shadow_routing_trace(
    *, job: JobRecord, settings: Settings, database: Database
) -> tuple[tuple[TraceIdentity, ...], tuple[TraceBudget, ...], tuple[TraceRoutingDecision, ...]]:
    """Preview the pinned release without changing retrieval or generation."""

    pin = job.payload.expert_memory_pin
    if pin.mode != "shadow" or pin.release_id is None:
        return (), (), ()
    try:
        _release, package = KnowledgeRepository(database).load_release(pin.release_id)
        decision = preview_routing(
            job.payload.message,
            package,
            config=settings.expert_memory.model_copy(update={"mode": "shadow"}),
        )
    except (KnowledgeRepositoryError, OSError, ValueError) as error:
        LOGGER.warning(
            "expert_memory_shadow_routing_failed job_id=%s error_type=%s",
            job.id,
            type(error).__name__,
        )
        return (), (), ()
    items = tuple(
        TraceIdentity(
            item_id=item.id,
            revision=item.revision,
            content_sha256=item.content_sha256,
        )
        for item in decision.selected
    )
    limits = {
        "planning": settings.expert_memory.planning_max_characters,
        "semantic_filter": settings.expert_memory.semantic_max_characters,
        "generation": settings.expert_memory.generation_max_characters,
    }
    budgets = tuple(
        TraceBudget(
            stage=context.stage,
            limit_characters=limits[context.stage],
            used_characters=context.characters,
            fallback=decision.fallback == "budget_fallback",
        )
        for context in decision.contexts
    )
    routing_decisions = tuple(
        TraceRoutingDecision(route_id=route.route_id, reason=route.reason)
        for route in decision.routes
    )
    return items, budgets, routing_decisions


def _trace_manifest(
    *,
    job: JobRecord,
    settings: Settings,
    history: Sequence[Mapping[str, str]],
    interaction_mode: str,
    state: str,
    result: ChatbotResult | None,
    now,
    run_id: UUID | None = None,
    created_at=None,
    routing_items: tuple[TraceIdentity, ...] = (),
    budgets: tuple[TraceBudget, ...] = (),
    routing_decisions: tuple[TraceRoutingDecision, ...] = (),
) -> ExpertRunManifest:
    """Persist hashes and evidence IDs for audit without logging question or passage text."""

    def digest(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    evidence: list[TraceEvidence] = []
    candidates: list[TraceCandidate] = []
    claim_links: list[TraceClaimLink] = []
    incomplete = False
    if result is not None:
        candidates.extend(
            TraceCandidate(
                identity=TraceIdentity(
                    item_id=item.item_id,
                    revision=item.revision,
                    content_sha256=item.content_sha256,
                ),
                stage=item.stage,
                rank=item.rank,
                score=item.score,
                decision=item.decision,
                reason=item.reason,
            )
            for item in result.retrieval_trace_candidates
        )
        for anchor in result.citation_anchors:
            linked_ids: list[str] = []
            for citation in anchor.evidence:
                if citation.source_text_sha256 is None or citation.presented_text_sha256 is None:
                    incomplete = True
                    continue
                source_kind = "chunk" if citation.chunk_id is not None else "article_abstract"
                source_id = str(citation.chunk_id or anchor.article_id or anchor.record_id)
                evidence.append(
                    TraceEvidence(
                        evidence_id=citation.evidence_id,
                        source_kind=source_kind,
                        source_id=source_id,
                        text_sha256=citation.source_text_sha256,
                        presented_text_sha256=citation.presented_text_sha256,
                        page_start=citation.page_start,
                        page_end=citation.page_end,
                        section_path=(
                            None if citation.page_start is not None else citation.section_path
                        ),
                    )
                )
                candidates.append(
                    TraceCandidate(
                        identity=TraceIdentity(
                            item_id=citation.evidence_id,
                            revision=1,
                            content_sha256=citation.source_text_sha256,
                        ),
                        stage="final_context",
                        rank=anchor.display_index,
                        decision="retained",
                    )
                )
                linked_ids.append(citation.evidence_id)
            if linked_ids:
                claim_links.append(
                    TraceClaimLink(claim_id=anchor.citation_id, evidence_ids=tuple(linked_ids))
                )
    output_state = "incomplete" if incomplete else state
    prompt_tokens = result.prompt_tokens if result is not None else 0
    completion_tokens = result.completion_tokens if result is not None else 0
    response_sha256 = digest(result.answer_markdown) if result is not None else None
    return ExpertRunManifest(
        run_id=run_id or uuid4(),
        job_id=job.id,
        attempt=job.attempt,
        mode=job.payload.expert_memory_pin.mode,
        code_revision=_installed_code_revision(),
        question_sha256=digest(job.payload.message),
        user_context_sha256=_trace_hash(history),
        answer_effort=job.payload.answer_effort,
        interaction_mode="research" if interaction_mode == "research" else "conversation",
        configuration_sha256=_trace_hash(settings.expert_memory.model_dump(mode="json")),
        sql_schema_version=CURRENT_SCHEMA_VERSION,
        index_fingerprints=_index_fingerprints(settings),
        routing_items=routing_items,
        routing_decisions=routing_decisions,
        budgets=budgets,
        corpus_fingerprint_before=(
            result.trace_corpus_fingerprint_before if result is not None else None
        ),
        corpus_fingerprint_after=(
            result.trace_corpus_fingerprint_after if result is not None else None
        ),
        release_id=job.payload.expert_memory_pin.release_id,
        release_sha256=job.payload.expert_memory_pin.release_sha256,
        recipe_version=job.payload.expert_memory_pin.recipe_version,
        recipe_sha256=job.payload.expert_memory_pin.recipe_sha256,
        model_versions=(
            ((result.model,) if result is not None else ())
            + (settings.embeddings.model_name, settings.reranker.model_name)
        ),
        candidates=tuple(candidates[:300]),
        evidence=tuple(evidence),
        output=TraceOutput(
            state=output_state,
            response_sha256=response_sha256,
            claim_links=tuple(claim_links),
        ),
        cost=TraceCost(
            retrieval_requests=(
                sum(item.vector_query_count for item in result.retrieval_traces)
                if result is not None
                else 0
            ),
            llm_requests=sum(item.request_count for item in result.generation_traces)
            if result is not None
            else 0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_milliseconds=int(result.duration_seconds * 1000) if result is not None else 0,
        ),
        state=output_state,
        created_at=created_at or now,
        updated_at=now,
    )


class ChatbotAnswerer(Protocol):
    def __call__(
        self,
        settings: Settings,
        database: Database,
        *,
        message: str,
        history: Sequence[Mapping[str, str]],
        use_external_sources: bool,
        interaction_mode: str,
        previous_sources: Sequence[ChatbotSource],
        analyze_figures: bool = False,
        on_figure_analysis: Callable[[], None] | None = None,
        on_argo_reserved: Callable[[], None] | None = None,
        on_argo_response: Callable[[], None] | None = None,
        on_progress: Callable[[ChatbotProgressStage], None] | None = None,
        retrieval_checkpoint: ChatRetrievalCheckpoint | None = None,
        on_retrieval_checkpoint: Callable[[ChatRetrievalCheckpoint], None] | None = None,
        experimental_profile: str | None = None,
        answer_effort: AnswerEffort = AnswerEffort.BALANCED,
    ) -> ChatbotResult: ...


@dataclass(slots=True)
class ChatAnswerHandler:
    """Adapt one durable payload to the shared `answer_chatbot` service."""

    settings: Settings
    database: Database
    answer: ChatbotAnswerer = answer_chatbot

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
        """Resume the job trace and conversation before committing a terminal chat result."""

        routing_items, routing_budgets, routing_decisions = _shadow_routing_trace(
            job=job,
            settings=self.settings,
            database=self.database,
        )
        previous_trace = context.repository.trace_manifest(job.id, attempt=job.attempt)
        if previous_trace is None:
            previous_trace = _trace_manifest(
                job=job,
                settings=self.settings,
                history=(),
                interaction_mode=job.payload.interaction_mode,
                state="running",
                result=None,
                now=context.clock(),
                routing_items=routing_items,
                budgets=routing_budgets,
                routing_decisions=routing_decisions,
            )
            context.save_trace_checkpoint(previous_trace)
        elif not routing_items:
            routing_items = previous_trace.routing_items
            routing_budgets = previous_trace.budgets
            routing_decisions = previous_trace.routing_decisions
        context.check_cancellation()
        conversation = self.database.chat_conversation(str(job.conversation_id))
        if conversation is None:
            raise ValueError("job conversation no longer exists")
        evaluation_run_id = getattr(job.payload, "evaluation_run_id", None)
        evaluation_question_id = getattr(job.payload, "evaluation_question_id", None)
        evaluation_profile = getattr(job.payload, "evaluation_profile", None)
        evaluation_question_sha256 = getattr(job.payload, "evaluation_question_sha256", None)
        if evaluation_run_id is not None:
            user_messages = [
                message for message in conversation["messages"] if message["role"] == "user"
            ]
            if (
                len(user_messages) != 1
                or user_messages[0]["id"] != str(job.user_message_id)
                or user_messages[0]["content"] != job.payload.message
            ):
                raise ArgoScientificValidationError(
                    "evaluation question integrity failed before generation",
                    reason=ScientificValidationReason.QUESTION_INTEGRITY,
                )
        history = [
            {"role": message["role"], "content": message["content"]}
            for message in conversation["messages"]
            if message["id"] != str(job.user_message_id)
            and not (
                message["role"] == "assistant"
                and isinstance(message.get("response"), dict)
                and message["response"].get("kind") == "job_terminal_notice"
            )
        ]
        previous_sources = latest_chatbot_sources(conversation["messages"])
        interaction_mode = resolve_chat_interaction_mode(
            job.payload.message,
            history,
            job.payload.interaction_mode,
            has_reusable_sources=bool(previous_sources),
        )
        enrichment_allowed = (
            interaction_mode == "research"
            and job.payload.use_external_sources
            and self.settings.app.allow_bibliographic_apis
            and self.settings.bibliographic.enabled
            and load_local_profile() is LocalProfile.ADMIN
        )
        published_order = JOB_STEP_ORDER[job.step]
        progress_steps: dict[ChatbotProgressStage, JobStep] = {
            "planning": JobStep.PLANNING,
            "search": JobStep.SEARCH,
            "enrichment": JobStep.ENRICHMENT,
            "reranking": JobStep.RERANKING,
            "evidence_selection": JobStep.EVIDENCE_SELECTION,
            "coverage": JobStep.COVERAGE,
            "figure_analysis": JobStep.FIGURE_ANALYSIS,
            "generation": JobStep.GENERATION,
        }

        def publish_progress(stage: ChatbotProgressStage) -> None:
            nonlocal published_order
            context.check_cancellation()
            step = progress_steps[stage]
            step_order = JOB_STEP_ORDER[step]
            if step_order <= published_order:
                return
            context.publish(step)
            published_order = step_order

        def publish_figure_analysis() -> None:
            publish_progress("figure_analysis")

        def check_argo_reservation() -> None:
            context.check_cancellation()

        validation_step_published = False

        def publish_validation_after_response() -> None:
            nonlocal validation_step_published
            if validation_step_published:
                return
            context.check_cancellation()
            context.publish(JobStep.VALIDATION)
            validation_step_published = True

        trace_manifest = _trace_manifest(
            job=job,
            settings=self.settings,
            history=history,
            interaction_mode=interaction_mode,
            state="running",
            result=None,
            now=context.clock(),
            run_id=previous_trace.run_id if previous_trace is not None else None,
            created_at=previous_trace.created_at if previous_trace is not None else None,
            routing_items=routing_items,
            budgets=routing_budgets,
            routing_decisions=routing_decisions,
        )
        context.save_trace_checkpoint(trace_manifest)
        figure_options = (
            {
                "analyze_figures": True,
                "on_figure_analysis": publish_figure_analysis,
            }
            if job.payload.analyze_figures
            else {}
        )
        evaluation_options = (
            {"experimental_profile": evaluation_profile} if evaluation_profile is not None else {}
        )
        answer_parameters = tuple(inspect.signature(self.answer).parameters.values())

        def supports_answer_parameter(name: str) -> bool:
            return any(
                parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in answer_parameters
            )

        effort_options = (
            {"answer_effort": job.payload.answer_effort}
            if supports_answer_parameter("answer_effort")
            else {}
        )
        checkpoint_store = ChatRetrievalCheckpointStore(
            self.settings.paths.cache_dir / "chat_job_checkpoints"
        )
        figure_checkpoint_store = FigureAnalysisCheckpointStore(
            self.settings.paths.cache_dir / "chat_job_checkpoints"
        )
        checkpoint_request = {
            "payload": job.payload.model_dump(
                mode="json",
                exclude={"client_request_id"},
            ),
            "history": history,
            "interaction_mode": interaction_mode,
            "external_enrichment_allowed": enrichment_allowed,
        }
        checkpoint_fingerprint = checkpoint_store.request_fingerprint(checkpoint_request)
        retrieval_checkpoint = checkpoint_store.load(
            job.user_message_id,
            request_fingerprint=checkpoint_fingerprint,
        )
        figure_checkpoint_fingerprint = checkpoint_store.request_fingerprint(
            {
                "payload": job.payload.model_dump(
                    mode="json",
                    exclude={"client_request_id"},
                ),
                "history": history,
                "interaction_mode": interaction_mode,
                "external_enrichment_allowed": enrichment_allowed,
                "checkpoint_kind": "figure_analysis",
            }
        )
        figure_checkpoint = figure_checkpoint_store.load(
            job.user_message_id,
            request_fingerprint=figure_checkpoint_fingerprint,
        )
        if job.payload.analyze_figures and figure_checkpoint is None:
            figure_checkpoint = FigureAnalysisCheckpoint.empty(figure_checkpoint_fingerprint)

        def save_retrieval_checkpoint(checkpoint: ChatRetrievalCheckpoint) -> None:
            try:
                checkpoint_store.save(
                    job.user_message_id,
                    request_fingerprint=checkpoint_fingerprint,
                    checkpoint=checkpoint,
                )
            except OSError:
                LOGGER.warning(
                    "chat_retrieval_checkpoint_write_failed job_id=%s",
                    job.id,
                )

        def save_figure_checkpoint(checkpoint: FigureAnalysisCheckpoint) -> None:
            try:
                figure_checkpoint_store.save(job.user_message_id, checkpoint=checkpoint)
            except OSError:
                LOGGER.warning(
                    "chat_figure_checkpoint_write_failed job_id=%s",
                    job.id,
                )

        checkpoint_options = {}
        if supports_answer_parameter("retrieval_checkpoint"):
            checkpoint_options["retrieval_checkpoint"] = retrieval_checkpoint
        if supports_answer_parameter("on_retrieval_checkpoint"):
            checkpoint_options["on_retrieval_checkpoint"] = save_retrieval_checkpoint
        if supports_answer_parameter("figure_checkpoint"):
            checkpoint_options["figure_checkpoint"] = figure_checkpoint
        if supports_answer_parameter("on_figure_checkpoint"):
            checkpoint_options["on_figure_checkpoint"] = save_figure_checkpoint
        result = self.answer(
            self.settings,
            self.database,
            message=job.payload.message,
            history=history,
            use_external_sources=enrichment_allowed,
            interaction_mode=interaction_mode,
            previous_sources=previous_sources,
            on_argo_reserved=check_argo_reservation,
            on_argo_response=publish_validation_after_response,
            on_progress=publish_progress,
            **figure_options,
            **evaluation_options,
            **effort_options,
            **checkpoint_options,
        )
        if result.message != job.payload.message:
            raise ArgoScientificValidationError(
                "evaluation question integrity failed after generation",
                reason=ScientificValidationReason.QUESTION_INTEGRITY,
            )
        if evaluation_run_id is not None:
            result = result.model_copy(
                update={
                    "evaluation": ChatbotEvaluationTrace(
                        run_id=evaluation_run_id,
                        question_id=evaluation_question_id,
                        profile=evaluation_profile,
                        question_sha256=evaluation_question_sha256,
                    )
                }
            )
        trace_manifest = _trace_manifest(
            job=job,
            settings=self.settings,
            history=history,
            interaction_mode=interaction_mode,
            state="succeeded",
            result=result,
            now=context.clock(),
            run_id=trace_manifest.run_id,
            created_at=trace_manifest.created_at,
            routing_items=routing_items,
            budgets=routing_budgets,
            routing_decisions=routing_decisions,
        )
        return JobHandlerResult(
            assistant_content=result.answer_markdown,
            assistant_response=result.model_dump(mode="json"),
            response_time_milliseconds=result.duration_seconds * 1000,
            trace_manifest=trace_manifest,
        )
