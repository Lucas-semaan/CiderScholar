from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.jobs.contracts import (
    ALLOWED_JOB_TRANSITIONS,
    JOB_ERROR_DISPOSITIONS,
    JOB_RETRY_DELAYS,
    JOB_STEP_LABELS,
    MAX_JOB_ATTEMPTS,
    RESERVED_FUTURE_JOB_TYPES,
    ChatAnswerPayload,
    DeepResearchPayload,
    JobErrorDisposition,
    JobErrorKind,
    JobPublic,
    JobPublicError,
    JobState,
    JobStep,
    JobType,
    LongSynthesisPayload,
    PrivateIngestionPayload,
    can_transition,
    retry_delay_after,
)


def test_job_type_is_closed_and_reserves_future_names() -> None:
    assert tuple(JobType) == (
        JobType.CHAT_ANSWER,
        JobType.WEEKLY_MAINTENANCE,
        JobType.DEEP_RESEARCH,
        JobType.LONG_SYNTHESIS,
        JobType.PRIVATE_INGESTION,
    )
    assert JobType("chat_answer") is JobType.CHAT_ANSWER
    assert JobType("weekly_maintenance") is JobType.WEEKLY_MAINTENANCE
    assert JobType("deep_research") is JobType.DEEP_RESEARCH
    assert JobType("long_synthesis") is JobType.LONG_SYNTHESIS
    assert JobType("private_ingestion") is JobType.PRIVATE_INGESTION
    assert not RESERVED_FUTURE_JOB_TYPES

    with pytest.raises(ValueError):
        JobType("unknown")


def test_job_states_and_transitions_are_closed_and_explicit() -> None:
    assert {state.value for state in JobState} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancel_requested",
        "cancelled",
    }
    assert set(ALLOWED_JOB_TRANSITIONS) == set(JobState)
    assert can_transition(JobState.QUEUED, JobState.RUNNING)
    assert can_transition(JobState.RUNNING, JobState.QUEUED)
    assert can_transition(JobState.RUNNING, JobState.CANCEL_REQUESTED)
    assert can_transition(JobState.CANCEL_REQUESTED, JobState.CANCELLED)

    for terminal_state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED):
        assert not ALLOWED_JOB_TRANSITIONS[terminal_state]
        assert all(not can_transition(terminal_state, target) for target in JobState)


def test_every_job_step_has_a_safe_user_label() -> None:
    assert {step.value for step in JobStep} == {
        "waiting",
        "search",
        "reranking",
        "enrichment",
        "argo",
        "validation",
        "persistence",
        "backup",
        "suggestions",
        "harvest",
        "index",
        "publish",
        "evidence",
        "verification",
        "synthesis",
        "ingestion",
    }
    assert set(JOB_STEP_LABELS) == set(JobStep)
    assert all(label.strip() for label in JOB_STEP_LABELS.values())


def test_background_payloads_are_versioned_and_reject_unsafe_paths() -> None:
    synthesis = LongSynthesisPayload(
        query_id=" query-1 ",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )
    private = PrivateIngestionPayload(
        staged_files=["digest-report.pdf"],
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )

    assert synthesis.query_id == "query-1"
    assert synthesis.version == private.version == 1
    with pytest.raises(ValidationError):
        PrivateIngestionPayload(
            staged_files=["../outside.pdf"],
            conversation_id=uuid4(),
            client_request_id=uuid4(),
        )


def test_job_error_retry_classification_is_explicit() -> None:
    assert set(JOB_ERROR_DISPOSITIONS) == set(JobErrorKind)
    assert JOB_ERROR_DISPOSITIONS[JobErrorKind.TIMEOUT] is JobErrorDisposition.RETRYABLE
    assert JOB_ERROR_DISPOSITIONS[JobErrorKind.QUOTA] is JobErrorDisposition.RETRYABLE
    assert JOB_ERROR_DISPOSITIONS[JobErrorKind.AUTHENTICATION] is JobErrorDisposition.TERMINAL
    assert JOB_ERROR_DISPOSITIONS[JobErrorKind.VALIDATION] is JobErrorDisposition.TERMINAL


