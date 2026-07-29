"""Public request and response contracts for the local web application."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.jobs.contracts import JobPublic
from app.memory_profiles import MemoryProfileName
from app.suggestions.models import (
    DoiSuggestionSource,
    ManualSuggestionSource,
    UrlSuggestionSource,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArgoKeyRequest(ApiModel):
    key: str = Field(min_length=1, max_length=4098)


class FolderIngestionRequest(ApiModel):
    folder: str = Field(min_length=1, max_length=2000)
    recursive: bool = True


class IndexRequest(ApiModel):
    retry_failed: bool = False


class ChatJobSubmitRequest(ApiModel):
    message: str = Field(min_length=2, max_length=4000)
    client_request_id: UUID
    use_external_sources: bool = False
    mode: Literal["quick", "deep_research"] = "quick"
    interaction_mode: Literal["auto", "research", "conversation"] = "auto"

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("message must contain at least two characters")
        return cleaned


class PersistedUserMessage(ApiModel):
    id: UUID
    role: Literal["user"] = "user"
    content: str = Field(min_length=1, max_length=4000)
    created_at: datetime


class ChatJobSubmitResponse(ApiModel):
    job: JobPublic
    user_message: PersistedUserMessage


class JobRetryRequest(ApiModel):
    client_request_id: UUID


class ChatConversationCreateRequest(ApiModel):
    title: str = Field(default="Nouvelle conversation", min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return " ".join(value.split())


class ChatConversationRenameRequest(ApiModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return " ".join(value.split())


class ChatFeedbackRequest(ApiModel):
    helpful: bool


class ChatFavoriteRequest(ApiModel):
    favorite: bool


class ChatExportRequest(ApiModel):
    conversation_ids: list[UUID] = Field(min_length=1, max_length=100)
    message_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    format: Literal["markdown", "pdf"]

    @field_validator("conversation_ids", "message_ids")
    @classmethod
    def unique_identifiers(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("export identifiers must be unique")
        return values


class SynthesisRequest(ApiModel):
    resume: bool = True
    client_request_id: UUID = Field(default_factory=uuid4)


class LibraryReviewDecisionRequest(ApiModel):
    decision: Literal["accepted", "rejected"]


class RuntimeSettingsRequest(ApiModel):
    default_article_count: int = Field(ge=1, le=100)
    lexical_weight: float = Field(ge=0.0, le=1.0)
    vector_weight: float = Field(ge=0.0, le=1.0)
    reranker_weight: float = Field(ge=0.0, le=1.0)
    embedding_batch_size: int = Field(ge=1, le=64)
    passages_per_article: int = Field(ge=1, le=8)


class PublisherCredentialRequest(ApiModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)
    authorization_confirmed: Literal[True]


class ConfirmedCorpusAction(ApiModel):
    confirmed: Literal[True]


class ConfirmedAdminAction(ApiModel):
    confirmed: Literal[True]


class ConfirmedDesktopAction(ApiModel):
    confirmed: Literal[True]


class SynchronizedRootRequest(ApiModel):
    path: Path
    confirm_unexpected_name: bool = False


class MemoryProfileRequest(ApiModel):
    profile: MemoryProfileName


class SuggestionReferenceRequest(ApiModel):
    source: Annotated[
        DoiSuggestionSource | UrlSuggestionSource | ManualSuggestionSource,
        Field(discriminator="kind"),
    ]
    scientific_comment: str | None = Field(default=None, max_length=1500)


class PublisherCollectionRequest(ApiModel):
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    targets: list[str] = Field(min_length=1, max_length=1000)
    authorization_reference: str = Field(min_length=3, max_length=500)
    authorization_confirmed: Literal[True]

    @field_validator("targets")
    @classmethod
    def clean_targets(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("at least one DOI or record id is required")
        return list(dict.fromkeys(cleaned))
