"""Closed contracts shared by durable-job producers, workers, and APIs."""

from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobType(StrEnum):
    """Job types accepted by the current application and database schema."""

    CHAT_ANSWER = "chat_answer"
    WEEKLY_MAINTENANCE = "weekly_maintenance"
    DEEP_RESEARCH = "deep_research"
    LONG_SYNTHESIS = "long_synthesis"
    PRIVATE_INGESTION = "private_ingestion"


# These names are documented and unavailable until their own roadmap task adds
# them to JobType and to the matching SQLite constraint.
RESERVED_FUTURE_JOB_TYPES: frozenset[str] = frozenset()


class JobState(StrEnum):
    """Persisted lifecycle states for a durable job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


ALLOWED_JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset(
        {
            JobState.QUEUED,
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCEL_REQUESTED,
        }
    ),
    JobState.CANCEL_REQUESTED: frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}),
    JobState.SUCCEEDED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
}

ACTIVE_JOB_STATES = frozenset({JobState.QUEUED, JobState.RUNNING, JobState.CANCEL_REQUESTED})


def can_transition(current: JobState, target: JobState) -> bool:
    """Return whether a persisted lifecycle transition is permitted."""

    return target in ALLOWED_JOB_TRANSITIONS[current]


class JobStep(StrEnum):
    """Safe progress steps exposed to users."""

    WAITING = "waiting"
    BACKUP = "backup"
    SUGGESTIONS = "suggestions"
    HARVEST = "harvest"
    INDEX = "index"
    SEARCH = "search"
    RERANKING = "reranking"
    ENRICHMENT = "enrichment"
    ARGO = "argo"
    VALIDATION = "validation"
    PUBLISH = "publish"
    PERSISTENCE = "persistence"
    EVIDENCE = "evidence"
    VERIFICATION = "verification"
    SYNTHESIS = "synthesis"
    INGESTION = "ingestion"


JOB_STEP_LABELS: dict[JobStep, str] = {
    JobStep.WAITING: "En attente",
    JobStep.SEARCH: "Recherche des sources",
    JobStep.RERANKING: "Reranking des passages",
    JobStep.ENRICHMENT: "Enrichissement des références",
    JobStep.ARGO: "Génération de la réponse",
    JobStep.VALIDATION: "Validation scientifique",
    JobStep.PERSISTENCE: "Enregistrement du résultat",
    JobStep.BACKUP: "Sauvegarde du corpus",
    JobStep.SUGGESTIONS: "Import des suggestions",
    JobStep.HARVEST: "Collecte bibliographique",
    JobStep.INDEX: "Indexation et contrôles",
    JobStep.PUBLISH: "Publication du corpus",
    JobStep.EVIDENCE: "Extraction des preuves",
    JobStep.VERIFICATION: "Vérification des affirmations",
    JobStep.SYNTHESIS: "Synthèse approfondie",
}

JOB_STEP_LABELS[JobStep.INGESTION] = "Ingestion des documents privés"

JOB_STEP_ORDER: dict[JobStep, int] = {step: index for index, step in enumerate(JobStep)}


class JobErrorKind(StrEnum):
    """Stable technical error categories safe to persist and expose."""

    TIMEOUT = "timeout"
    QUOTA = "quota"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"


class JobErrorDisposition(StrEnum):
    """Whether a failed attempt may be retried automatically."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"


JOB_ERROR_DISPOSITIONS: dict[JobErrorKind, JobErrorDisposition] = {
    JobErrorKind.TIMEOUT: JobErrorDisposition.RETRYABLE,
    JobErrorKind.QUOTA: JobErrorDisposition.RETRYABLE,
    JobErrorKind.AUTHENTICATION: JobErrorDisposition.TERMINAL,
    JobErrorKind.VALIDATION: JobErrorDisposition.TERMINAL,
}


MAX_JOB_ATTEMPTS = 3
JOB_RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2))


def retry_delay_after(attempt: int) -> timedelta | None:
    """Return the delay after a failed 1-based attempt, or None when exhausted."""

    if attempt < 1:
        raise ValueError("attempt must be at least one")
    if attempt >= MAX_JOB_ATTEMPTS:
        return None
    return JOB_RETRY_DELAYS[attempt - 1]


class ChatAnswerPayload(BaseModel):
    """Versioned internal input for a durable chat-answer job."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    message: str = Field(min_length=2, max_length=4000)
    conversation_id: UUID
    client_request_id: UUID
    use_external_sources: bool = False
    analyze_figures: bool = False
    interaction_mode: Literal["auto", "research", "conversation"] = "auto"

    @property
    def idempotency_key(self) -> tuple[UUID, UUID]:
        """Stable conversation-scoped key used by enqueue persistence."""

        return (self.conversation_id, self.client_request_id)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("message must contain at least two characters")
        return cleaned


class WeeklyMaintenancePayload(BaseModel):
    """Versioned internal input for one administrator maintenance cycle."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    maintenance_id: UUID
    conversation_id: UUID
    client_request_id: UUID
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("maintenance request time must be timezone-aware")
        return value


class DeepResearchPayload(BaseModel):
    """Versioned input for one resumable full-text analysis."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    message: str = Field(min_length=2, max_length=4000)
    conversation_id: UUID
    client_request_id: UUID
    analyze_figures: bool = False

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("message must contain at least two characters")
        return cleaned


class LongSynthesisPayload(BaseModel):
    """Versioned input for one resumable hierarchical synthesis."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    query_id: str = Field(min_length=1, max_length=200)
    resume: bool = True
    conversation_id: UUID
    client_request_id: UUID

    @field_validator("query_id")
    @classmethod
    def clean_query_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query_id cannot be blank")
        return cleaned


class PrivateIngestionPayload(BaseModel):
    """Versioned references to PDFs already staged in the private local directory."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    staged_files: list[str] = Field(min_length=1, max_length=100)
    conversation_id: UUID
    client_request_id: UUID

    @field_validator("staged_files")
    @classmethod
    def validate_staged_files(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            path = PurePosixPath(item)
            if (
                not item
                or len(item) > 500
                or "\\" in item
                or path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
                or path.suffix.casefold() != ".pdf"
            ):
                raise ValueError("staged_files must contain safe relative PDF paths")
            cleaned.append(item)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("staged_files cannot contain duplicates")
        return cleaned


JobPayload = (
    ChatAnswerPayload
    | WeeklyMaintenancePayload
    | DeepResearchPayload
    | LongSynthesisPayload
    | PrivateIngestionPayload
)


class JobPublicError(BaseModel):
    """Bounded, non-sensitive error information exposed by the public API."""

    model_config = ConfigDict(extra="forbid")

    code: JobErrorKind
    message: str = Field(min_length=1, max_length=300)
    retry_at: datetime | None = None


class JobPublic(BaseModel):
    """Public job projection; deliberately excludes the internal payload."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    conversation_id: UUID
    type: JobType
    state: JobState
    step: JobStep
    attempt: int = Field(ge=0, le=MAX_JOB_ATTEMPTS)
    available_at: datetime
    created_at: datetime
    updated_at: datetime
    result_message_id: UUID | None = None
    error: JobPublicError | None = None
