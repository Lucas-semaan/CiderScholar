"""Closed contracts shared by durable-job producers, workers, and APIs."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.chat_effort import AnswerEffort, migrate_legacy_answer_effort
from app.expert_feedback.models import CandidatePatch


class JobType(StrEnum):
    """Job types accepted by the current application and database schema."""

    CHAT_ANSWER = "chat_answer"
    WEEKLY_MAINTENANCE = "weekly_maintenance"
    BIBLIOGRAPHIC_WATCH = "bibliographic_watch"
    DEEP_RESEARCH = "deep_research"
    LONG_SYNTHESIS = "long_synthesis"
    CORPUS_INGESTION = "corpus_ingestion"
    EXPERT_IMPROVEMENT = "expert_improvement"


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
    PLANNING = "planning"
    BACKUP = "backup"
    # Compatibility only: old persisted maintenance jobs may still contain this value.
    SUGGESTIONS = "suggestions"
    HARVEST = "harvest"
    INDEX = "index"
    SEARCH = "search"
    ENRICHMENT = "enrichment"
    RERANKING = "reranking"
    EVIDENCE_SELECTION = "evidence_selection"
    COVERAGE = "coverage"
    FIGURE_ANALYSIS = "figure_analysis"
    GENERATION = "generation"
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
    JobStep.PLANNING: "Analyse et planification de la question",
    JobStep.SEARCH: "Recherche locale dans le corpus",
    JobStep.ENRICHMENT: "Enrichissement des références",
    JobStep.RERANKING: "Classement et fusion des passages",
    JobStep.EVIDENCE_SELECTION: "Sélection sémantique des preuves",
    JobStep.COVERAGE: "Contrôle et complément de la couverture",
    JobStep.FIGURE_ANALYSIS: "Analyse locale des figures",
    JobStep.GENERATION: "Génération de la réponse finale",
    JobStep.ARGO: "Traitement ARGO (ancien suivi)",
    JobStep.VALIDATION: "Validation scientifique",
    JobStep.PERSISTENCE: "Enregistrement du résultat",
    JobStep.BACKUP: "Sauvegarde du corpus",
    JobStep.SUGGESTIONS: "Étape historique retirée",
    JobStep.HARVEST: "Collecte bibliographique",
    JobStep.INDEX: "Indexation et contrôles",
    JobStep.PUBLISH: "Publication du corpus",
    JobStep.EVIDENCE: "Extraction des preuves",
    JobStep.VERIFICATION: "Vérification des affirmations",
    JobStep.SYNTHESIS: "Synthèse approfondie",
}

JOB_STEP_LABELS[JobStep.INGESTION] = "Ingestion des documents"

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


class ExpertMemoryPin(BaseModel):
    """Internal immutable identity of the expert-memory release used by a job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    mode: Literal["off", "shadow", "active"] = "off"
    release_id: UUID | None = None
    release_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    recipe_version: str | None = Field(default=None, max_length=80)
    recipe_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def coherent_release_identity(self) -> ExpertMemoryPin:
        if (self.release_id is None) != (self.release_sha256 is None):
            raise ValueError("expert memory release identity must include ID and hash")
        if self.mode == "off" and any(
            value is not None
            for value in (
                self.release_id,
                self.release_sha256,
                self.recipe_version,
                self.recipe_sha256,
            )
        ):
            raise ValueError("off expert memory jobs cannot carry a release pin")
        if (self.recipe_version is None) != (self.recipe_sha256 is None):
            raise ValueError("expert memory recipe identity must include version and hash")
        if self.mode != "off" and self.release_id is None:
            raise ValueError("shadow and active expert memory jobs require a release pin")
        return self


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
    answer_effort: AnswerEffort = AnswerEffort.BALANCED
    expert_memory_pin: ExpertMemoryPin = Field(default_factory=ExpertMemoryPin)
    evaluation_run_id: str | None = Field(
        default=None,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$",
    )
    evaluation_question_id: str | None = Field(
        default=None,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$",
    )
    evaluation_profile: Literal["p0", "p1", "p2"] | None = None
    evaluation_question_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    orchestrator_parent_job_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_answer_intensity(cls, values: object) -> object:
        """Read queued version-1 jobs written before ``answer_effort`` existed."""

        return migrate_legacy_answer_effort(values)

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

    @model_validator(mode="after")
    def validate_evaluation_cell(self) -> ChatAnswerPayload:
        metadata = (
            self.evaluation_run_id,
            self.evaluation_question_id,
            self.evaluation_profile,
        )
        if not any(metadata):
            if self.evaluation_question_sha256 is not None:
                raise ValueError("an evaluation fingerprint requires complete evaluation metadata")
            if self.orchestrator_parent_job_id is not None:
                raise ValueError("an orchestrator parent requires complete evaluation metadata")
            return self
        if not all(metadata):
            raise ValueError("evaluation run, question and profile must be supplied together")
        if self.interaction_mode != "research":
            raise ValueError("evaluation questions require the isolated research interaction mode")
        if self.use_external_sources:
            raise ValueError("evaluation questions cannot use external sources")
        expected = sha256(self.message.encode("utf-8")).hexdigest()
        if self.evaluation_question_sha256 is None:
            self.evaluation_question_sha256 = expected
        elif self.evaluation_question_sha256 != expected:
            raise ValueError(
                "evaluation question fingerprint does not match the normalized message"
            )
        return self


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


