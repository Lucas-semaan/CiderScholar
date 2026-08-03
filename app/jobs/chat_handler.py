"""Durable chat handler delegating to the existing scientific workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings
from app.corpora import LocalProfile, load_local_profile
from app.database.sqlite import Database
from app.jobs.contracts import JobStep
from app.jobs.repository import JobRecord
from app.jobs.worker import JobHandlerResult, JobProgressContext
from app.models.chatbot import ChatbotResult, ChatbotSource
from app.services.chatbot import latest_chatbot_sources, resolve_chat_interaction_mode
from app.services.workflows import answer_chatbot


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
        history = [
            {"role": message["role"], "content": message["content"]}
            for message in conversation["messages"]
            if message["id"] != str(job.user_message_id)
        ]
        previous_sources = latest_chatbot_sources(conversation["messages"])
        interaction_mode = resolve_chat_interaction_mode(
            job.payload.message,
            history,
            job.payload.interaction_mode,
            has_reusable_sources=bool(previous_sources),
        )
        if interaction_mode == "research":
            context.publish(JobStep.SEARCH)
        enrichment_allowed = (
            interaction_mode == "research"
            and job.payload.use_external_sources
            and self.settings.app.allow_bibliographic_apis
            and self.settings.bibliographic.enabled
            and load_local_profile() is LocalProfile.ADMIN
        )
        if enrichment_allowed:
            context.publish(JobStep.ENRICHMENT)
        figure_step_published = enrichment_allowed

        def publish_figure_analysis() -> None:
            nonlocal figure_step_published
            if figure_step_published:
                return
            context.check_cancellation()
            context.publish(JobStep.ENRICHMENT)
            figure_step_published = True

        argo_step_published = False

        def publish_argo_after_reservation() -> None:
            nonlocal argo_step_published
            if argo_step_published:
                return
            context.check_cancellation()
            context.publish(JobStep.ARGO)
            argo_step_published = True

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
        result = self.answer(
            self.settings,
            self.database,
            message=job.payload.message,
            history=history,
            use_external_sources=enrichment_allowed,
            interaction_mode=interaction_mode,
            previous_sources=previous_sources,
            on_argo_reserved=publish_argo_after_reservation,
            on_argo_response=publish_validation_after_response,
            **figure_options,
        )
        return JobHandlerResult(
            assistant_content=result.answer_markdown,
            assistant_response=result.model_dump(mode="json"),
            response_time_milliseconds=result.duration_seconds * 1000,
        )
