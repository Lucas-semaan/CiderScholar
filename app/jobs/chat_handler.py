"""Durable chat handler delegating to the existing scientific workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings
from app.corpora import LocalProfile, load_local_profile
from app.database.sqlite import Database
from app.jobs.contracts import JOB_STEP_ORDER, JobStep
from app.jobs.repository import JobRecord
from app.jobs.worker import JobHandlerResult, JobProgressContext
from app.llm.argo_client import ArgoScientificValidationError, ScientificValidationReason
from app.models.chatbot import ChatbotEvaluationTrace, ChatbotResult, ChatbotSource
from app.services.chatbot import latest_chatbot_sources, resolve_chat_interaction_mode
from app.services.workflows import ChatbotProgressStage, answer_chatbot


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
        experimental_profile: str | None = None,
    ) -> ChatbotResult: ...


@dataclass(slots=True)
class ChatAnswerHandler:
    """Adapt one durable payload to the shared `answer_chatbot` service."""

    settings: Settings
    database: Database
    answer: ChatbotAnswerer = answer_chatbot

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
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

        context.check_cancellation()
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
        return JobHandlerResult(
            assistant_content=result.answer_markdown,
            assistant_response=result.model_dump(mode="json"),
            response_time_milliseconds=result.duration_seconds * 1000,
        )