class BibliographicWatchPayload(WeeklyMaintenancePayload):
    """References for an additive administrator watch run; no secrets or query text."""


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


class CorpusIngestionPayload(BaseModel):
    """Versioned references to PDFs already staged in the corpus directory."""

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


class ExpertImprovementPayload(BaseModel):
    """Versioned private input for diagnosis, compilation, evaluation, or orchestration."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    operation: Literal["diagnose", "compile", "evaluate", "orchestrate"] = "diagnose"
    correction_id: UUID | None = None
    expected_revision: int | None = Field(default=None, strict=True, ge=1)
    max_llm_requests: int = Field(default=0, strict=True, ge=0, le=0)
    conversation_id: UUID
    client_request_id: UUID
    diagnosis_id: UUID | None = None
    candidate_patch: CandidatePatch | None = None
    evaluation_id: UUID | None = None
    base_state_path: str | None = Field(default=None, min_length=1, max_length=400)
    candidate_state_path: str | None = Field(default=None, min_length=1, max_length=400)
    campaign_pair_path: str | None = Field(default=None, min_length=1, max_length=400)

    @model_validator(mode="after")
    def exact_operation_shape(self) -> ExpertImprovementPayload:
        if self.operation in {"diagnose", "compile"} and (
            self.correction_id is None or self.expected_revision is None
        ):
            raise ValueError("diagnosis and compilation jobs require correction identity")
        if self.operation == "compile" and (
            self.diagnosis_id is None or self.candidate_patch is None
        ):
            raise ValueError("candidate compilation requires diagnosis_id and candidate_patch")
        if self.operation == "diagnose" and (
            self.diagnosis_id is not None or self.candidate_patch is not None
        ):
            raise ValueError("diagnosis jobs cannot carry candidate compilation data")
        if self.operation == "evaluate" and (
            self.evaluation_id is None
            or self.base_state_path is None
            or self.candidate_state_path is None
            or self.correction_id is not None
            or self.expected_revision is not None
            or self.diagnosis_id is not None
            or self.candidate_patch is not None
            or self.campaign_pair_path is not None
        ):
            raise ValueError("evaluation jobs require only evaluation and campaign identities")
        if self.operation == "orchestrate" and (
            self.evaluation_id is None
            or self.campaign_pair_path is None
            or self.correction_id is not None
            or self.expected_revision is not None
            or self.diagnosis_id is not None
            or self.candidate_patch is not None
            or self.base_state_path is not None
            or self.candidate_state_path is not None
        ):
            raise ValueError("orchestration jobs require only evaluation and campaign identity")
        if self.operation not in {"evaluate", "orchestrate"} and (
            self.evaluation_id is not None
            or self.base_state_path is not None
            or self.candidate_state_path is not None
            or self.campaign_pair_path is not None
        ):
            raise ValueError("evaluation campaign data is only valid for evaluation jobs")
        return self


JobPayload = (
    ChatAnswerPayload
    | WeeklyMaintenancePayload
    | BibliographicWatchPayload
    | DeepResearchPayload
    | LongSynthesisPayload
    | CorpusIngestionPayload
    | ExpertImprovementPayload
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