def test_job_retry_policy_is_bounded() -> None:
    assert MAX_JOB_ATTEMPTS == 3
    assert (timedelta(seconds=30), timedelta(minutes=2)) == JOB_RETRY_DELAYS
    assert retry_delay_after(1) == timedelta(seconds=30)
    assert retry_delay_after(2) == timedelta(minutes=2)
    assert retry_delay_after(3) is None
    assert retry_delay_after(99) is None

    with pytest.raises(ValueError):
        retry_delay_after(0)


def test_chat_answer_payload_is_versioned_bounded_and_strict() -> None:
    conversation_id = uuid4()
    client_request_id = uuid4()
    payload = ChatAnswerPayload(
        message="  Pourquoi   ce résultat ? ",
        conversation_id=conversation_id,
        client_request_id=client_request_id,
        use_external_sources=True,
        analyze_figures=True,
    )

    assert payload.version == 1
    assert payload.message == "Pourquoi ce résultat ?"
    assert payload.conversation_id == conversation_id
    assert payload.client_request_id == client_request_id
    assert payload.idempotency_key == (conversation_id, client_request_id)
    assert payload.use_external_sources is True
    assert payload.analyze_figures is True
    assert payload.interaction_mode == "auto"

    with pytest.raises(ValidationError):
        ChatAnswerPayload(
            message="x", conversation_id=conversation_id, client_request_id=client_request_id
        )
    with pytest.raises(ValidationError):
        ChatAnswerPayload(
            message="x" * 4001,
            conversation_id=conversation_id,
            client_request_id=client_request_id,
        )
    with pytest.raises(ValidationError):
        ChatAnswerPayload(
            message="question",
            conversation_id=conversation_id,
            client_request_id=client_request_id,
            unknown=True,
        )
    with pytest.raises(ValidationError):
        ChatAnswerPayload(
            message="question",
            conversation_id="not-a-uuid",
            client_request_id=client_request_id,
        )
    with pytest.raises(ValidationError):
        ChatAnswerPayload(
            version=2,
            message="question",
            conversation_id=conversation_id,
            client_request_id=client_request_id,
        )


def test_chat_answer_payload_requires_uuid_client_request_id() -> None:
    with pytest.raises(ValidationError):
        ChatAnswerPayload(message="question", conversation_id=uuid4())
    with pytest.raises(ValidationError):
        ChatAnswerPayload(
            message="question", conversation_id=uuid4(), client_request_id="not-a-uuid"
        )


def test_deep_research_payload_is_strict_versioned_and_idempotent() -> None:
    conversation_id = uuid4()
    request_id = uuid4()
    payload = DeepResearchPayload(
        message="  Analyse   les preuves ",
        conversation_id=conversation_id,
        client_request_id=request_id,
        analyze_figures=True,
    )

    assert payload.message == "Analyse les preuves"
    assert payload.version == 1
    assert payload.analyze_figures is True
    with pytest.raises(ValidationError):
        DeepResearchPayload(
            message="Analyse",
            conversation_id=conversation_id,
            client_request_id=request_id,
            hidden_label="forbidden",
        )


def test_public_job_contract_excludes_internal_payload_and_bounds_errors() -> None:
    now = datetime.now(UTC)
    public_job = JobPublic(
        id=uuid4(),
        conversation_id=uuid4(),
        type=JobType.CHAT_ANSWER,
        state=JobState.QUEUED,
        step=JobStep.WAITING,
        attempt=0,
        available_at=now,
        created_at=now,
        updated_at=now,
        error=JobPublicError(
            code=JobErrorKind.QUOTA,
            message="Service temporairement indisponible",
            retry_at=now + timedelta(minutes=1),
        ),
    )

    serialized = public_job.model_dump(mode="json")
    assert "payload" not in serialized
    assert "client_request_id" not in serialized
    assert "worker_id" not in serialized
    assert serialized["step"] == "waiting"
    assert serialized["error"]["code"] == "quota"

    with pytest.raises(ValidationError):
        JobPublic(**public_job.model_dump(), payload={"message": "secret"})
    with pytest.raises(ValidationError):
        JobPublicError(code=JobErrorKind.TIMEOUT, message="x" * 301)
